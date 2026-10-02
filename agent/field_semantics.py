"""Shared business vocabulary; original storage keys remain unchanged."""
FIELD_MEANINGS = {'desc': '顾客公司', 'date': '发注日', 'from': '源公司',
                  'item': '商品或项目', 'amount': '数量', 'price': '单价',
                  'tax': '税率', 'sum': '票面金额'}
SUMMARY_MEANINGS = {'total_rows': '明细行数（不是单据数）',
                    'total_amount': '数量合计（不是金额）',
                    'total_sum': '票面金额合计'}
FIELD_RULES = '''
业务字段规则：amount=数量，price=单价，tax=税率，sum=票面金额。
统计结果 total_amount=数量合计，total_sum=金额合计，total_rows=明细行数，不是单据数。
不得把 amount/total_amount 标为金额；金额应读取 sum/total_sum。
以已确认票面金额为准，不能用数量乘单价覆盖金额，票面可能有折扣。
仅在原文明确提供币种时标注币种，不推断货币单位。
'''
