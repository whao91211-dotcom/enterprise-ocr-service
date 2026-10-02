"""Live HTTP diagnostics on synthetic data; each case runs once, without retries."""
import argparse
import json
import re
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def assess(events, expected):
    calls = [e for e in events if e['type'] == 'tool_call']
    answer = next((e['answer'] for e in events if e['type'] == 'answer'), '')
    checks = {'completed': bool(answer) and not any(e['type'] == 'error' for e in events)}
    if 'filters' in expected:
        queries = [e for e in calls if e['name'] == 'rag_summarize']
        checks['query_filters'] = bool(queries) and all(
            all(str(e['args'].get(k, '')) == v for k, v in expected['filters'].items())
            for e in queries) and len(queries) == len(calls)
        if expected['total'] is None:
            checks['answer_reports_no_match'] = any(word in answer for word in ('无', '未找到', '没有'))
        else:
            checks['answer_contains_total'] = bool(re.search(
                r'(?<![\d.])'+expected['total']+r'(?:\.0+)?(?![\d.])', answer.replace(',', '')))
    if expected.get('no_tools'):
        checks['no_tools'] = not calls
    if 'prefix' in expected:
        checks['answer_prefix'] = answer.lstrip().startswith(expected['prefix'])
    return checks


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    from db import database, chat_memory, task_state
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
    cases = [
        ('explicit_override', '改查2025年乙公司的销售额，按公司分组。',
         {'filters': {'year': '2025', 'keyword': '乙公司', 'group_by': 'desc'}, 'total': '900'}, None),
        ('partial_followup', '继续上次甲公司的查询，只把年份改为2025年，按公司分组。',
         {'filters': {'year': '2025', 'keyword': '甲公司', 'group_by': 'desc'}, 'total': None}, None),
        ('new_global_query', '开始一个新查询：不限制年份和公司，统计全部已确认记录的总销售额。',
         {'filters': {'year': '', 'keyword': ''}, 'total': '1200'}, None),
        ('unrelated_topic', '新话题：用一句话解释Python列表是什么，不查询销售数据。',
         {'no_tools': True}, None),
        ('persisted_preference', '统计2024年甲公司的销售额。',
         {'filters': {'year': '2024', 'keyword': '甲公司'}, 'total': '100', 'prefix': '统计结果'},
         '回答正文必须以统计结果四个字开头，不添加Markdown符号。'),
        ('current_style_override', '统计2024年甲公司的销售额。本次回答正文必须以本次结果四个字开头，不添加Markdown符号。',
         {'filters': {'year': '2024', 'keyword': '甲公司'}, 'total': '100', 'prefix': '本次结果'},
         '回答正文必须以统计结果四个字开头，不添加Markdown符号。'),
    ]
    report = {'model': config.DEEPSEEK_MODEL, 'dataset': '3 synthetic rows: 2024甲100, 2024乙200, 2025乙900',
              'execution': 'real HTTP SSE + persisted SQLite state/preferences + live model + real SQL tools',
              'limitations': ['One run per case; not population accuracy or latency comparison',
                              'Answer substring checks require manual review; no token/cost measurement'], 'cases': []}
    previous = database._DATA_DIR, database.DB_PATH
    try:
        with tempfile.TemporaryDirectory(prefix='agent_context_probe_') as tmp:
            database._DATA_DIR, database.DB_PATH = Path(tmp), Path(tmp)/'agent.db'
            _seed_sales()
            with TestClient(app) as client, patch.object(agent, 'get_llm', side_effect=factory):
                def post(body):
                    response = client.post('/api/agent/chat', json=body)
                    response.raise_for_status()
                    return [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith('data: ')]
                for name, question, expected, preference in cases:
                    session, _ = chat_memory.resolve_session(None)
                    if preference:
                        saved = post({'message': '记住：'+preference})
                        profile = saved[0]['profile_id']
                        assert '已记住' in saved[-1]['answer']
                    else:
                        profile = None
                        task_state.save(session, {'tool': 'rag_summarize', 'year': '2024', 'keyword': '甲公司', 'group_by': 'desc'})
                        chat_memory.save_turn(session, '查询2024年甲公司销售额', '销售额100')
                        for i in range(7):
                            chat_memory.save_turn(session, f'无关对话{i}', '收到')
                    start = time.perf_counter()
                    events = post({'message': question, 'session_id': session, 'profile_id': profile})
                    checks = assess(events, expected)
                    case = {'name': name, 'question': question, 'expected': expected, 'checks': checks,
                            'passed': all(checks.values()), 'elapsed_ms': round((time.perf_counter()-start)*1000),
                            'events': events, 'state_after': task_state.load(session)}
                    report['cases'].append(case)
                    args.output.parent.mkdir(parents=True, exist_ok=True)
                    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
                    print(json.dumps({k:case[k] for k in ('name', 'checks', 'passed', 'elapsed_ms')}, ensure_ascii=False), flush=True)
    finally:
        database._DATA_DIR, database.DB_PATH = previous


if __name__ == '__main__':
    main()
