from unittest.mock import patch

import pytest

from evals.task_benchmark import ScriptedModel, call, load_streaming_agent

_REAL_RUNTIME = None


@pytest.fixture
def runtime():
    # Other HTTP tests stub agent.agent; load the real module for this suite.
    import sys
    old = sys.modules.pop('agent.agent', None)
    import agent as package
    previous = getattr(package, 'agent', None)
    if hasattr(package, 'agent'):
        del package.agent
    global _REAL_RUNTIME
    if _REAL_RUNTIME is None:
        _REAL_RUNTIME = load_streaming_agent()
    module = _REAL_RUNTIME
    yield module
    sys.modules.pop('agent.agent', None)
    if old is not None:
        sys.modules['agent.agent'] = old
    if previous is not None:
        package.agent = previous


@pytest.mark.parametrize('names,per_round,maximum', [
    (['missing'], 1, 2), (['missing', 'other'], 1, 4), (['missing'], 3, 8),
])
def test_failed_calls_stop_before_budget(runtime, names, per_round, maximum):
    rounds = [[call(names[i % len(names)], {}, f'{i}-{j}') for j in range(per_round)]
              for i in range(8)]
    with patch.object(runtime, 'get_llm', return_value=ScriptedModel(rounds)):
        events = list(runtime.run_agent_events('测试'))
    assert sum(e['type'] == 'tool_call' for e in events) <= maximum
    assert any(e['type'] == 'answer' for e in events)


def test_successful_calls_have_total_budget(runtime):
    class Good:
        def invoke(self, args):
            return 'ok'
    rounds = [[call('good', {'n': i*3+j}, f'{i}-{j}') for j in range(3)] for i in range(8)]
    with patch.dict(runtime._TOOL_BY_NAME, {'good': Good()}), \
         patch.object(runtime, 'get_llm', return_value=ScriptedModel(rounds)):
        events = list(runtime.run_agent_events('测试'))
    assert sum(e['type'] == 'tool_call' for e in events) == 8


def test_timeout_returns_without_waiting_for_underlying_work(runtime):
    import time
    import config
    class Slow:
        def invoke(self, args):
            time.sleep(.3)
            return 'ok'
    with patch.dict(runtime._TOOL_BY_NAME, {'slow': Slow()}), \
         patch.object(config, 'AGENT_TOOL_TIMEOUT_SECONDS', .02, create=True), \
         patch.object(runtime, 'get_llm', return_value=ScriptedModel([[call('slow', {})]])):
        start = time.perf_counter()
        events = list(runtime.run_agent_events('测试'))
    assert time.perf_counter()-start < .2
    assert '超时' in events[-1]['answer']
    assert sum(e['type'] == 'tool_call' for e in events) == 1


def test_failed_write_is_not_retried(runtime):
    class Broken:
        def invoke(self, args):
            raise RuntimeError('write status uncertain')
    with patch.dict(runtime._TOOL_BY_NAME, {'correct_confirm': Broken()}), \
         patch.object(runtime, 'get_llm', return_value=ScriptedModel(
             [[call('correct_confirm', {'file_name': 'a.png'})]] * 8)):
        events = list(runtime.run_agent_events('确认单据'))
    assert sum(e['type'] == 'tool_call' for e in events) == 1
    assert '核对状态' in events[-1]['answer']


def test_model_timeout_is_reported(runtime):
    import time
    import config
    class SlowModel(ScriptedModel):
        def invoke(self, messages):
            time.sleep(.3)
    with patch.object(config, 'AGENT_MODEL_TIMEOUT_SECONDS', .02, create=True), \
         patch.object(runtime, 'get_llm', return_value=SlowModel()):
        events = list(runtime.run_agent_events('测试'))
    assert '超时' in events[-1]['answer']


def test_timeout_slots_are_retained_then_released(monkeypatch):
    import time
    from threading import BoundedSemaphore, Event
    from agent import runtime_guard
    slots = BoundedSemaphore(4)
    release = Event()
    monkeypatch.setattr(runtime_guard, '_INFLIGHT', slots)
    try:
        for _ in range(4):
            with pytest.raises(runtime_guard.CallTimeout):
                runtime_guard.bounded_call(lambda: release.wait(2), .01)
        with pytest.raises(RuntimeError, match='仍有调用'):
            runtime_guard.bounded_call(lambda: 'must not start', .01)
    finally:
        release.set()
        deadline = time.monotonic()+1
        while slots._value != 4 and time.monotonic() < deadline:
            time.sleep(.005)
    assert slots._value == 4
    assert runtime_guard.bounded_call(lambda: 'recovered', .1) == 'recovered'


def test_fabricated_report_claim_requires_real_tool_result(runtime, tmp_path):
    from langchain_core.messages import AIMessage
    path = tmp_path/'real-report.docx'

    class Model(ScriptedModel):
        def invoke(self, messages):
            self.steps += 1
            if self.steps == 1:
                return AIMessage(content='报告已生成: C:/fabricated/report.docx')
            if self.steps == 2:
                return AIMessage(content='', tool_calls=[call('generate_report', {}, 'create')])
            return AIMessage(content=f'报告已生成: {path}')

    class Report:
        def invoke(self, arguments):
            path.write_bytes(b'created-by-tool')
            return f'报告已生成: {path}（共 1 张单据, 1 行记录）'

    with patch.object(runtime, 'get_llm', return_value=Model()), patch.dict(runtime._TOOL_BY_NAME, {'generate_report': Report()}):
        events = list(runtime.run_agent_events('生成Word报告'))
    assert path.is_file()
    assert sum(e['type'] == 'tool_call' for e in events) == 1
    assert 'C:/fabricated/report.docx' not in events[-1]['answer']


@pytest.mark.parametrize('claim', ['报告已生成: C:/fabricated/report.docx', '2024年销售额柱状图已生成', '统计饼图已生成'])
def test_repeated_unverified_file_claim_stops_without_false_success(runtime, claim):
    from langchain_core.messages import AIMessage

    class Model(ScriptedModel):
        def invoke(self, messages):
            self.steps += 1
            return AIMessage(content=claim)

    model = Model()
    with patch.object(runtime, 'get_llm', return_value=model):
        events = list(runtime.run_agent_events('生成Word报告'))
    assert model.steps == 2
    assert '未生成' in events[-1]['answer']
    assert 'fabricated' not in events[-1]['answer']


def test_read_parameter_can_be_corrected(runtime):
    class Query:
        def invoke(self, args):
            if args.get('group_by') == 'invalid':
                return 'group_by 仅支持: item'
            return '（没有符合条件的已确认数据）'
    model = ScriptedModel([[call('rag_summarize', {'group_by': 'invalid'}, 'a')],
                           [call('rag_summarize', {'group_by': 'item'}, 'b')]])
    with patch.dict(runtime._TOOL_BY_NAME, {'rag_summarize': Query()}), \
         patch.object(runtime, 'get_llm', return_value=model):
        events = list(runtime.run_agent_events('统计'))
    assert events[-1]['answer'] == '替身回答'
    assert sum(e['type'] == 'tool_call' for e in events) == 2


def test_ocr_uses_existing_longer_budget(runtime):
    import time
    class OCR:
        def invoke(self, args):
            time.sleep(.03)
            return '识别成功'
    with patch.dict(runtime._TOOL_BY_NAME, {'ocr_recognize_chat': OCR()}), \
         patch.object(runtime.config, 'AGENT_TOOL_TIMEOUT_SECONDS', .005), \
         patch.object(runtime, 'get_llm', return_value=ScriptedModel([[call('ocr_recognize_chat', {})]])):
        events = list(runtime.run_agent_events('识别'))
    assert events[-1]['answer'] == '替身回答'


def test_update_claim_without_tool_is_corrected_before_return(runtime):
    from langchain_core.messages import AIMessage

    class Model(ScriptedModel):
        def invoke(self, messages):
            self.steps += 1
            if self.steps == 1:
                return AIMessage(content='已按你的要求修改行 ID 1，未确认。')
            if self.steps == 2:
                return AIMessage(content='', tool_calls=[call('correct_update_row',
                    {'row_id': 1, 'fields': '{"sum":"150"}'}, 'update')])
            return AIMessage(content='行 #1 已更新，未确认。')

    class Update:
        def invoke(self, args):
            return '✅ 行 #1 已更新: {"sum":"150"}'

    with patch.object(runtime, 'get_llm', return_value=Model()), \
         patch.dict(runtime._TOOL_BY_NAME, {'correct_update_row': Update()}):
        events = list(runtime.run_agent_events('请修改行1的sum为150，暂时不要确认。'))
    assert sum(e['type']=='tool_call' for e in events) == 1


def test_repeated_update_claim_without_tool_stops(runtime):
    from langchain_core.messages import AIMessage

    class Model(ScriptedModel):
        def invoke(self, messages):
            self.steps += 1
            return AIMessage(content='已按你的要求修改行 ID 1。')

    model = Model()
    with patch.object(runtime, 'get_llm', return_value=model):
        events = list(runtime.run_agent_events('请修改行1的sum为150。'))
    assert model.steps == 2
    assert '未修改' in events[-1]['answer']
