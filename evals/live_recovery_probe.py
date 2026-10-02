"""Live model recovery with explicit fault injection on synthetic SQL tasks."""
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
    parser.add_argument('--cases', nargs='+', choices=('transient_read', 'invalid_group', 'permanent_read', 'ocr_offline'),
                        default=['transient_read', 'invalid_group', 'permanent_read', 'ocr_offline'])
    parser.add_argument('--repeats', type=int, choices=(1, 2), default=2)
    args = parser.parse_args()
    from db import database, crud
    from evals.task_benchmark import load_streaming_agent
    from evals.real_text_baseline import _seed_sales
    from langchain_deepseek import ChatDeepSeek
    import config
    agent = load_streaming_agent()
    from web import agent_chat
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    app = FastAPI()
    app.include_router(agent_chat.router)
    factory = lambda: ChatDeepSeek(model=config.DEEPSEEK_MODEL,
        api_key=config.require_deepseek_key(), temperature=.3, timeout=20, max_retries=0)
    report = {'dataset': 'synthetic 2024甲100/乙200, 2025乙900', 'model': config.DEEPSEEK_MODEL,
              'selected_cases': args.cases, 'repeats': args.repeats,
              'limits': ['One or two runs per selected fault; not population recovery rate',
                         'Fault injected in tools; model actions are autonomous', 'No real OCR quality measurement'], 'cases': []}
    previous = database._DATA_DIR, database.DB_PATH
    try:
        with tempfile.TemporaryDirectory(prefix='agent_recovery_') as root, TestClient(app) as client:
            for repeat in range(1, args.repeats+1):
                for kind in args.cases:
                    directory = Path(root)/f'{repeat}_{kind}'
                    directory.mkdir()
                    database._DATA_DIR, database.DB_PATH = directory, directory/'agent.db'
                    _seed_sales()
                    counts = {'invocations': 0, 'injected': 0}
                    real_tool = agent._TOOL_BY_NAME['rag_summarize']

                    class InvalidFirstCall:
                        def __init__(self):
                            self.llm = factory()
                            self.first = True

                        def bind_tools(self, tools):
                            self.bound = self.llm.bind_tools(tools)
                            return self

                        def invoke(self, messages):
                            response = self.bound.invoke(messages)
                            if self.first and response.tool_calls:
                                self.first = False
                                for call in response.tool_calls:
                                    if call['name'] == 'rag_summarize':
                                        call['args'] = {**call['args'], 'group_by': 'unsupported'}
                            return response

                    class FaultTool:
                        def invoke(self, arguments):
                            counts['invocations'] += 1
                            if kind == 'invalid_group' and arguments.get('group_by') == 'unsupported':
                                counts['injected'] += 1
                                return real_tool.invoke(arguments)
                            if kind in ('permanent_read', 'ocr_offline') or counts['invocations'] == 1:
                                counts['injected'] += 1
                                raise RuntimeError('OCR服务已离线，请勿重复入库' if kind == 'ocr_offline' else 'temporary synthetic SQL service failure')
                            return real_tool.invoke(arguments)

                    target = 'ocr_recognize_chat' if kind == 'ocr_offline' else 'rag_summarize'
                    question = ('请识别并入库此销售单据，图片路径为 C:/synthetic-eval/invoice.png。'
                                if kind == 'ocr_offline' else '请统计2024年甲公司的销售额，按公司分组，使用已确认数据。')
                    start = time.perf_counter()
                    model_factory = InvalidFirstCall if kind == 'invalid_group' else factory
                    with patch.dict(agent._TOOL_BY_NAME, {target: FaultTool()}), patch.object(agent, 'get_llm', side_effect=model_factory):
                        response = client.post('/api/agent/chat', json={'message': question})
                    response.raise_for_status()
                    events = [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith('data: ')]
                    answer = next((e['answer'] for e in events if e['type'] == 'answer'), '')
                    calls = [e for e in events if e['type'] == 'tool_call']
                    checks = {'fault_exercised': counts['injected'] > 0, 'has_answer': bool(answer),
                              'no_runtime_error_event': not any(e['type'] == 'error' for e in events)}
                    if kind in ('transient_read', 'invalid_group'):
                        successful_events = [e for e in events if not (e['type'] == 'tool_result' and e.get('result', '').startswith(('工具执行出错:', 'group_by 仅支持:')))]
                        checks.update(score(successful_events, {'kind': 'query', 'filters': {'year': '2024', 'keyword': '甲公司'}, 'total': '100'}))
                        checks['bounded_recovery'] = 2 <= counts['invocations'] <= 3
                        if kind == 'invalid_group':
                            checks['parameter_repaired'] = any(e['args'].get('group_by') == 'unsupported' for e in calls) and any(
                                e['args'].get('group_by') in ('item', 'desc', 'date') for e in calls[1:])
                    elif kind == 'permanent_read':
                        checks['bounded_stop'] = 1 <= counts['invocations'] <= 4 and len(calls) <= config.AGENT_MAX_TOOL_CALLS
                        checks['failure_disclosed'] = any(w in answer for w in ('失败', '无法', '不可用', '错误'))
                    else:
                        checks['write_not_retried'] = counts['invocations'] == 1 and len(calls) == 1
                        checks['no_ocr_database_write'] = len(crud.all_docs_with_stats()) == 3
                        checks['service_failure_disclosed'] = '离线' in answer or '识别失败' in answer
                    case = {'id': kind, 'repeat': repeat, 'question': question, 'injection_counts': counts,
                            'checks': checks, 'passed': all(checks.values()), 'events': events,
                            'elapsed_ms': round((time.perf_counter()-start)*1000)}
                    report['cases'].append(case)
                    args.output.parent.mkdir(parents=True, exist_ok=True)
                    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
                    print(json.dumps({k:case[k] for k in ('id', 'repeat', 'checks', 'elapsed_ms')}, ensure_ascii=False), flush=True)
    finally:
        database._DATA_DIR, database.DB_PATH = previous


if __name__ == '__main__':
    main()
