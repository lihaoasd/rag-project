"""
词库 Repository — lexicon_terms 表 CRUD（分页/搜索/批量导入；词条为全局分词/同义词词典，无分类）。
"""
import uuid

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from models.lexicon import LexiconTerm
from utils.logging import get_logger

logger = get_logger(__name__)


class LexiconRepository:
    """封装 lexicon_terms 表的常见操作。"""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(
        self,
        term: str,
        aliases: list[str] | None = None,
        enabled: bool = True,
    ) -> LexiconTerm:
        entry = LexiconTerm(
            id=str(uuid.uuid4()),
            term=term,
            aliases=aliases or [],
            enabled=enabled,
        )
        self.session.add(entry)
        await self.session.commit()
        await self.session.refresh(entry)
        logger.info("词条写入: term=%s", term)
        return entry

    async def get_by_term(self, term: str) -> LexiconTerm | None:
        stmt = select(LexiconTerm).where(LexiconTerm.term == term)
        return (await self.session.scalars(stmt)).first()

    async def get_by_id(self, term_id: str) -> LexiconTerm | None:
        uuid.UUID(term_id)  # 验证格式
        stmt = select(LexiconTerm).where(LexiconTerm.id == term_id)
        return (await self.session.scalars(stmt)).first()

    async def list_page(
        self,
        offset: int = 0,
        limit: int = 20,
        search: str = "",
    ) -> tuple[list[LexiconTerm], int]:
        """分页查询词条（支持词条/别名模糊搜索）。"""
        stmt = select(LexiconTerm)
        count_stmt = select(func.count(LexiconTerm.id))
        if search:
            like = f"%{search}%"
            stmt = stmt.where(
                LexiconTerm.term.ilike(like) | LexiconTerm.aliases.cast(func.text).ilike(like)
            )
            count_stmt = count_stmt.where(
                LexiconTerm.term.ilike(like) | LexiconTerm.aliases.cast(func.text).ilike(like)
            )
        total = (await self.session.scalars(count_stmt)).one()
        rows = list(
            (
                await self.session.scalars(
                    stmt.order_by(LexiconTerm.updated_at.desc()).offset(offset).limit(limit)
                )
            ).all()
        )
        return rows, total

    async def update(
        self,
        term_id: str,
        term: str | None = None,
        aliases: list[str] | None = None,
        enabled: bool | None = None,
    ) -> LexiconTerm | None:
        entry = await self.get_by_id(term_id)
        if entry is None:
            return None
        if term is not None:
            entry.term = term
        if aliases is not None:
            entry.aliases = aliases
        if enabled is not None:
            entry.enabled = enabled
        await self.session.commit()
        await self.session.refresh(entry)
        return entry

    async def delete(self, term_id: str) -> LexiconTerm | None:
        entry = await self.get_by_id(term_id)
        if entry is None:
            return None
        await self.session.delete(entry)
        await self.session.commit()
        return entry

    async def list_enabled(self) -> list[LexiconTerm]:
        """查询所有启用词条（ik-dict 导出 / 查询扩展缓存用）。"""
        stmt = select(LexiconTerm).where(LexiconTerm.enabled.is_(True))
        return list((await self.session.scalars(stmt)).all())

    async def stats(self) -> dict:
        total = (await self.session.scalars(select(func.count(LexiconTerm.id)))).one()
        enabled = (
            await self.session.scalars(
                select(func.count(LexiconTerm.id)).where(LexiconTerm.enabled.is_(True))
            )
        ).one()
        return {"total": total, "enabled": enabled}
