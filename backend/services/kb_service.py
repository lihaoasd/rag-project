"""
知识库业务逻辑 — PostgreSQL CRUD + Milvus 清理。
"""
from core.database import async_session
from core.indexing import drop_kb_collection
from utils import minio_client
from repositories.kb_repository import KnowledgeBaseRepository
from repositories.document_repository import DocumentRepository
from repositories.conversation_repository import ConversationRepository
from utils.logging import get_logger

logger = get_logger(__name__)


def _kb_to_dict(kb) -> dict:
    """将 ORM 对象转为 API 响应字典。"""
    return {
        "id": str(kb.id),
        "name": kb.name,
        "description": kb.description,
        "document_count": kb.document_count,
        "created_at": kb.created_at.isoformat() if kb.created_at else "",
        "updated_at": kb.updated_at.isoformat() if kb.updated_at else "",
    }


async def create_knowledge_base(name: str, description: str | None = None) -> dict:
    """创建知识库（写入 PostgreSQL）。"""
    async with async_session() as session:
        repo = KnowledgeBaseRepository(session)
        kb = await repo.create(name=name, description=description)
        return _kb_to_dict(kb)


async def list_knowledge_bases() -> list[dict]:
    """获取所有知识库列表。"""
    async with async_session() as session:
        repo = KnowledgeBaseRepository(session)
        kbs = await repo.list_all()
        return [_kb_to_dict(kb) for kb in kbs]


async def get_knowledge_base(kb_id: str) -> dict | None:
    """获取单个知识库详情。"""
    async with async_session() as session:
        repo = KnowledgeBaseRepository(session)
        kb = await repo.get_by_id(kb_id)
        return _kb_to_dict(kb) if kb else None


async def delete_knowledge_base(kb_id: str) -> bool:
    """删除知识库 — PG(手动级联) + Milvus + MinIO 三处清理。"""
    # 1. 手动级联删除子表记录（无 FK，需按依赖顺序手动删除）
    #    messages → conversations → document_chunks → documents → knowledge_base
    async with async_session() as session:
        conv_repo = ConversationRepository(session)
        doc_repo = DocumentRepository(session)
        kb_repo = KnowledgeBaseRepository(session)

        # 检查知识库是否存在
        kb = await kb_repo.get_by_id(kb_id)
        if kb is None:
            return False

        # 1a. 删除消息
        await conv_repo.delete_messages_by_kb_id(kb_id)
        # 1b. 删除会话
        await conv_repo.delete_conversations_by_kb_id(kb_id)
        # 1c. 删除文档切片
        await doc_repo.delete_chunks_by_kb_id(kb_id)
        # 1d. 删除文档（获取文档列表用于后续 MinIO 清理）
        deleted_docs = await doc_repo.delete_documents_by_kb_id(kb_id)
        # 1e. 删除知识库
        await kb_repo.delete(kb_id)

    # 2. 删除 Milvus Collection
    try:
        drop_kb_collection(kb_id)
    except Exception as e:
        logger.error("删除 Milvus Collection 失败: %s", e, exc_info=True)

    # 3. 删除 MinIO 中该知识库的所有文档文件
    try:
        minio_client.delete_prefix(f"{kb_id}/")
    except Exception as e:
        logger.error("删除 MinIO 文件失败: %s", e, exc_info=True)

    return True
