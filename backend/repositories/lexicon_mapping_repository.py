"""
词库词条 × 领域模板映射 Repository — lexicon_domain_mappings 表操作。

语义（docs/10 §7 / docs/plan/48）：
- 词条 × 领域模板 → 实体 code（同域唯一，写入前由服务层校验 code ∈ 模板）；
- 供图谱抽取按 KB 绑定模板拉取 {标准词 → code}、图谱检索候选、词库页映射编辑。
"""
import uuid

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from models.lexicon import LexiconTerm
from models.lexicon_mapping import LexiconDomainMapping
from utils.logging import get_logger

logger = get_logger(__name__)


class LexiconMappingRepository:
    """封装 lexicon_domain_mappings 表操作（无外键，词条/模板删除的映射由服务层联动清理）。"""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def list_by_term(self, term_id: str) -> list[LexiconDomainMapping]:
        stmt = select(LexiconDomainMapping).where(
            LexiconDomainMapping.lexicon_term_id == term_id
        )
        return list((await self.session.scalars(stmt)).all())

    async def list_by_profile(self, profile_id: str) -> list[tuple[str, str]]:
        """该模板下启用词条的 (标准词, entity_code) 列表（图谱抽取用）。"""
        stmt = (
            select(LexiconTerm.term, LexiconDomainMapping.entity_code)
            .join(
                LexiconDomainMapping,
                LexiconDomainMapping.lexicon_term_id == LexiconTerm.id,
            )
            .where(
                LexiconDomainMapping.domain_profile_id == profile_id,
                LexiconTerm.enabled.is_(True),
            )
        )
        rows = (await self.session.execute(stmt)).all()
        return [(str(term), str(code)) for term, code in rows]

    async def count_by_terms(self, term_ids: list[str]) -> dict[str, int]:
        """批量统计各词条映射的领域数（列表展示）。"""
        if not term_ids:
            return {}
        stmt = (
            select(
                LexiconDomainMapping.lexicon_term_id,
                func.count(LexiconDomainMapping.id),
            )
            .where(LexiconDomainMapping.lexicon_term_id.in_(term_ids))
            .group_by(LexiconDomainMapping.lexicon_term_id)
        )
        rows = (await self.session.execute(stmt)).all()
        return {str(tid): int(cnt) for tid, cnt in rows}

    async def replace_for_term(
        self, term_id: str, items: list[tuple[str, str]]
    ) -> None:
        """整包替换某词条的领域映射：删旧插新（items = [(domain_profile_id, entity_code)]）。"""
        await self.session.execute(
            delete(LexiconDomainMapping).where(
                LexiconDomainMapping.lexicon_term_id == term_id
            )
        )
        for profile_id, code in items:
            self.session.add(
                LexiconDomainMapping(
                    id=str(uuid.uuid4()),
                    lexicon_term_id=term_id,
                    domain_profile_id=profile_id,
                    entity_code=code,
                )
            )
        await self.session.commit()
        logger.info("词条领域映射已更新: term_id=%s, mappings=%d", term_id, len(items))

    async def delete_by_term(self, term_id: str) -> None:
        await self.session.execute(
            delete(LexiconDomainMapping).where(
                LexiconDomainMapping.lexicon_term_id == term_id
            )
        )
        await self.session.commit()

    async def delete_by_profile(self, profile_id: str) -> int:
        result = await self.session.execute(
            delete(LexiconDomainMapping).where(
                LexiconDomainMapping.domain_profile_id == profile_id
            )
        )
        await self.session.commit()
        return result.rowcount or 0
