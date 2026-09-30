from evals.task_benchmark import matches_expected


def test_numeric_oracle_does_not_accept_wrong_or_partial_totals():
    expected = {'matched_rows': 30, 'total_sum': '1234.50'}
    assert matches_expected({'summary': {'total_rows': 30, 'total_sum': 1234.5}}, expected)
    assert not matches_expected({'summary': {'total_rows': 20, 'total_sum': 1234.5}}, expected)
    assert not matches_expected({'summary': {'total_rows': 30, 'total_sum': 1234}}, expected)


def test_no_match_requires_empty_result_not_tool_error():
    expected = {'matched_rows': 0}
    assert matches_expected('（没有符合条件的已确认数据）', expected)
    assert not matches_expected('工具执行出错: 服务不可用', expected)
