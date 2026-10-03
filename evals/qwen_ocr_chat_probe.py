"""Real DeepSeek -> Qwen OCR -> pending SQLite via chat upload; synthetic only."""
import argparse
import json
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    # Fixed programmatically generated image; no user-selected invoice input.
    source = Path(__file__).resolve().parents[1]/'data/benchmark_v1/qwen-ocr-synthetic-2026-10-03/clean.png'
    from evals.qwen_ocr_synthetic_probe import CASES, FIELDS, same
    expected = CASES[0][1]
    from evals.task_benchmark import load_streaming_agent
    load_streaming_agent()
    from db import database, crud
    from web import agent_chat
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    app = FastAPI()
    app.include_router(agent_chat.router)
    started = time.perf_counter()
    with tempfile.TemporaryDirectory(prefix='qwen_ocr_chat_') as tmp:
        directory = Path(tmp)
        with patch.object(database,'_DATA_DIR',directory), patch.object(database,'DB_PATH',directory/'agent.db'), \
             patch.object(agent_chat,'UPLOAD_DIR',directory/'uploads'), TestClient(app) as client:
            database.init_db()
            response = client.post('/api/agent/chat/upload', data={
                'message':'请识别这张合成销售单据并入库为待确认，不要确认，也不要做统计。'},
                files={'file':('synthetic-clean.png',source.read_bytes(),'image/png')})
            response.raise_for_status()
            events = [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith('data: ')]
            calls = [e for e in events if e['type']=='tool_call']
            docs = crud.all_docs_with_stats()
            rows = crud.rows_by_doc(docs[0]['id']) if docs else []
            checks = {'exactly_one_ocr_call':len(calls)==1 and calls[0]['name']=='ocr_recognize_chat',
                      'exact_rows':len(rows)==len(expected),
                      'pending_only':bool(rows) and all(r['status']=='pending' for r in rows),
                      'confirmed_stats_excluded':crud.confirmed_count()==0,
                      'field_values':len(rows)==len(expected) and all(same(rows[i][key],row[j],j>=4)
                          for i,row in enumerate(expected) for j,key in enumerate(FIELDS)),
                      'answer':any(e['type']=='answer' for e in events),
                      'no_error':not any(e['type']=='error' for e in events)}
    report = {'mode':'Real model decisions, real Qwen OCR, actual chat upload + isolated SQLite',
              'dataset':'synthetic clean two-row image only', 'checks':checks, 'passed':all(checks.values()),
              'events':events,'elapsed_ms':round((time.perf_counter()-started)*1000),
              'limits':['One clean image workflow; not real invoice accuracy or long-term availability']}
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'checks':checks,'passed':report['passed'],'elapsed_ms':report['elapsed_ms']},ensure_ascii=False))


if __name__=='__main__':
    main()
