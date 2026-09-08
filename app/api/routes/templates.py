"""单据模板路由（结构化字段定义管理）。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import models as m
from app.api.deps import CurrentUser, get_current_user
from app.core.db import get_session
from app.schemas import TemplateIn, TemplateOut

router = APIRouter(tags=["templates"])


def _to_out(t: m.DocTemplate) -> TemplateOut:
    return TemplateOut(
        code=t.code,
        name=t.name,
        field_defs=t.field_defs or [],
        active=t.active,
        updated_by=t.updated_by,
        updated_at=t.updated_at,
    )


@router.get("/templates", response_model=list[TemplateOut])
async def list_templates(
    include_inactive: bool = False,
    session: AsyncSession = Depends(get_session),
):
    res = await session.execute(
        select(m.DocTemplate).order_by(m.DocTemplate.code)
    )
    return [
        _to_out(t) for t in res.scalars().all() if include_inactive or t.active
    ]


@router.get("/templates/{code}", response_model=TemplateOut)
async def get_template(code: str, session: AsyncSession = Depends(get_session)):
    t = await m.get_template(session, code)
    if t is None:
        raise HTTPException(status_code=404, detail="模板不存在")
    return _to_out(t)


@router.post("/templates", response_model=TemplateOut, status_code=201)
async def create_template(
    body: TemplateIn,
    session: AsyncSession = Depends(get_session),
    user: CurrentUser = Depends(get_current_user),
):
    existing = await m.get_template(session, body.code)
    if existing is not None:
        raise HTTPException(status_code=409, detail=f"模板已存在: {body.code}")
    t = m.DocTemplate(
        code=body.code,
        name=body.name,
        field_defs=[f.model_dump() for f in body.field_defs],
        active=body.active,
        updated_by=user.id,
    )
    session.add(t)
    await session.commit()
    await session.refresh(t)
    return _to_out(t)


@router.put("/templates/{code}", response_model=TemplateOut)
async def update_template(
    code: str,
    body: TemplateIn,
    session: AsyncSession = Depends(get_session),
    user: CurrentUser = Depends(get_current_user),
):
    t = await m.get_template(session, code)
    if t is None:
        raise HTTPException(status_code=404, detail="模板不存在")
    if body.code != code:
        raise HTTPException(status_code=422, detail="路径 code 与 body.code 不一致")
    t.name = body.name
    t.field_defs = [f.model_dump() for f in body.field_defs]
    t.active = body.active
    t.updated_by = user.id
    await session.commit()
    await session.refresh(t)
    return _to_out(t)
