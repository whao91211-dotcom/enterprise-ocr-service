"""Real uvicorn HTTP concurrency and bounded soak; model responses are scripted."""
import argparse
import concurrent.futures
import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
from contextlib import closing
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def serve(directory, port):
    from db import database
    database._DATA_DIR, database.DB_PATH = directory, directory/'agent.db'
    from evals.real_text_baseline import _seed_sales
    _seed_sales()
    from evals.task_benchmark import load_streaming_agent
    agent = load_streaming_agent()
    from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

    class Model:
        def bind_tools(self, tools):
            return self

        def invoke(self, messages):
            humans = [json.loads(m.content) for m in messages if isinstance(m, HumanMessage)]
            current = humans[-1]
            if isinstance(messages[-1], ToolMessage):
                result = json.loads(messages[-1].content)
                return AIMessage(content=json.dumps({'request_id': current['request_id'],
                    'history_ids': [m['request_id'] for m in humans[:-1]], 'summary': result['summary']}))
            (directory/('started_'+current['request_id'])).write_text(str(time.perf_counter_ns()), encoding='ascii')
            time.sleep(current.get('delay', .001))
            (directory/('finished_'+current['request_id'])).write_text(str(time.perf_counter_ns()), encoding='ascii')
            return AIMessage(content='', tool_calls=[{'name': 'rag_summarize', 'id': current['request_id'],
                'args': {'group_by': 'desc', 'year': current['year'], 'keyword': current['keyword']}}])

    agent.get_llm = Model
    from web import agent_chat
    from fastapi import FastAPI
    app = FastAPI()
    app.include_router(agent_chat.router)

    @app.get('/health')
    async def health():
        try:
            import psutil
            rss = psutil.Process().memory_info().rss
        except ImportError:
            rss = None
        return {'ok': True, 'threads': threading.active_count(), 'rss_bytes': rss}

    import uvicorn
    uvicorn.run(app, host='127.0.0.1', port=port, log_level='error')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--serve', type=Path)
    parser.add_argument('--port', type=int)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--soak-requests', type=int, default=500)
    args = parser.parse_args()
    if args.serve:
        serve(args.serve, args.port)
        return
    if not args.output or not 1 <= args.soak_requests <= 2000:
        parser.error('Provide --output and 1..2000 soak requests')
    import requests
    local_clients = threading.local()

    def http():
        if not hasattr(local_clients, 'client'):
            local_clients.client = requests.Session()
            local_clients.client.trust_env = False
        return local_clients.client
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    base = f'http://127.0.0.1:{port}'
    report = {'mode': 'actual uvicorn, scripted model + real tools/SQLite', 'cases': [],
              'limits': ['Offline model stimuli; not real provider latency or throughput',
                         'Bounded same-process local test; not long-term uptime proof']}
    with tempfile.TemporaryDirectory(prefix='agent_http_probe_') as tmp:
        directory = Path(tmp)
        with (directory/'server.log').open('w', encoding='utf-8') as log:
            process = subprocess.Popen([sys.executable, __file__, '--serve', tmp, '--port', str(port)],
                stdout=log, stderr=log, creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
            try:
                deadline = time.monotonic()+90
                while True:
                    if process.poll() is not None:
                        raise RuntimeError('Diagnostic server exited during startup')
                    try:
                        if http().get(base+'/health', timeout=.5).status_code == 200:
                            break
                    except requests.RequestException:
                        pass
                    if time.monotonic() > deadline:
                        raise RuntimeError('Diagnostic server startup exceeded 90 seconds')
                    time.sleep(.1)

                def post(ident, sid=None, year='2024', keyword='甲公司', delay=.001):
                    response = http().post(base+'/api/agent/chat', json={'session_id': sid,
                        'message': json.dumps({'request_id': ident, 'year': year, 'keyword': keyword, 'delay': delay}, ensure_ascii=False)}, timeout=15)
                    response.raise_for_status()
                    return [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith('data: ')]

                def record(ident, checks, evidence):
                    report['cases'].append({'id': ident, 'checks': checks,
                                            'passed': all(checks.values()), 'evidence': evidence})
                    args.output.parent.mkdir(parents=True, exist_ok=True)
                    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
                    print(json.dumps({'id': ident, 'checks': checks}, ensure_ascii=False), flush=True)

                with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
                    pending = pool.submit(post, 'responsive', None, '2024', '甲公司', .4)
                    start_deadline = time.monotonic()+10
                    while not (directory/'started_responsive').exists():
                        if time.monotonic() > start_deadline:
                            raise RuntimeError('Model stimulus never started')
                        time.sleep(.005)
                    started = time.perf_counter()
                    health = http().get(base+'/health', timeout=5)
                    latency = time.perf_counter()-started
                    events = pending.result()
                    record('health_during_model_wait', {'health_responds_under_100ms': latency < .1,
                        'chat_completed': any(e['type'] == 'answer' for e in events)}, {'health_seconds': latency})

                    seeds = [post(f'seed-{i}') for i in range(4)]
                    ids = [e[0]['session_id'] for e in seeds]
                    started = time.perf_counter()
                    futures = [pool.submit(post, f'parallel-{i}', ids[i], '2024' if i % 2 == 0 else '2025',
                                           '甲公司' if i % 2 == 0 else '乙公司', .2) for i in range(4)]
                    outputs = [f.result() for f in futures]
                    elapsed = time.perf_counter()-started
                    answers = [next((json.loads(e['answer']) for e in output if e['type'] == 'answer' and e['answer'].startswith('{')), {}) for output in outputs]
                    intervals = [(int((directory/f'started_parallel-{i}').read_text()),
                                  int((directory/f'finished_parallel-{i}').read_text())) for i in range(4)]
                    maximum_overlap = max(sum(start <= point < finish for start, finish in intervals)
                                          for point, _ in intervals)
                    record('four_distinct_sessions', {'all_answers': all(answers),
                        'sessions_isolated': all(a.get('history_ids') == [f'seed-{i}'] for i, a in enumerate(answers)),
                        'correct_totals': all(a.get('summary', {}).get('total_sum') == (100 if i % 2 == 0 else 900) for i, a in enumerate(answers)),
                        'requests_overlap': maximum_overlap >= 2}, {'elapsed_seconds': elapsed,
                        'maximum_overlapping_model_waits': maximum_overlap,
                        'model_wait_intervals_ns': intervals, 'answers': answers})

                    seed = post('same-seed')
                    sid = seed[0]['session_id']
                    futures = [pool.submit(post, f'same-{i}', sid, '2024' if i == 0 else '2025',
                                           '甲公司' if i == 0 else '乙公司', .3) for i in range(2)]
                    outputs = [f.result() for f in futures]
                    completed = sum(any(e['type'] == 'answer' for e in output) for output in outputs)
                    busy = sum(any(e['type'] == 'error' and e.get('code') == 'session_busy' for e in output) for output in outputs)
                    saved = http().get(base+f'/api/agent/sessions/{sid}', timeout=5).json()['messages']
                    record('same_session_competition', {'one_request_completed': completed == 1,
                        'one_request_rejected_busy': busy == 1, 'only_completed_turn_saved': len(saved) == 4},
                        {'completed': completed, 'busy': busy, 'saved_messages': len(saved), 'outputs': outputs})

                seed = post('disconnect-seed')
                disconnect_sid = seed[0]['session_id']
                stream = http().post(base+'/api/agent/chat', json={'session_id': disconnect_sid,
                    'message': json.dumps({'request_id': 'disconnect', 'year': '2024',
                        'keyword': '甲公司', 'delay': .4})}, stream=True, timeout=15)
                deadline = time.monotonic()+10
                while not (directory/'started_disconnect').exists():
                    if time.monotonic() > deadline:
                        raise RuntimeError('Disconnect stimulus never started')
                    time.sleep(.005)
                stream.close()
                # Closing a client stream does not cancel an in-flight model or tool.
                time.sleep(.7)
                retry = post('disconnect-retry', disconnect_sid)
                answer = next((json.loads(e['answer']) for e in retry
                    if e['type'] == 'answer' and e['answer'].startswith('{')), {})
                record('client_disconnect_releases_session', {
                    'retry_completed': answer.get('request_id') == 'disconnect-retry',
                    'session_not_stuck_busy': not any(e.get('code') == 'session_busy' for e in retry)},
                    {'retry_events': retry, 'limit': 'Checks gate release, not cancellation of underlying work'})

                sid = None
                history_ids = []
                failures = []
                samples = []
                before = http().get(base+'/health', timeout=5).json()
                started = time.perf_counter()
                for index in range(args.soak_requests):
                    events = post(f'soak-{index}', sid, '2024' if index % 2 == 0 else '2025',
                                  '甲公司' if index % 2 == 0 else '乙公司')
                    sid = events[0]['session_id']
                    answer = next((json.loads(e['answer']) for e in events if e['type'] == 'answer' and e['answer'].startswith('{')), {})
                    correct = (answer.get('request_id') == f'soak-{index}' and
                        answer.get('history_ids') == history_ids[-5:] and
                        answer.get('summary', {}).get('total_sum') == (100 if index % 2 == 0 else 900) and
                        not any(e['type'] == 'error' for e in events))
                    if not correct:
                        failures.append({'index': index, 'events': events, 'expected_history': history_ids[-5:]})
                    history_ids.append(f'soak-{index}')
                    if (index+1) % 100 == 0:
                        samples.append({'requests': index+1, **http().get(base+'/health', timeout=5).json()})
                import sqlite3
                with closing(sqlite3.connect(directory/'agent.db')) as conn:
                    count = conn.execute('SELECT count(*) FROM chat_messages WHERE session_id=?', (sid,)).fetchone()[0]
                    integrity = conn.execute('PRAGMA integrity_check').fetchone()[0]
                after = http().get(base+'/health', timeout=5).json()
                record('bounded_soak', {'all_history_and_totals_correct': not failures,
                    'all_turns_persisted': count == 2*args.soak_requests, 'sqlite_integrity': integrity == 'ok'},
                    {'requests': args.soak_requests, 'failures': failures, 'saved_message_count': count,
                     'elapsed_seconds': time.perf_counter()-started, 'resources_before': before,
                     'resources_after': after, 'resource_samples': samples})
            finally:
                process.terminate()
                process.wait(timeout=10)


if __name__ == '__main__':
    main()
