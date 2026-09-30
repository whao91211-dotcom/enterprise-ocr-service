from evals.prepare_benchmark import normalize_row, inspect_rows


def test_normalization_keeps_discounted_amount_and_blank_tax():
    raw = ['output_image\\Sample1.png', '公司', '2024-04-29 00:00:00',
           '供货商', '商品', 10, '¥2,740.00', None, 27208]
    row = normalize_row(raw, 15)
    assert row['image_name'] == 'Sample1.png'
    assert row['values']['date'] == '2024-04-29'
    assert row['values']['price'] == '2740'
    assert row['values']['sum'] == '27208'
    assert row['values']['tax'] is None
    issues = inspect_rows([row, normalize_row(raw, 16)])
    assert any(i['code'] == 'amount_requires_image_check' for i in issues)
    assert any(i['code'] == 'repeated_row_preserved' for i in issues)
    assert len([i for i in issues if i['code'] == 'blank_field']) == 2


def test_invalid_numeric_value_is_retained_and_flagged():
    raw = ['Sample2.png', '公司', 'bad-date', '供货商', '商品',
           '未知', 10, 0, None]
    row = normalize_row(raw, 20)
    assert row['values']['amount'] == '未知'
    assert row['values']['sum'] is None
    issues = inspect_rows([row])
    assert any(i['code'] == 'invalid_number' for i in issues)
    assert any(i['code'] == 'invalid_date' for i in issues)


def test_chinese_date_normalizes_without_changing_source():
    raw = ['Sample3.png', '公司', '2021年04月27日', '供货商', '商品', 1, 10, '10%', 10]
    row = normalize_row(raw, 21)
    assert row['values']['date'] == '2021-04-27'
    assert row['values']['tax'] == '0.1'
    assert row['raw'][2] == '2021年04月27日'
