"""Offline SQL/context/runtime baseline, with live-task specifications kept separate."""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
import time
from decimal import Decimal
from pathlib import Path
from types import ModuleType
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def matches_expected(result, expected):
    if expected['matched_rows'] == 0:
        return isinstance(result, str) and result in (
            '（没有符合条件的已确认数据）', '（未检索到匹配的已确认数据）')
    if not isinstance(result, dict):
        return False
    summary = result.get('summary', {})
    return (summary.get('total_rows') == expected['matched_rows']
            and Decimal(str(summary.get('total_sum', 'NaN'))) == Decimal(expected['total_sum']))


def load_streaming_agent():
    # Legacy ask/build_agent imports unrelated Transformers dependencies.
    # This suite exercises only the real run_agent_events function and tools.
    legacy = ModuleType('langchain_classic.agents')
    def unused(*args, **kwargs):
        raise RuntimeError('Legacy executor is outside this streaming benchmark')
    legacy.AgentExecutor = unused
    legacy.create_tool_calling_agent = unused
    with patch.dict(sys.modules, {'langchain_classic.agents': legacy}):
        from agent import agent
    return agent


class ScriptedModel:
    """Only controls test stimuli; never scores real model decisions."""
    def __init__(self, calls=None):
        self.calls = calls or []
        self.messages = []
        self.steps = 0

    def bind_tools(self, tools):
        return self

    def invoke(self, messages):
        from langchain_core.messages import AIMessage
        self.messages.append(list(messages))
        self.steps += 1
        calls = self.calls[self.steps - 1] if self.steps <= len(self.calls) else []
        return AIMessage(content='' if calls else '替身回答', tool_calls=calls)


def blocking_worker(kind, connection):
    agent = load_streaming_agent()
    class BlockingModel(ScriptedModel):
        def invoke(self, messages):
            connection.send('blocking-call-started')
            time.sleep(3600)
    class BlockingTool:
        def invoke(self, arguments):
            connection.send('blocking-call-started')
            time.sleep(3600)
    try:
        model = BlockingModel() if kind == 'model' else ScriptedModel([[call('blocking', {})]])
        with patch.object(agent, 'get_llm', return_value=model), \
             patch.dict(agent._TOOL_BY_NAME, {'blocking': BlockingTool()}):
            list(agent.run_agent_events('超时测试'))
        connection.send('agent-returned')
    except Exception as exc:
        connection.send({'error': type(exc).__name__})
    finally:
        connection.close()


def timeout_probes():
    import multiprocessing
    context = multiprocessing.get_context('spawn')
    workers, results = [], []
    try:
        for kind in ('tool', 'model'):
            receiver, sender = context.Pipe(duplex=False)
            process = context.Process(target=blocking_worker, args=(kind, sender))
            process.start()
            sender.close()
            workers.append((kind, receiver, process))
        for kind, receiver, process in workers:
            if not receiver.poll(45):
                results.append((kind, 'not_run', {'reason': 'worker initialization timeout'}))
                continue
            message = receiver.recv()
            if message != 'blocking-call-started':
                results.append((kind, 'not_run', {'initialization_result': message}))
                continue
            started = time.perf_counter()
            returned = receiver.poll(2)
            evidence = {'injected_call_budget_seconds': 2,
                        'observation_seconds': round(time.perf_counter()-started, 3),
                        'agent_returned': returned,
                        'terminated_by_evaluation_harness': not returned,
                        'note': '2-second diagnostic budget; production thresholds not selected'}
            if returned:
                evidence['result'] = receiver.recv()
            results.append((kind, 'pass' if returned else 'fail', evidence))
    finally:
        for _, receiver, process in workers:
            if process.is_alive():
                process.terminate()
            process.join(5)
            receiver.close()
    return results


def call(name, args, ident='call-1'):
    return {'name': name, 'args': args, 'id': ident}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--dataset', type=Path, default=Path('data/benchmark_v1'))
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    from db import crud, database
    agent = load_streaming_agent()
    from tools import ocr_client
    from tools.rag_tool import rag_summarize

    fixture = json.loads((args.dataset / 'agent_fixture.json').read_text(encoding='utf-8'))
    tasks = json.loads((args.dataset / 'agent_tasks.json').read_text(encoding='utf-8'))['cases']
    results, specs = [], []

    def record(case_id, category, passed, evidence):
        results.append({'case_id': case_id, 'category': category,
                        'status': 'pass' if passed else 'fail', 'evidence': evidence})

    def specification(case_id, category, question, acceptance, **kwargs):
        specs.append({'case_id': case_id, 'category': category, 'question': question,
                      'acceptance': acceptance, **kwargs})

    def run_script(model, question='统计销售额', history=None, preferences=None):
        with patch.object(agent, 'get_llm', return_value=model):
            return list(agent.run_agent_events(question, history, preferences=preferences))

    started = time.perf_counter()
    previous = database._DATA_DIR, database.DB_PATH
    try:
        with tempfile.TemporaryDirectory(prefix='agent_task_benchmark_') as tmp:
            database._DATA_DIR = Path(tmp)
            database.DB_PATH = Path(tmp) / 'agent.db'
            database.init_db()
            for sample in fixture['samples']:
                doc = crud.upsert_document(sample['image_name'], sample['image_sha256'])
                crud.insert_rows(doc, [r['values'] for r in sample['rows']])
                crud.confirm_doc(doc, 'benchmark-fixture')
            pending = crud.upsert_document('unconfirmed-control.png', None)
            crud.insert_rows(pending, [{'item': 'pending-only', 'sum': '999999999'}])
            for task in tasks:
                expected = task['expected']
                raw = rag_summarize.invoke({'keyword': expected['keyword']})
                try:
                    parsed = json.loads(raw)
                except json.JSONDecodeError:
                    parsed = raw
                record(task['case_id'], 'sql_oracle', matches_expected(parsed, expected),
                       {'expected': expected, 'tool_output': parsed})
                specs.append({**task, 'category': 'normal_query', 'live_status': 'not_run'})

            context_specs = [
                ('context-followup', '那2025年呢？', '应继承上一轮筛选对象，并改为2025年'),
                ('context-recent', '继续刚才的统计', '应使用最近轮次中的公司和年份'),
                ('context-old', '按最早指定的2024年继续', '需要最早消息；缺失时澄清，不猜年份'),
                ('preference-available', '统计销售额', '跨会话偏好应可用并被遵循'),
                ('preference-override', '这次用详细中文回答', '当前要求应覆盖已保存的简洁偏好'),
            ]
            for ident, question, acceptance in context_specs:
                specification(ident, 'context', question, acceptance)
            from langchain_core.messages import HumanMessage, SystemMessage
            model = ScriptedModel()
            prior = [{'role': 'user', 'content': '统计甲公司2024年的金额'},
                     {'role': 'assistant', 'content': '示例上一轮回答'}]
            run_script(model, '那2025年呢？', prior)
            record('context-followup', 'context_delivery',
                   any(isinstance(m, HumanMessage) and m.content == prior[0]['content']
                       for m in model.messages[0]), '检查历史传递，不评模型继承参数')
            history = [{'role': 'user' if i % 2 == 0 else 'assistant',
                        'content': f'轮次{i}'} for i in range(14)]
            history[0]['content'] = 'required-old-year=2024'
            model = ScriptedModel()
            run_script(model, '继续', history)
            contents = [m.content for m in model.messages[0]]
            record('context-recent', 'context_delivery', '轮次12' in contents, '最近消息传入')
            record('context-old', 'context_delivery', 'required-old-year=2024' in contents,
                   '14条历史截取最近10条，最早的必要年份丢失')
            model = ScriptedModel()
            run_script(model, '这次用详细中文回答', preferences=['回答简洁'])
            system = next(m.content for m in model.messages[0] if isinstance(m, SystemMessage))
            record('preference-available', 'context_delivery', '回答简洁' in system,
                   '只验证偏好注入；未验证模型遵循或跨会话存取')
            record('preference-override', 'context_delivery', '以当前请求为准' in system,
                   '只验证优先级规则注入，不等同于实际遵循')

            fault_specs = [
                ('ocr-offline', '识别图片', '服务离线应说明失败，不能写入未完成结果'),
                ('tool-exception', '统计金额', '异常应转成可处理错误，不使循环崩溃'),
                ('invalid-parameters', '按错误维度统计', '错误应可见；真实模型应修正或解释'),
                ('unknown-tool', '调用不存在工具', '未知工具应返回错误，不使循环崩溃'),
                ('malformed-arguments', '传入错误参数类型', '参数校验错误应可见，不使循环崩溃'),
                ('repeated-failure', '反复调用同一失败工具', '相同失败调用最多2次，应停止或改变策略'),
                ('alternating-failure', '交替调用两个失败工具', '无进展时应在4次调用内结束'),
                ('tool-budget', '每轮请求多个工具', '单个任务工具调用总数应不超过8次'),
                ('tool-timeout', '工具一直不返回', '应遵循调用超时预算；离线诊断使用2秒，生产阈值待选型'),
                ('model-timeout', '模型一直不返回', '应遵循调用超时预算；离线诊断使用2秒，生产阈值待选型'),
            ]
            for ident, question, acceptance in fault_specs:
                specification(ident, 'fault', question, acceptance,
                              thresholds_status='benchmark target, not implemented guarantee')
            image = Path(tmp) / 'offline.png'
            image.write_bytes(b'synthetic-image')
            before = crud.confirmed_count()
            with patch.object(ocr_client, 'recognize', side_effect=ConnectionError('injected offline')):
                output = agent._run_tool_call(call('ocr_recognize_chat', {'image_path': str(image)}))
            record('ocr-offline', 'runtime_guard', '识别失败' in output
                   and crud.get_document_by_name(image.name) is None
                   and crud.confirmed_count() == before, output)
            class BrokenTool:
                def invoke(self, args):
                    raise RuntimeError('injected tool failure')
            with patch.dict(agent._TOOL_BY_NAME, {'rag_summarize': BrokenTool()}):
                output = agent._run_tool_call(call('rag_summarize', {}))
                record('tool-exception', 'runtime_guard', '工具执行出错' in output, output)
            output = agent._run_tool_call(call('rag_summarize', {'group_by': 'invalid'}))
            record('invalid-parameters', 'runtime_guard', '仅支持' in output, output)
            output = agent._run_tool_call(call('nonexistent', {}))
            record('unknown-tool', 'runtime_guard', '未知工具' in output, output)
            output = agent._run_tool_call(call('rag_summarize', {'group_by': ['item']}))
            record('malformed-arguments', 'runtime_guard', '工具执行出错' in output, output)
            for ident, names, per_round, maximum in (
                ('repeated-failure', ['nonexistent'], 1, 2),
                ('alternating-failure', ['nonexistent', 'missing'], 1, 4),
                ('tool-budget', ['nonexistent'], 3, 8),
            ):
                rounds = [[call(names[i % len(names)], {}, f'call-{i}-{j}')
                           for j in range(per_round)] for i in range(8)]
                events = run_script(ScriptedModel(rounds))
                count = sum(e['type'] == 'tool_call' for e in events)
                record(ident, 'runtime_guard', count <= maximum,
                       {'observed_tool_calls': count, 'target_max': maximum,
                        'terminated_by_round_cap': any('次数过多' in e.get('answer', '') for e in events),
                        'stimulus': 'scripted model keeps requesting failed tools'})
            for kind, status, evidence in timeout_probes():
                results.append({'case_id': f'{kind}-timeout', 'category': 'runtime_guard',
                                'status': status, 'evidence': evidence})
    finally:
        database._DATA_DIR, database.DB_PATH = previous
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / 'cases.json').write_text(json.dumps(specs, ensure_ascii=False, indent=2), encoding='utf-8')
    report = {'mode': 'offline', 'fixture_images': len(fixture['samples']),
              'fixture_rows': sum(len(s['rows']) for s in fixture['samples']),
              'specification_cases': len(specs), 'elapsed_seconds': round(time.perf_counter()-started, 3),
              'label_acceptance': 'User accepted supplied invoice labels as-is; no automatic recalculation',
              'categories': {}, 'cases': results,
              'limitations': ['No real LLM/OCR requests; not Agent task success rate',
                             'Unused legacy AgentExecutor imports bypassed; real streaming loop and SQL tools used',
                             'Context delivery is not preference adherence or correct reasoning',
                             'Runtime guards use scripted tool requests, not real model recovery',
                             'Timeout probes use a 2-second diagnostic budget; service SDK behavior is not tested',
                             'Fixture is a small deterministic subset, not a representative benchmark',
                             'No quality improvements, actual tokens, API cost or model latency measured']}
    for category in sorted({r['category'] for r in results}):
        subset = [r for r in results if r['category'] == category]
        report['categories'][category] = {status: sum(r['status'] == status for r in subset)
                                          for status in ('pass', 'fail', 'not_run')}
    (args.output / 'offline-results.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({k: v for k, v in report.items() if k not in ('cases', 'limitations')}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
