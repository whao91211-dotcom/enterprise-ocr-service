"""种子数据：默认单据模板（invoice 示例）。

字段清单为占位示例，真实字段由业务确认后：
1) 修改本文件并重新执行 python -m app.seed.seed_templates
2) 或通过 POST /api/v1/templates 管理接口维护
"""

from __future__ import annotations

import asyncio
import sys

from sqlalchemy import func, select

from app import models as m
from app.core.config import get_settings
from app.core.db import get_session_factory, init_db

DEFAULT_TEMPLATES: list[dict] = [
    {
        "code": "invoice",
        "name": "发票",
        "field_defs": [
            {"key": "invoice_code", "label": "发票代码", "type": "string", "required": False},
            {"key": "invoice_no", "label": "发票号码", "type": "string", "required": True},
            {"key": "invoice_date", "label": "开票日期", "type": "date", "required": True},
            {"key": "check_code", "label": "校验码", "type": "string", "required": False},
            {"key": "seller", "label": "销售方名称", "type": "string", "required": True},
            {"key": "seller_tax_no", "label": "销售方税号", "type": "string", "required": True},
            {"key": "buyer", "label": "购买方名称", "type": "string", "required": True},
            {"key": "amount", "label": "金额(不含税)", "type": "amount", "required": True},
            {"key": "tax", "label": "税额", "type": "amount", "required": True},
            {"key": "total", "label": "价税合计", "type": "amount", "required": True},
        ],
    },
    {
        "code": "contract",
        "name": "合同",
        "field_defs": [
            {"key": "contract_no", "label": "合同编号", "type": "string", "required": True},
            {"key": "party_a", "label": "甲方", "type": "string", "required": True},
            {"key": "party_b", "label": "乙方", "type": "string", "required": True},
            {"key": "sign_date", "label": "签订日期", "type": "date", "required": False},
            {"key": "amount", "label": "合同金额", "type": "amount", "required": False},
        ],
    },
    {
        "code": "sales",
        "name": "销售消息",
        "row_mode": True,
        "field_defs": [
            {"key": "desc", "label": "顾客公司", "type": "string", "required": True},
            {"key": "item", "label": "货物名称", "type": "string", "required": True},
            {"key": "amount", "label": "货物数量", "type": "number", "required": True},
            {"key": "price", "label": "货物单价", "type": "amount", "required": True},
            {"key": "tax", "label": "货物税率", "type": "amount", "required": False},
            {"key": "sum", "label": "货物金额", "type": "amount", "required": True},
        ],
    },
]


async def seed() -> int:
    """幂等写入默认模板（已存在同名则跳过）。返回新增数量。"""
    init_db()
    async with get_session_factory()() as session:
        created = 0
        for tpl in DEFAULT_TEMPLATES:
            existing = await session.execute(
                select(m.DocTemplate).where(m.DocTemplate.code == tpl["code"])
            )
            if existing.scalar_one_or_none() is None:
                session.add(
                    m.DocTemplate(
                        code=tpl["code"],
                        name=tpl["name"],
                        field_defs=tpl["field_defs"],
                        row_mode=tpl.get("row_mode", False),
                        updated_by="seed",
                    )
                )
                created += 1
        await session.commit()
        return created


async def ensure_default_templates() -> None:
    """应用启动时幂等补种（仅当模板表为空）。"""
    init_db()
    async with get_session_factory()() as session:
        cnt = (await session.execute(select(func.count()).select_from(m.DocTemplate))).scalar_one()
        if cnt == 0:
            await seed()


if __name__ == "__main__":
    n = asyncio.run(seed())
    print(f"seeded {n} template(s), db={get_settings().database_url}")
    sys.exit(0)
