from evals.context_preference_probe import assess


def test_rejects_old_filters_even_when_answer_mentions_correct_total():
    events = [{'type': 'tool_call', 'name': 'rag_summarize', 'args': {'year': '2024'}},
              {'type': 'answer', 'answer': '销售额900'}]
    checks = assess(events, {'filters': {'year': '2025'}, 'total': '900'})
    assert checks['answer_contains_total']
    assert not checks['query_filters']


def test_total_check_does_not_match_a_different_number():
    events = [{'type': 'tool_call', 'name': 'rag_summarize', 'args': {}},
              {'type': 'answer', 'answer': '销售额11200'}]
    assert not assess(events, {'filters': {}, 'total': '1200'})['answer_contains_total']
    events[-1]['answer'] = '销售额1,200.00'
    assert assess(events, {'filters': {}, 'total': '1200'})['answer_contains_total']


def test_prefix_and_no_tool_checks_reject_unwanted_behaviour():
    events = [{'type': 'tool_call', 'name': 'rag_query', 'args': {}},
              {'type': 'answer', 'answer': '旧标题：统计结果100'}]
    checks = assess(events, {'no_tools': True, 'prefix': '统计结果'})
    assert not checks['no_tools']
    assert not checks['answer_prefix']


def test_error_is_not_a_completed_answer():
    checks = assess([{'type': 'answer', 'answer': '收到'}, {'type': 'error'}], {})
    assert not checks['completed']
