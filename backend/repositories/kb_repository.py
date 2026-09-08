"""
知识库 Repository — knowledge_bases 表 CRUD。
"""
import uuid

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from models.knowledge_base import KnowledgeBase
from utils.logging import get_logger

logger = get_logger(__name__)


class KnowledgeBaseRepository:
    """封装 knowledge_bases 表的常见操作。"""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(
        self,
        name: str,
        description: str | None = None,
        domain_profile_id: str | None = None,
    ) -> KnowledgeBase:
        """创建知识库（v2：携带领域模板绑定；None → 语义等同未绑定）。"""
        kb = KnowledgeBase(
            id=str(uuid.uuid4()),
            name=name,
            description=description,
            document_count=0,
            domain_profile_id=domain_profile_id,
        )
        self.session.add(kb)
        await self.session.commit()
        await self.session.refresh(kb)
        logger.info("知识库写入 PG: id=%s, name=%s", kb.id, name)
        return kb

    async def list_all(self) -> list[KnowledgeBase]:
        """获取所有知识库，按创建时间倒序。"""
        stmt = select(KnowledgeBase).order_by(KnowledgeBase.created_at.desc())
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def get_by_id(self, kb_id: str) -> KnowledgeBase | None:
        """按 ID 获取知识库。"""
        try:
            uuid.UUID(kb_id)  # 验证格式
        except ValueError:
            return None
        stmt = select(KnowledgeBase).where(KnowledgeBase.id == kb_id)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def delete(self, kb_id: str) -> bool:
        """删除知识库。返回 True 表示删除了记录。"""
        kb = await self.get_by_id(kb_id)
        if kb is None:
            return False
        await self.session.delete(kb)
        await self.session.commit()
        logger.info("知识库已从 PG 删除: id=%s", kb_id)
        return True

    async def increment_doc_count(self, kb_id: str) -> None:
        """文档数量 +1（原子操作）。"""
        uuid.UUID(kb_id)  # 验证格式
        stmt = (
            update(KnowledgeBase)
            .where(KnowledgeBase.id == kb_id)
            .values(document_count=KnowledgeBase.document_count + 1)
        )
        await self.session.execute(stmt)
        await self.session.commit()

    async def decrement_doc_count(self, kb_id: str) -> None:
        """文档数量 -1（原子操作）。"""
        uuid.UUID(kb_id)  # 验证格式
        stmt = (
            update(KnowledgeBase)
            .where(KnowledgeBase.id == kb_id)
            .values(document_count=KnowledgeBase.document_count - 1)
        )
        await self.session.execute(stmt)
        await self.session.commit()
