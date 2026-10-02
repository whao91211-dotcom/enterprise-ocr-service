"""Live multi-turn HTTP diagnostics after a no-match query, on synthetic rows."""
import argparse
import json
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from evals.context_preference_probe import assess


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    from db import database, task_state
    from evals.task_benchmark import load_streaming_agent
    from evals.real_text_baseline import _seed_sales
    agent = load_streaming_agent()
    from web import agent_chat
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from langchain_deepseek import ChatDeepSeek
    import config
    app = FastAPI()
    app.include_router(agent_chat.router)
    factory = lambda: ChatDeepSeek(model=config.DEEPSEEK_MODEL,
        api_key=config.require_deepseek_key(), temperature=.3, timeout=20, max_retries=0)
    initial = [
        ('统计2024年甲公司的销售额，按公司分组。',
         {'filters': {'year': '2024', 'keyword': '甲公司', 'group_by': 'desc'}, 'total': '100'}),
        ('继续甲公司的查询，只把年份改为2025年，按公司分组。',
         {'filters': {'year': '2025', 'keyword': '甲公司', 'group_by': 'desc'}, 'total': None}),
    ]
    continuations = [
        ('change_company', '那乙公司呢？',
         {'filters': {'year': '2025', 'keyword': '乙公司'}, 'total': '900'}),
        ('change_year', '那只把年份改回2024年呢？',
         {'filters': {'year': '2024', 'keyword': '甲公司'}, 'total': '100'}),
        ('repeat_no_match', '刚才这个查询的销售额是多少？不要更换年份或公司。', None),
    ]
    report = {'model': config.DEEPSEEK_MODEL, 'dataset': '3 synthetic rows: 2024甲100, 2024乙200, 2025乙900',
              'execution': 'real HTTP, live model, real SQL, persisted conversation and successful state',
              'limitations': ['Each three-turn scenario runs once', 'Recent query remains in last 10 messages',
                              'Not a general accuracy estimate; numeric checks need manual review'], 'cases': []}
    previous = database._DATA_DIR, database.DB_PATH
    try:
        with tempfile.TemporaryDirectory(prefix='agent_no_match_probe_') as tmp:
            database._DATA_DIR, database.DB_PATH = Path(tmp), Path(tmp)/'agent.db'
            _seed_sales()
            with TestClient(app) as client, patch.object(agent, 'get_llm', side_effect=factory):
                for name, question, expected in continuations:
                    session = profile = None
                    case = {'name': name, 'turns': []}
                    for message, goal in initial + [(question, expected)]:
                        start = time.perf_counter()
                        response = client.post('/api/agent/chat', json={
                            'message': message, 'session_id': session, 'profile_id': profile})
                        response.raise_for_status()
                        events = [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith('data: ')]
                        session, profile = events[0]['session_id'], events[0]['profile_id']
                        if goal is None:
                            # Reusing the immediately preceding no-match answer is valid without another call.
                            calls = [e for e in events if e['type'] == 'tool_call']
                            goal = {'filters': {'year': '2025', 'keyword': '甲公司'}, 'total': None}
                            checks = assess(events, goal)
                            if not calls:
                                checks.pop('query_filters')
                        else:
                            checks = assess(events, goal)
                        case['turns'].append({'question': message, 'expected': goal, 'checks': checks,
                            'passed': all(checks.values()), 'elapsed_ms': round((time.perf_counter()-start)*1000),
                            'events': events, 'state_after': task_state.load(session)})
                        print(json.dumps({'case': name, 'turn': len(case['turns']), 'checks': checks}, ensure_ascii=False), flush=True)
                    case['passed'] = all(t['passed'] for t in case['turns'])
                    report['cases'].append(case)
                    args.output.parent.mkdir(parents=True, exist_ok=True)
                    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    finally:
        database._DATA_DIR, database.DB_PATH = previous


if __name__ == '__main__':
    main()
