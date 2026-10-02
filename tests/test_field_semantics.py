from evals.answer_checks import field_label_errors


def test_amount_money_label_is_detected():
    assert field_label_errors('| 合计金额(amount) | 1.00 |')
    assert field_label_errors('total_amount（金额合计）：1')
    assert not field_label_errors('| 数量合计(amount) | 1 |\n| 金额(sum) | 100 |')
    assert not field_label_errors('销售额合计100.00')
