"""Sequential interleaving of 10 sessions on one SQLite DB; not a concurrency load test."""
import argparse
import json
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    from db import database, chat_memory, task_state, preference_memory
    from evals.task_benchmark import load_streaming_agent, ScriptedModel
    from evals.real_text_baseline import _seed_sales
    agent = load_streaming_agent()
    from web import agent_chat
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    app = FastAPI()
    app.include_router(agent_chat.router)
    report = {'sessions': 10, 'rounds': 6, 'mode': 'offline sequential interleaving',
              'limits': ['Scripted model actions; measures persistence/isolation not model quality',
                         'No network/OCR or actual concurrent requests; no throughput claim'], 'cases': []}
    previous = database._DATA_DIR, database.DB_PATH
    try:
        with tempfile.TemporaryDirectory(prefix='agent_session_soak_') as tmp, TestClient(app) as client:
            database._DATA_DIR, database.DB_PATH = Path(tmp), Path(tmp)/'agent.db'
            _seed_sales()

            def post(body):
                response = client.post('/api/agent/chat', json=body)
                response.raise_for_status()
                return [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith('data: ')]

            sessions = []
            for i in range(10):
                events = post({'message': f'记住：隔离标记PROFILE-{i}。'})
                assert '已记住' in events[-1]['answer']
                sessions.append(events[0])
            assert len({s['session_id'] for s in sessions}) == 10
            assert len({s['profile_id'] for s in sessions}) == 10
            steps = [('2024', '甲公司', 'desc', True), ('2025', '乙公司', 'desc', True),
                     ('2025', '乙公司', 'invalid', False), ('2025', '甲公司', 'desc', False),
                     ('2024', '', 'desc', True), ('2024', '甲公司', 'desc', True)]
            expected_states = [{} for _ in sessions]
            for round_index, step in enumerate(steps):
                for i, session in enumerate(sessions):
                    year, keyword, group, successful = step
                    # Distinct simultaneous expected states expose accidental cross-session reuse.
                    if i % 2:
                        if round_index in (0, 5):
                            year, keyword = '2025', '乙公司'
                        elif round_index == 1:
                            year, keyword = '2024', '甲公司'
                        elif round_index == 4:
                            year = '2025'
                    sid = session['session_id']
                    prior = task_state.load(sid)
                    history = chat_memory.load_messages(sid, 10)
                    model = ScriptedModel([[{'name': 'rag_summarize', 'args': {
                        'year': year, 'keyword': keyword, 'group_by': group}, 'id': 'query'}]])
                    with patch.object(agent, 'get_llm', return_value=model):
                        events = post({'message': f'SESSION-{i}: 查询{year}年{keyword}，轮次{round_index}',
                                       'session_id': sid, 'profile_id': session['profile_id']})
                    if successful:
                        expected_states[i] = {'tool': 'rag_summarize', 'year': year,
                                              'keyword': keyword, 'group_by': group}
                    system = str(model.messages[0][0].content)
                    received_history = [str(m.content) for m in model.messages[0][1:-1]]
                    checks = {
                        'session_id_preserved': events[0]['session_id'] == sid,
                        'profile_id_preserved': events[0]['profile_id'] == session['profile_id'],
                        'history_exact': received_history == [m['content'] for m in history],
                        'own_preference_delivered': f'隔离标记PROFILE-{i}。' in system,
                        'other_preferences_absent': all(f'隔离标记PROFILE-{j}。' not in system for j in range(10) if j != i),
                        'prior_state_delivered': not prior or json.dumps(prior, ensure_ascii=False) in system,
                        'expected_state': task_state.load(sid) == expected_states[i],
                        'all_sessions_state_isolated': all(task_state.load(s['session_id']) == expected_states[j]
                                                           for j, s in enumerate(sessions)),
                        'no_runtime_error': not any(e['type'] == 'error' for e in events),
                        'turn_persisted': len(chat_memory.load_messages(sid, 100)) == 2*(round_index+1),
                        'preference_unchanged': preference_memory.list_preferences(session['profile_id']) == [f'隔离标记PROFILE-{i}。'],
                    }
                    report['cases'].append({'session_index': i, 'round': round_index+1,
                        'checks': checks, 'passed': all(checks.values())})
            report['passed'] = sum(c['passed'] for c in report['cases'])
            report['total'] = len(report['cases'])
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
            print(json.dumps({k:v for k,v in report.items() if k != 'cases'}, ensure_ascii=False), flush=True)
    finally:
        database._DATA_DIR, database.DB_PATH = previous


if __name__ == '__main__':
    main()
