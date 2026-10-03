"""Live OCR/upload/correction/confirmation/query/chart/report; fixed synthetic image only."""
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
    from evals.qwen_ocr_synthetic_probe import CASES, FIELDS, same
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
    report = {'mode': 'real Qwen OCR and DeepSeek via HTTP, isolated synthetic business data',
              'model': config.DEEPSEEK_MODEL, 'cases': [], 'artifacts': [],
              'limits': ['One workflow; not representative end-to-end success rate', 'No real confidential invoices sent; original InternVL remains offline']}
    previous = database._DATA_DIR, database.DB_PATH
    try:
        with tempfile.TemporaryDirectory(prefix='agent_artifact_flow_') as tmp, TestClient(app) as client, \
             patch.object(plot_tool, 'CHARTS_DIR', args.artifacts_root/'charts'), \
             patch.object(report_tool, 'REPORTS_DIR', args.artifacts_root/'reports'), \
             patch.object(agent, 'get_llm', side_effect=factory), \
             patch.object(agent_chat, 'UPLOAD_DIR', Path(tmp)/'uploads'):
            database._DATA_DIR, database.DB_PATH = Path(tmp), Path(tmp)/'agent.db'
            database.init_db()
            source = Path(__file__).resolve().parents[1]/'data/benchmark_v1/qwen-ocr-synthetic-2026-10-03/clean.png'
            start = time.perf_counter()
            response = client.post('/api/agent/chat/upload', data={
                'message': '请识别这张合成销售单据并入库为待确认，不要确认，也不要做统计。'},
                files={'file': ('synthetic-clean.png', source.read_bytes(), 'image/png')})
            response.raise_for_status()
            events = [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith('data: ')]
            session, profile = events[0]['session_id'], events[0]['profile_id']
            docs = crud.all_docs_with_stats()
            rows = crud.rows_by_doc(docs[0]['id']) if len(docs)==1 else []
            expected = CASES[0][1]
            checks = {'one_document': len(docs)==1,
                'one_ocr_call': sum(e.get('name')=='ocr_recognize_chat' and e['type']=='tool_call' for e in events)==1,
                'two_pending_rows': len(rows)==2 and all(r['status']=='pending' for r in rows),
                'all_16_fields': len(rows)==2 and all(same(rows[i][key], row[j], j>=4)
                    for i,row in enumerate(expected) for j,key in enumerate(FIELDS)),
                'no_confirmed_rows': crud.confirmed_count()==0,
                'answer': any(e['type']=='answer' for e in events),
                'no_error': not any(e['type']=='error' for e in events)}
            report['cases'].append({'id':'ocr_upload', 'events':events, 'checks':checks,
                'passed':all(checks.values()), 'elapsed_ms':round((time.perf_counter()-start)*1000)})
            args.output.parent.mkdir(parents=True,exist_ok=True)
            args.output.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
            print(json.dumps({'id':'ocr_upload','checks':checks},ensure_ascii=False),flush=True)
            if not all(checks.values()):
                raise SystemExit(1)
            doc_id = docs[0]['id']
            file_name = docs[0]['file_name']
            row_id = rows[0]['id']
            definitions = [
                ('inspect_pending', f'查看{file_name}的识别行和确认状态。'),
                ('correct_pending', f'我已核对原始单据，请将行ID {row_id} 的amount改为3、price改为50、sum改为150；暂时不要确认。'),
                ('exclude_pending', '请重新统计2026年全部公司已确认的总销售额。'),
                ('confirm_and_query', f'我已核对无误，请确认{file_name}，再查询2026年全部公司已确认总销售额。'),
                ('draw_chart', '请画2026年全部公司的销售额柱状图，按顾客公司分组。'),
                ('generate_report', '请生成2026年全部公司的Word销售统计报告，并包含按顾客公司分组的统计图。'),
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
                    checks['correct_values'] = all(float(row[k]) == v for k, v in [('amount', 3), ('price', 50), ('sum', 150)])
                    checks['not_confirmed'] = row['status'] == 'pending'
                elif ident == 'exclude_pending':
                    results = [e['result'] for e in events if e['type']=='tool_result' and e.get('name')=='rag_summarize']
                    answer = next((e['answer'] for e in events if e['type']=='answer'), '')
                    checks['queried_correct_scope'] = bool(calls) and all(e['name']=='rag_summarize'
                        and str(e['args'].get('year'))=='2026' and not e['args'].get('keyword') for e in calls)
                    checks['empty_confirmed_result'] = bool(results) and all('没有符合条件的已确认数据' in r for r in results)
                    checks['pending_excluded'] = crud.confirmed_count()==0
                    checks['no_match_disclosed'] = any(word in answer for word in ('没有', '无匹配', '未找到'))
                elif ident == 'confirm_and_query':
                    checks['confirmation_tool'] = 'correct_confirm' in names
                    checks['now_confirmed'] = crud.rows_by_doc(doc_id)[0]['status'] == 'confirmed'
                    query_events = [e for e in events if e.get('name') in ('rag_summarize', 'rag_query') or e['type'] in ('answer', 'error')]
                    checks.update(score(query_events, {'kind': 'query', 'filters': {'year': '2026', 'keyword': ''}, 'total': '390'}))
                elif ident == 'draw_chart':
                    paths = list((args.artifacts_root/'charts').glob('*.png'))
                    checks['chart_tool_scope'] = any(e['name'] == 'plot_chart' and e['args'].get('year') == '2026'
                        and e['args'].get('group_by') == 'desc' and e['args'].get('chart_type', 'bar') == 'bar' for e in calls)
                    checks['png_exists'] = bool(paths)
                    for path in paths:
                        with Image.open(path) as image:
                            image.verify()
                        report['artifacts'].append(str(path))
                else:
                    paths = list((args.artifacts_root/'reports').glob('*.docx'))
                    checks['report_tool_scope'] = any(e['name'] == 'generate_report' and e['args'].get('year') == '2026' for e in calls)
                    checks['docx_exists'] = bool(paths)
                    for path in paths:
                        document = Document(path)
                        overview = {r.cells[0].text: r.cells[1].text for r in document.tables[0].rows}
                        checks['report_scope_count_total'] = overview['涉及单据'] == '1 张' and overview['确认记录行数'] == '2 行' and overview['总销售额'] == '390.00'
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
    if not all(case['passed'] for case in report['cases']):
        raise SystemExit(1)


if __name__ == '__main__':
    main()
