"""
领域模板 Repository — domain_profiles 表 CRUD + KB 引用查询/绑定。
"""
import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from models.domain import DomainProfile
from models.knowledge_base import KnowledgeBase
from utils.logging import get_logger

logger = get_logger(__name__)


class DomainRepository:
    """封装 domain_profiles 表操作与 KB 绑定查询。"""

    def __init__(self, session: AsyncSession):
        self.session = session

    # ---------- 模板 CRUD ----------

    async def create(
        self,
        name: str,
        entity_types: list[dict],
        relation_types: list[dict],
        description: str | None = None,
        is_preset: bool = False,
    ) -> DomainProfile:
        profile = DomainProfile(
            id=str(uuid.uuid4()),
            name=name,
            description=description,
            entity_types=entity_types,
            relation_types=relation_types,
            is_preset=is_preset,
        )
        self.session.add(profile)
        await self.session.commit()
        await self.session.refresh(profile)
        return profile

    async def get_by_id(self, profile_id: str) -> DomainProfile | None:
        uuid.UUID(profile_id)  # 格式校验
        stmt = select(DomainProfile).where(DomainProfile.id == profile_id)
        return (await self.session.scalars(stmt)).first()

    async def list_all(self) -> list[DomainProfile]:
        stmt = select(DomainProfile).order_by(
            DomainProfile.is_preset.desc(), DomainProfile.updated_at.desc()
        )
        return list((await self.session.scalars(stmt)).all())

    async def update(
        self,
        profile_id: str,
        name: str | None = None,
        description: str | None = None,
        entity_types: list[dict] | None = None,
        relation_types: list[dict] | None = None,
    ) -> DomainProfile | None:
        profile = await self.get_by_id(profile_id)
        if profile is None:
            return None
        if name is not None:
            profile.name = name
        if description is not None:
            profile.description = description
        if entity_types is not None:
            profile.entity_types = entity_types
        if relation_types is not None:
            profile.relation_types = relation_types
        await self.session.commit()
        await self.session.refresh(profile)
        return profile

    async def delete(self, profile_id: str) -> DomainProfile | None:
        profile = await self.get_by_id(profile_id)
        if profile is None:
            return None
        await self.session.delete(profile)
        await self.session.commit()
        return profile

    # ---------- KB 引用与绑定 ----------

    async def count_kb_refs(self, profile_id: str) -> int:
        """引用该模板的知识库数量（删除模板前校验，>0 → 409）。"""
        stmt = select(func.count(KnowledgeBase.id)).where(
            KnowledgeBase.domain_profile_id == profile_id
        )
        return (await self.session.scalars(stmt)).one()

    async def list_kb_ids_by_profile(self, profile_id: str) -> list[str]:
        """引用该模板的全部知识库 id（模板变更联动失效图谱用）。"""
        stmt = select(KnowledgeBase.id).where(KnowledgeBase.domain_profile_id == profile_id)
        return list((await self.session.scalars(stmt)).all())

    async def bind_kb(self, kb_id: str, profile_id: str) -> KnowledgeBase | None:
        """KB 绑定/换绑领域模板。"""
        stmt = select(KnowledgeBase).where(KnowledgeBase.id == kb_id)
        kb = (await self.session.scalars(stmt)).first()
        if kb is None:
            return None
        kb.domain_profile_id = profile_id
        await self.session.commit()
        await self.session.refresh(kb)
        return kb
