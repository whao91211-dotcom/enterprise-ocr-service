"""One real-model Office routing task using isolated synthetic sales; no OCR upload."""
import argparse
import json
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    from evals.task_benchmark import load_streaming_agent
    agent=load_streaming_agent()
    from db import database,crud,artifacts
    from tools import report_tool,office_tool
    from web import agent_chat
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    app=FastAPI();app.include_router(agent_chat.router)
    root=args.output.resolve().parent
    root.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp,patch.object(database,'_DATA_DIR',Path(tmp)),patch.object(database,'DB_PATH',Path(tmp)/'agent.db'), \
            patch.object(report_tool,'REPORTS_DIR',root/'files'),patch.object(office_tool,'OFFICE_DIR',root/'files'),TestClient(app) as client:
        database.init_db()
        doc=crud.insert_document('synthetic-office-agent.png')
        crud.insert_rows(doc,[{'desc':'合成甲公司','date':'2026-10-03','item':'A','amount':'2','sum':'100'},
                              {'desc':'合成乙公司','date':'2026-10-03','item':'B','amount':'3','sum':'240'}])
        crud.confirm_doc(doc)
        start=time.perf_counter()
        response=client.post('/api/agent/chat',json={'message':'请把2026年合成甲公司的已确认销售数据，使用同一份数据快照生成Word报告、Excel明细和PPT汇报，按顾客公司分析。三种文件都需要。'})
        response.raise_for_status()
        events=[json.loads(line[6:]) for line in response.text.splitlines() if line.startswith('data: ')]
        cards=[e for e in events if e['type']=='artifact']
        records=[artifacts.get(c['artifact_id']) for c in cards]
        checks={'answer':any(e['type']=='answer' for e in events),'no_error':not any(e['type']=='error' for e in events),
                'three_formats':{r['kind'] for r in records}=={'docx','xlsx','pptx'},
                'correct_totals':len(records)==3 and all(r['snapshot']['summary']['total_sum']==100 for r in records),
                'identical_snapshots':len(records)==3 and all(r['snapshot']==records[0]['snapshot'] for r in records)}
        result={'mode':'one real DeepSeek task, isolated synthetic sales','checks':checks,'events':events,
                'elapsed_ms':round((time.perf_counter()-start)*1000),'limits':['One task, not overall success rate; no confidential data']}
        args.output.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps({'checks':checks,'elapsed_ms':result['elapsed_ms']},ensure_ascii=False),flush=True)
        if not all(checks.values()):
            raise SystemExit(1)


if __name__=='__main__':
    main()
