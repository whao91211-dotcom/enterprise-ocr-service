"""Six fixed reliability cases; scripted setup, live model only for evaluated turns."""
import argparse
import json
import re
import sys
import tempfile
import time
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from evals.context_preference_probe import assess

CASES = [
    {'id': 'no_match_amount', 'setup': 'no_match',
     'question': '刚才2025年甲公司这个查询的销售额是多少？不要更换条件。',
     'expected': {'kind': 'no_match'}},
    {'id': 'confirmed_zero_control', 'setup': 'zero',
     'question': '请重新查询2025年甲公司的销售额。',
     'expected': {'kind': 'query', 'filters': {'year': '2025', 'keyword': '甲公司'}, 'total': '0'}},
    {'id': 'truncated_no_match_ambiguous', 'setup': 'truncated',
     'question': '继续刚才没有匹配的那个查询，只换成乙公司，年份不变。',
     'expected': {'kind': 'clarify'}},
    {'id': 'truncated_explicit_override', 'setup': 'truncated',
     'question': '请重新查询2025年乙公司的销售额。',
     'expected': {'kind': 'query', 'filters': {'year': '2025', 'keyword': '乙公司'}, 'total': '900'}},
    {'id': 'latest_same_filters', 'setup': 'updated',
     'question': '请重新查询2024年甲公司销售额，我要数据库此刻的最新结果，不要直接引用上次回答。',
     'expected': {'kind': 'query', 'filters': {'year': '2024', 'keyword': '甲公司'}, 'total': '150'}},
    {'id': 'latest_global_scope', 'setup': 'updated',
     'question': '请重新查询数据库此刻2024年全部公司的总销售额，不限制公司，不要直接引用上次回答。',
     'expected': {'kind': 'query', 'filters': {'year': '2024', 'keyword': ''}, 'total': '350'}},
]


def score(events, goal):
    text = next((e['answer'] for e in events if e['type'] == 'answer'), '')
    calls = [e for e in events if e['type'] == 'tool_call']
    checks = assess(events, {})
    if goal['kind'] == 'query':
        checks.update(assess(events, goal))
        totals = []
        for event in events:
            if event['type'] == 'tool_result' and event.get('name') == 'rag_summarize':
                try:
                    totals.append(Decimal(str(json.loads(event['result'])['summary']['total_sum'])))
                except (ValueError, KeyError, TypeError):
                    pass
        checks['tool_total'] = bool(totals) and all(v == Decimal(goal['total']) for v in totals)
    elif goal['kind'] == 'clarify':
        checks['no_query_guess'] = not calls
        checks['asks_for_conditions'] = any(w in text for w in ('请', '能否', '补充', '无法确定', '不明确')) and any(
            w in text for w in ('年份', '条件', '哪一次', '具体'))
    elif goal['kind'] == 'no_match':
        checks['no_match_explained'] = any(w in text for w in ('没有', '无匹配', '未找到', '未检索'))
        # Narrow warning detector; manual review remains necessary for paraphrases/negation.
        normalized = re.sub(r'[*`_\s]', '', text)
        clauses = re.split(r'[。；;\n]', normalized)
        checks['no_zero_assertion'] = not any(
            re.search(r'(?:销售额|总金额|票面金额)(?:合计)?(?:是|为|：|:)(?:0(?:\.0+)?|零)(?!\d)', c)
            and not any(w in c for w in ('不能', '无法', '不代表', '不等于', '未能')) for c in clauses)
        checks['no_old_query'] = all(e['name'] in ('rag_query', 'rag_summarize') and
            str(e['args'].get('year', '')) == '2025' and e['args'].get('keyword') == '甲公司' for e in calls)
    return checks


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--repeats', type=int, choices=range(1, 6), default=1)
    parser.add_argument('--include-variants', action='store_true')
    args = parser.parse_args()
    from db import database, chat_memory, task_state, crud
    from evals.task_benchmark import load_streaming_agent, ScriptedModel
    from evals.real_text_baseline import _seed_sales
    from langchain_core.messages import AIMessage
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
    variants = {
        'no_match_amount': '还是刚才2025年甲公司的条件。没有查到记录，那能确定它实际卖了多少钱吗？',
        'truncated_no_match_ambiguous': '把刚才查不到数据的那次改成乙公司再统计，年份沿用那次的，不要改年份。',
    }
    definitions = [{**c, 'base_case_id': c['id'], 'wording': 'original'} for c in CASES]
    if args.include_variants:
        definitions += [{**c, 'id': c['id']+'_variant', 'base_case_id': c['id'],
                         'wording': 'variant', 'question': variants[c['id']]}
                        for c in CASES if c['id'] in variants]
    runs = [{**c, 'repeat': repeat} for repeat in range(1, args.repeats+1) for c in definitions]
    report = {'suite': 'known-gap-v1', 'model': config.DEEPSEEK_MODEL,
              'repeats': args.repeats, 'include_variants': args.include_variants,
              'model_settings': {'temperature': .3, 'timeout_seconds': 20, 'max_retries': 0},
              'dataset': 'synthetic 2024甲100/乙200, 2025乙900; isolated zero/addition controls',
              'limits': ['Scripted setup uses real HTTP, SQL and state extraction; only target turn uses live model',
                         'Limited repeated development cases; automatic semantic checks need manual review',
                         'Not overall accuracy; no comparable before/after or cost measurement'], 'cases': []}
    previous = database._DATA_DIR, database.DB_PATH
    try:
        with tempfile.TemporaryDirectory(prefix='agent_known_gap_') as root, TestClient(app) as client:
            for definition in runs:
                directory = Path(root)/f"{definition['repeat']}_{definition['id']}"
                directory.mkdir()
                database._DATA_DIR, database.DB_PATH = directory, directory/'agent.db'
                _seed_sales()
                session = profile = None

                def post(message):
                    nonlocal session, profile
                    response = client.post('/api/agent/chat', json={'message': message,
                        'session_id': session, 'profile_id': profile})
                    response.raise_for_status()
                    events = [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith('data: ')]
                    session, profile = events[0]['session_id'], events[0]['profile_id']
                    return events

                def add_row(file_name, year, total):
                    doc = crud.insert_document(file_name)
                    crud.insert_rows(doc, [{'desc': '甲公司', 'date': year+'-07-01', 'from': '示例供货商',
                        'item': '新增商品', 'amount': '1', 'price': total, 'tax': '0', 'sum': total}])
                    crud.confirm_doc(doc, 'synthetic-eval')

                def setup_query(year, answer):
                    model = ScriptedModel([[{'name': 'rag_summarize', 'args': {
                        'year': year, 'keyword': '甲公司', 'group_by': 'desc'}, 'id': 'setup'}]])
                    original = model.invoke

                    def invoke(messages):
                        response = original(messages)
                        return response if response.tool_calls else AIMessage(content=answer)

                    model.invoke = invoke
                    with patch.object(agent, 'get_llm', return_value=model):
                        return post(f'统计{year}年甲公司的销售额，按公司分组。')

                setup_events = [setup_query('2024', '2024年甲公司已确认票面金额合计100。')]
                if definition['setup'] == 'zero':
                    add_row('synthetic-zero.png', '2025', '0')
                    setup_events.append(setup_query('2025', '2025年甲公司有1条已确认记录，票面金额合计0。'))
                elif definition['setup'] in ('no_match', 'truncated'):
                    setup_events.append(setup_query('2025', '没有符合2025年甲公司条件的已确认数据。'))
                if definition['setup'] == 'truncated':
                    for i in range(7):
                        chat_memory.save_turn(session, f'无关消息{i}', '收到')
                if definition['setup'] == 'updated':
                    add_row('synthetic-added.png', '2024', '50')
                before = task_state.load(session)
                history = chat_memory.load_messages(session, 10)
                start = time.perf_counter()
                with patch.object(agent, 'get_llm', side_effect=factory):
                    events = post(definition['question'])
                checks = score(events, definition['expected'])
                case = {**definition, 'setup_events': setup_events, 'history_before': history,
                        'state_before': before, 'events': events, 'checks': checks,
                        'passed': all(checks.values()), 'state_after': task_state.load(session),
                        'elapsed_ms': round((time.perf_counter()-start)*1000)}
                report['cases'].append(case)
                args.output.parent.mkdir(parents=True, exist_ok=True)
                args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
                print(json.dumps({'id': case['id'], 'repeat': case['repeat'], 'checks': checks,
                                  'elapsed_ms': case['elapsed_ms']}, ensure_ascii=False), flush=True)
    finally:
        database._DATA_DIR, database.DB_PATH = previous


if __name__ == '__main__':
    main()
