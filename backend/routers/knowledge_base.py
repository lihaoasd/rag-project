"""
知识库管理 API。
"""
from fastapi import APIRouter, HTTPException

from models.schemas import KnowledgeBaseCreate, KnowledgeBaseResponse
from services import kb_service
from utils.logging import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/api/knowledge-bases", tags=["知识库管理"])


@router.post("", response_model=KnowledgeBaseResponse, status_code=201)
async def create_knowledge_base(body: KnowledgeBaseCreate):
    """创建知识库"""
    logger.info("创建知识库: name=%s", body.name)
    try:
        result = await kb_service.create_knowledge_base(
            name=body.name, description=body.description
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
