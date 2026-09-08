"""
领域模板 API（v2）— 模板列表/CRUD/复制/预览（docs/10 §4.2 领域配置页）。

约定：
- 内置预设（is_preset=True）只读、可复制，不可编辑/删除（DomainValidationError → 400）；
- 删除仍被 KB 引用的模板 → 409（DomainInUseError）；
- 模板编辑/复制不影响存量 KB 绑定；编辑内容变化会联动失效引用 KB 的图谱状态
  （由 services.domain_service.update_profile 处理）。
"""
from fastapi import APIRouter, HTTPException, Response

from models.schemas import (
    DomainPreviewRequest,
    DomainProfileCreate,
    DomainProfileUpdate,
)
from services import domain_service
from utils.logging import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/api/domains", tags=["领域模板"])


def _list_item(p: dict) -> dict:
    return {
        "id": p["id"],
        "name": p["name"],
        "description": p.get("description", ""),
        "is_preset": p["is_preset"],
        "entity_count": len(p["entity_meta"]),
        "relation_count": len(p["relation_meta"]),
    }


def _detail(p: dict, used_by_kbs: int = 0) -> dict:
    meta = domain_service.profile_meta(p)
    meta["entity_count"] = len(p["entity_meta"])
    meta["relation_count"] = len(p["relation_meta"])
    meta["used_by_kbs"] = used_by_kbs
    return meta


@router.get("")
async def list_domains():
    """领域模板列表（预设在前）。"""
    profiles = await domain_service.list_profiles()
    return {"items": [_list_item(p) for p in profiles], "total": len(profiles)}


@router.post("/preview")
async def preview_domain(body: DomainPreviewRequest):
    """配置页实时校验：返回错误列表；全部通过时附生成提示词。"""
    entity_types = [e.model_dump() for e in body.entity_types]
    relation_types = [r.model_dump() for r in body.relation_types]
    errors = domain_service.validate_profile(
        "预览", entity_types, relation_types
    )
    return {
        "valid": not errors,
        "errors": errors,
        "prompt": (
            domain_service.build_extraction_prompt(entity_types, relation_types)
            if not errors else ""
        ),
        "entity_count": len(entity_types),
        "relation_count": len(relation_types),
    }


@router.post("", status_code=201)
async def create_domain(body: DomainProfileCreate):
    """新建自定义领域模板。"""
    try:
        p = await domain_service.create_profile(
            name=body.name,
            description=body.description,
            entity_types=[e.model_dump() for e in body.entity_types],
            relation_types=[r.model_dump() for r in body.relation_types],
        )
    except domain_service.DomainValidationError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return _detail(p)


@router.get("/{domain_id}")
async def get_domain(domain_id: str):
    """模板详情（渲染 meta + 引用 KB 数）。"""
    try:
        p = await domain_service.get_profile(domain_id)
        used = await domain_service.count_profile_refs(domain_id)
    except ValueError:
        p = None
        used = 0
    if p is None:
        raise HTTPException(status_code=404, detail="领域模板不存在")
    return _detail(p, used_by_kbs=used)


@router.put("/{domain_id}")
async def update_domain(domain_id: str, body: DomainProfileUpdate):
    """整包更新自定义模板（内置预设 → 400）。"""
    try:
        p = await domain_service.update_profile(
            profile_id=domain_id,
            name=body.name,
            description=body.description,
            entity_types=[e.model_dump() for e in body.entity_types],
            relation_types=[r.model_dump() for r in body.relation_types],
        )
    except domain_service.DomainValidationError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except ValueError:
        p = None
    if p is None:
        raise HTTPException(status_code=404, detail="领域模板不存在")
    return _detail(p)


@router.delete("/{domain_id}", status_code=204)
async def delete_domain(domain_id: str):
    """删除自定义模板（仍有 KB 引用 → 409）。"""
    try:
        ok = await domain_service.delete_profile(domain_id)
    except domain_service.DomainInUseError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except domain_service.DomainValidationError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except ValueError:
        ok = False
    if not ok:
        raise HTTPException(status_code=404, detail="领域模板不存在")
    return Response(status_code=204)


@router.post("/{domain_id}/copy", status_code=201)
async def copy_domain(domain_id: str):
    """复制模板为可编辑副本（预设同样适用）。"""
    try:
        p = await domain_service.copy_profile(domain_id)
    except domain_service.DomainValidationError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except ValueError:
        p = None
    if p is None:
        raise HTTPException(status_code=404, detail="领域模板不存在")
    return _detail(p)
