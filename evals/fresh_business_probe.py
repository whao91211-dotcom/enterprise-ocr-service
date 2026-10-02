"""Frozen expanded synthetic cases; offline oracle first, one optional live pass."""
import argparse
import json
import re
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

CASES = [
    ('full_total', '请重新查询2027年全部公司的票面销售金额合计，使用完整聚合，不要仅合计展示的分组。', '', 'total_sum', 1250),
    ('quantity', '请查询2027年全部公司的销售数量合计，区分数量与金额，使用完整聚合。', '', 'total_amount', 57),
    ('discount', '请查询2027年批量公司00的票面金额合计，包含折扣记录。', '批量公司00', 'total_sum', -24),
    ('real_zero', '请查询2027年零值公司的票面金额合计，并说明是否存在已确认记录。', '零值公司', 'total_sum', 0),
    ('other_year', '请查询2028年全部公司的票面金额合计，不要混入其他年份。', '', 'total_sum', 333),
    ('pending', '请查询2027年待核公司的已确认销售记录，未确认草稿不要用于结论。', '待核公司', None, None),
]


def seed():
    from db import crud
    from db.database import init_db
    init_db()
    doc = crud.insert_document('fresh-2027.png')
    rows = [{'desc': f'批量公司{i:02d}', 'date': '2027-03-01', 'item': f'商品{i:02d}',
             'amount': '1', 'price': '99999', 'sum': str(i+1)} for i in range(50)]
    rows += [{'desc': '批量公司00', 'date': '2027-03-01', 'item': '折扣', 'amount': '0', 'price': '0', 'sum': '-25'},
             {'desc': '零值公司', 'date': '2027-03-01', 'item': '零票面', 'amount': '7', 'price': '99999', 'sum': '0'}]
    crud.insert_rows(doc, rows)
    crud.confirm_doc(doc)
    doc = crud.insert_document('fresh-2028.png')
    crud.insert_rows(doc, [{'desc': '跨年公司', 'date': '2028-01-01', 'item': '跨年商品', 'amount': '3', 'sum': '333'}])
    crud.confirm_doc(doc)
    doc = crud.insert_document('fresh-pending.png')
    crud.insert_rows(doc, [{'desc': '待核公司', 'date': '2027-03-01', 'item': '待核商品', 'amount': '999', 'sum': '99999'}])


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--live', action='store_true')
    args = parser.parse_args()
    from db import database
    from tools.rag_tool import rag_query, rag_summarize
    previous = database._DATA_DIR, database.DB_PATH
    report = {'dataset_version': 'fresh-business-v1', 'frozen_cases': CASES,
              'data_recipe': '50 groups with invoice sums 1..50, discount -25, zero-value quantity 7, other year 333, pending 99999',
              'limits': ['Six new synthetic questions; one pass, no population accuracy estimate',
                         'After this run these cases cannot be treated as untouched for subsequent tuning'], 'offline': {}, 'cases': []}
    try:
        with tempfile.TemporaryDirectory(prefix='agent_fresh_business_') as root:
            database._DATA_DIR, database.DB_PATH = Path(root), Path(root)/'agent.db'
            seed()
            summary = json.loads(rag_summarize.invoke({'group_by': 'desc', 'year': '2027'}))
            details = json.loads(rag_query.invoke({'year': '2027'}))
            report['offline'] = {
                'full_summary': summary['summary'] == {'total_rows': 52, 'total_amount': 57, 'total_sum': 1250},
                'group_omissions': (summary['total_groups'], summary['returned_groups'], summary['omitted_groups']) == (51, 20, 31),
                'details_are_sample': (details['matched_rows'], details['returned_rows'], details['omitted_rows']) == (52, 20, 32),
                'discount_preserved': json.loads(rag_summarize.invoke({'keyword': '批量公司00', 'year': '2027'}))['summary']['total_sum'] == -24,
                'pending_excluded': '没有' in rag_summarize.invoke({'keyword': '待核公司', 'year': '2027'}),
            }
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
            if not all(report['offline'].values()):
                raise RuntimeError('Frozen dataset offline oracle failed')
            if args.live:
                from evals.task_benchmark import load_streaming_agent
                agent = load_streaming_agent()
                from langchain_deepseek import ChatDeepSeek
                import config
                factory = lambda: ChatDeepSeek(model=config.DEEPSEEK_MODEL, api_key=config.require_deepseek_key(),
                                              temperature=.3, timeout=20, max_retries=0)
                for ident, question, keyword, field, value in CASES:
                    started = time.perf_counter()
                    with patch.object(agent, 'get_llm', side_effect=factory):
                        events = list(agent.run_agent_events(question))
                    answer = next((e['answer'] for e in events if e['type'] == 'answer'), '')
                    calls = [e for e in events if e['type'] == 'tool_call']
                    year = '2028' if ident == 'other_year' else '2027'
                    checks = {'query_executed': bool(calls), 'scope': bool(calls) and all(
                        e['name'] in ('rag_query', 'rag_summarize') and str(e['args'].get('year')) == year
                        and e['args'].get('keyword', e['args'].get('query', '')) == keyword for e in calls)}
                    if field:
                        summaries = []
                        for e in events:
                            if e['type'] == 'tool_result' and e.get('name') == 'rag_summarize':
                                match = re.search(r'"summary":(\{[^}]+\})', e['result'])
                                if match:
                                    summaries.append(json.loads(match.group(1)))
                        checks['full_tool_value'] = bool(summaries) and all(s[field] == value for s in summaries)
                        numbers = re.findall(r'(?<!\d)-?\d+(?:\.\d+)?', answer.replace(',', '').replace('，', ''))
                        checks['answer_value'] = any(float(n) == value for n in numbers)
                        if ident == 'quantity':
                            checks['quantity_label'] = '数量' in answer
                        if ident == 'real_zero':
                            checks['record_existence'] = '没有' not in answer and any(w in answer for w in ('记录', '已确认', '存在'))
                    else:
                        checks['no_match'] = any(w in answer for w in ('没有', '无匹配', '未检索', '未找到'))
                    case = {'id': ident, 'checks': checks, 'passed': all(checks.values()), 'events': events,
                            'elapsed_ms': round((time.perf_counter()-started)*1000)}
                    report['cases'].append(case)
                    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
                    print(json.dumps({'id': ident, 'checks': checks}, ensure_ascii=False), flush=True)
    finally:
        database._DATA_DIR, database.DB_PATH = previous


if __name__ == '__main__':
    main()
