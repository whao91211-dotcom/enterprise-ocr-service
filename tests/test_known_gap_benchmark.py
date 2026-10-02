from evals.known_gap_benchmark import score


def answer(text):
    return [{'type': 'answer', 'answer': text}]


def test_no_match_rejects_zero_claim_but_accepts_uncertainty():
    goal = {'kind': 'no_match'}
    assert not all(score(answer('没有已确认数据，销售额为0。'), goal).values())
    assert all(score(answer('没有符合条件的已确认记录，无法确定实际销售额。'), goal).values())
    assert all(score(answer('没有匹配记录，不能认定实际销售额为0。'), goal).values())


def test_truncated_context_requires_clarification_not_old_query():
    goal = {'kind': 'clarify'}
    assert all(score(answer('请补充刚才查询的年份，我无法确定。'), goal).values())
    events = [{'type': 'tool_call', 'name': 'rag_summarize', 'args': {'year': '2024'}}]
    assert not all(score(events + answer('2024年销售额200'), goal).values())


def test_latest_query_requires_new_tool_result_not_cached_correct_number():
    goal = {'kind': 'query', 'filters': {'year': '2024', 'keyword': '甲公司'}, 'total': '150'}
    assert not all(score(answer('最新销售额150'), goal).values())
    events = [{'type': 'tool_call', 'name': 'rag_summarize', 'args': goal['filters']},
              {'type': 'tool_result', 'name': 'rag_summarize', 'result': '{"summary":{"total_sum":100}}'}]
    assert not all(score(events + answer('销售额150'), goal).values())
    events[-1]['result'] = '{"summary":{"total_sum":150}}'
    assert all(score(events + answer('销售额150'), goal).values())
