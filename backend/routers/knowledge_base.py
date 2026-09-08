"""
知识库管理 API。
"""
from fastapi import APIRouter, HTTPException

from models.schemas import (
    DomainBindRequest,
    KBModelBindingSet,
    KnowledgeBaseCreate,
    KnowledgeBaseResponse,
)
from services import domain_service, kb_service, model_service
from utils.logging import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/api/knowledge-bases", tags=["知识库管理"])


@router.post("", response_model=KnowledgeBaseResponse, status_code=201)
async def create_knowledge_base(body: KnowledgeBaseCreate):
    """创建知识库（v2：可指定领域模板，省略默认内置『通用』预设）"""
    logger.info("创建知识库: name=%s, domain=%s", body.name, body.domain_profile_id)
    # 显式指定模板时校验存在（无外键，应用层保证引用有效）
    if body.domain_profile_id:
        try:
            profile = await domain_service.get_profile(body.domain_profile_id)
        except ValueError:
            profile = None
        if profile is None:
            raise HTTPException(status_code=404, detail="领域模板不存在")
    try:
        result = await kb_service.create_knowledge_base(
            name=body.name,
            description=body.description,
            domain_profile_id=body.domain_profile_id,
        )
        logger.info("知识库创建成功: id=%s", result.get("id"))
        return result
    except Exception as e:
        logger.error("创建知识库失败: %s", str(e), exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("", response_model=list[KnowledgeBaseResponse])
async def list_knowledge_bases():
    """获取知识库列表"""
    logger.debug("获取知识库列表")
    return await kb_service.list_knowledge_bases()


@router.get("/{kb_id}", response_model=KnowledgeBaseResponse)
async def get_knowledge_base(kb_id: str):
    """获取知识库详情"""
    logger.debug("获取知识库详情: kb_id=%s", kb_id)
    result = await kb_service.get_knowledge_base(kb_id)
    if result is None:
        logger.warning("知识库不存在: kb_id=%s", kb_id)
        raise HTTPException(status_code=404, detail="知识库不存在")
    return result


@router.post("/{kb_id}/delete", status_code=204)
async def delete_knowledge_base(kb_id: str):
    """删除知识库及其下所有文档和向量数据"""
    logger.info("删除知识库: kb_id=%s", kb_id)
    success = await kb_service.delete_knowledge_base(kb_id)
    if not success:
        logger.warning("删除失败，知识库不存在: kb_id=%s", kb_id)
        raise HTTPException(status_code=404, detail="知识库不存在")
    logger.info("知识库已删除: kb_id=%s", kb_id)


@router.get("/{kb_id}/models")
async def get_kb_models(kb_id: str):
    """获取知识库各类型模型的绑定与最终生效配置"""
    try:
        return await model_service.get_kb_models(kb_id)
    except Exception as e:
        logger.error("获取知识库模型失败: %s", str(e), exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/{kb_id}/models/{model_type}")
async def bind_kb_model(kb_id: str, model_type: str, body: KBModelBindingSet):
    """绑定/解绑知识库模型（Embedding 有文档时锁定）"""
    try:
        return await model_service.bind_model(kb_id, model_type, body.model_config_id)
    except ValueError as e:
        msg = str(e)
        if "锁定" in msg:
            raise HTTPException(status_code=409, detail=msg)
        if "不存在" in msg:
            raise HTTPException(status_code=404, detail=msg)
        raise HTTPException(status_code=400, detail=msg)


# ============================================================
# 领域模板绑定（v2，docs/10 §4.1 KB 级配置）
# ============================================================

def _domain_view(kb_id: str, bound_id: str | None, profile: dict) -> dict:
    return {
        "kb_id": kb_id,
        "bound_profile_id": bound_id,
        "effective_profile_id": profile["id"],
        "profile": domain_service.profile_meta(profile),
    }


@router.get("/{kb_id}/domain")
async def get_kb_domain(kb_id: str):
    """当前绑定模板与生效模板 meta（未绑定/绑定失效时生效为内置『通用』）。"""
    kb = await kb_service.get_knowledge_base(kb_id)
    if kb is None:
        raise HTTPException(status_code=404, detail="知识库不存在")
    bound = kb.get("domain_profile_id")
    profile = await domain_service.get_profile_for_kb(kb_id)
    return _domain_view(kb_id, bound, profile)


@router.put("/{kb_id}/domain")
async def bind_kb_domain(kb_id: str, body: DomainBindRequest):
    """换绑领域模板（本体变化 → 图谱状态失效，需重建）。"""
    kb = await kb_service.get_knowledge_base(kb_id)
    if kb is None:
        raise HTTPException(status_code=404, detail="知识库不存在")
    try:
        profile = await domain_service.get_profile(body.domain_profile_id)
    except ValueError:
        profile = None
    if profile is None:
        raise HTTPException(status_code=404, detail="领域模板不存在")
    effective = await domain_service.bind_kb_profile(kb_id, body.domain_profile_id)
    if effective is None:
        raise HTTPException(status_code=404, detail="知识库不存在")
    return _domain_view(kb_id, body.domain_profile_id, effective)
