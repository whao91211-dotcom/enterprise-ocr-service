"""Live correction/confirmation/query/chart/report workflow on synthetic data."""
import argparse
import json
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from evals.known_gap_benchmark import score


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--artifacts-root', type=Path, required=True)
    args = parser.parse_args()
    args.artifacts_root = args.artifacts_root.resolve()
    args.artifacts_root.mkdir(parents=True, exist_ok=True)
    from db import database, crud
    from evals.task_benchmark import load_streaming_agent
    from evals.real_text_baseline import _seed_sales
    agent = load_streaming_agent()
    from tools import plot_tool, report_tool
    from web import agent_chat
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from langchain_deepseek import ChatDeepSeek
    from docx import Document
    from PIL import Image
    import config
    app = FastAPI()
    app.include_router(agent_chat.router)
    factory = lambda: ChatDeepSeek(model=config.DEEPSEEK_MODEL,
        api_key=config.require_deepseek_key(), temperature=.3, timeout=20, max_retries=0)
    report = {'mode': 'live HTTP synthetic workflow, seeded pending row (no OCR inference)',
              'model': config.DEEPSEEK_MODEL, 'cases': [], 'artifacts': [],
              'limits': ['One workflow; not representative end-to-end success rate', 'OCR recognition is not tested']}
    previous = database._DATA_DIR, database.DB_PATH
    try:
        with tempfile.TemporaryDirectory(prefix='agent_artifact_flow_') as tmp, TestClient(app) as client, \
             patch.object(plot_tool, 'CHARTS_DIR', args.artifacts_root/'charts'), \
             patch.object(report_tool, 'REPORTS_DIR', args.artifacts_root/'reports'), \
             patch.object(agent, 'get_llm', side_effect=factory):
            database._DATA_DIR, database.DB_PATH = Path(tmp), Path(tmp)/'agent.db'
            _seed_sales()
            doc_id = crud.insert_document('synthetic-pending.png')
            crud.insert_rows(doc_id, [{'desc': '甲公司', 'date': '2024-06-01', 'from': '示例供货商',
                'item': '待核对商品', 'amount': '1', 'price': '9999', 'tax': '0', 'sum': '9999'}])
            row_id = crud.rows_by_doc(doc_id)[0]['id']
            session = profile = None
            definitions = [
                ('inspect_pending', '查看synthetic-pending.png的识别行和确认状态。'),
                ('correct_pending', f'我已核对原始单据，请将行ID {row_id} 的amount改为2、price改为50、sum改为100；暂时不要确认。'),
                ('exclude_pending', '请重新统计2024年全部公司已确认的总销售额。'),
                ('confirm_and_query', '我已核对无误，请确认synthetic-pending.png，再查询2024年全部公司已确认总销售额。'),
                ('draw_chart', '请画2024年全部公司的销售额柱状图，按顾客公司分组。'),
                ('generate_report', '请生成2024年全部公司的Word销售统计报告，并包含按顾客公司分组的统计图。'),
            ]
            for ident, question in definitions:
                start = time.perf_counter()
                response = client.post('/api/agent/chat', json={'message': question, 'session_id': session, 'profile_id': profile})
                response.raise_for_status()
                events = [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith('data: ')]
                session, profile = events[0]['session_id'], events[0]['profile_id']
                calls = [e for e in events if e['type'] == 'tool_call']
                names = [e['name'] for e in calls]
                checks = {'answer': any(e['type'] == 'answer' for e in events),
                          'no_error_event': not any(e['type'] == 'error' for e in events)}
                if ident == 'inspect_pending':
                    checks['read_tool'] = 'correct_show_rows' in names
                    checks['still_pending'] = crud.rows_by_doc(doc_id)[0]['status'] == 'pending'
                elif ident == 'correct_pending':
                    row = crud.rows_by_doc(doc_id)[0]
                    checks['write_tool'] = 'correct_update_row' in names
                    checks['correct_values'] = all(float(row[k]) == v for k, v in [('amount', 2), ('price', 50), ('sum', 100)])
                    checks['not_confirmed'] = row['status'] == 'pending'
                elif ident == 'exclude_pending':
                    checks.update(score(events, {'kind': 'query', 'filters': {'year': '2024', 'keyword': ''}, 'total': '300'}))
                elif ident == 'confirm_and_query':
                    checks['confirmation_tool'] = 'correct_confirm' in names
                    checks['now_confirmed'] = crud.rows_by_doc(doc_id)[0]['status'] == 'confirmed'
                    query_events = [e for e in events if e.get('name') in ('rag_summarize', 'rag_query') or e['type'] in ('answer', 'error')]
                    checks.update(score(query_events, {'kind': 'query', 'filters': {'year': '2024', 'keyword': ''}, 'total': '400'}))
                elif ident == 'draw_chart':
                    paths = list((args.artifacts_root/'charts').glob('*.png'))
                    checks['chart_tool_scope'] = any(e['name'] == 'plot_chart' and e['args'].get('year') == '2024'
                        and e['args'].get('group_by') == 'desc' and e['args'].get('chart_type', 'bar') == 'bar' for e in calls)
                    checks['png_exists'] = bool(paths)
                    for path in paths:
                        with Image.open(path) as image:
                            image.verify()
                        report['artifacts'].append(str(path))
                else:
                    paths = list((args.artifacts_root/'reports').glob('*.docx'))
                    checks['report_tool_scope'] = any(e['name'] == 'generate_report' and e['args'].get('year') == '2024' for e in calls)
                    checks['docx_exists'] = bool(paths)
                    for path in paths:
                        document = Document(path)
                        overview = {r.cells[0].text: r.cells[1].text for r in document.tables[0].rows}
                        checks['report_scope_count_total'] = overview['涉及单据'] == '3 张' and overview['确认记录行数'] == '3 行' and overview['总销售额'] == '400.00'
                        checks['embedded_chart'] = len(document.inline_shapes) == 1
                        report['artifacts'].append(str(path))
                case = {'id': ident, 'question': question, 'events': events, 'checks': checks,
                        'passed': all(checks.values()), 'elapsed_ms': round((time.perf_counter()-start)*1000)}
                report['cases'].append(case)
                args.output.parent.mkdir(parents=True, exist_ok=True)
                args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
                print(json.dumps({'id': ident, 'checks': checks}, ensure_ascii=False), flush=True)
    finally:
        database._DATA_DIR, database.DB_PATH = previous


if __name__ == '__main__':
    main()
