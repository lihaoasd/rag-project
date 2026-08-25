"""
词库业务逻辑单元测试 — 查询扩展、CRUD、CSV 导入、IK 词典导出。
"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services import lexicon_service


class TestExpandQuery:
    def test_no_match_returns_original(self):
        lexicon_service._cache = {"甲公司": {"aliases": ["甲建设集团"], "category": "organization"}}
        assert lexicon_service.expand_query("随便搜") == ["随便搜"]

    def test_alias_in_query_adds_standard(self):
        lexicon_service._cache = {"甲公司": {"aliases": ["甲建设集团", "甲建设"], "category": "organization"}}
        assert lexicon_service.expand_query("甲建设集团中标") == ["甲建设集团中标", "甲公司"]

    def test_term_in_query_adds_aliases(self):
        lexicon_service._cache = {"甲公司": {"aliases": ["甲建设集团"], "category": "organization"}}
        assert lexicon_service.expand_query("甲公司资质") == ["甲公司资质", "甲建设集团"]

    def test_empty_cache(self):
        lexicon_service._cache = {}
        assert lexicon_service.expand_query("x") == ["x"]

    def test_multiple_matches_collect(self):
        lexicon_service._cache = {
            "甲公司": {"aliases": ["甲建设集团"], "category": "organization"},
            "市政总承包": {"aliases": ["市政公用工程施工总承包"], "category": "qualification"},
        }
        # 扩展词按 unicode 排序：市政总承包(市 U+5E02) < 甲公司(甲 U+7532)
        assert lexicon_service.expand_query("甲建设集团 市政公用工程施工总承包") == [
            "甲建设集团 市政公用工程施工总承包",
            "市政总承包",
            "甲公司",
        ]


def _entry(term="甲公司", aliases=None, category="公司", enabled=True, term_id="id1"):
    entry = MagicMock()
    entry.id = term_id
    entry.term = term
    entry.aliases = aliases or []
    entry.category = category
    entry.enabled = enabled
    entry.created_at = None
    entry.updated_at = None
    return entry


class TestCreateTerm:
    @pytest.mark.asyncio
    async def test_duplicate_raises(self):
        repo = MagicMock()
        repo.get_by_term = AsyncMock(return_value=MagicMock())
        with patch("services.lexicon_service.LexiconRepository", return_value=repo):
            with pytest.raises(ValueError, match="已存在"):
                await lexicon_service.create_term("甲公司")

    @pytest.mark.asyncio
    async def test_create_success(self):
        repo = MagicMock()
        repo.get_by_term = AsyncMock(return_value=None)
        repo.create = AsyncMock(return_value=_entry(aliases=["甲建设集团"]))
        with patch("services.lexicon_service.LexiconRepository", return_value=repo), \
             patch("services.lexicon_service.refresh_cache", new_callable=AsyncMock):
            result = await lexicon_service.create_term("甲公司", ["甲建设集团"], "公司", True)
        assert result["term"] == "甲公司"
        assert result["aliases"] == ["甲建设集团"]


class TestUpdateDelete:
    @pytest.mark.asyncio
    async def test_update_not_found(self):
        repo = MagicMock()
        repo.get_by_id = AsyncMock(return_value=None)
        with patch("services.lexicon_service.LexiconRepository", return_value=repo):
            with pytest.raises(ValueError, match="不存在"):
                await lexicon_service.update_term("id1", {"enabled": False})

    @pytest.mark.asyncio
    async def test_update_duplicate_term(self):
        repo = MagicMock()
        entry = _entry()
        repo.get_by_id = AsyncMock(return_value=entry)
        repo.get_by_term = AsyncMock(return_value=MagicMock())
        with patch("services.lexicon_service.LexiconRepository", return_value=repo):
            with pytest.raises(ValueError, match="已存在"):
                await lexicon_service.update_term("id1", {"term": "乙公司"})

    @pytest.mark.asyncio
    async def test_delete_not_found(self):
        repo = MagicMock()
        repo.delete = AsyncMock(return_value=None)
        with patch("services.lexicon_service.LexiconRepository", return_value=repo):
            with pytest.raises(ValueError, match="不存在"):
                await lexicon_service.delete_term("id1")


class TestImportExport:
    @pytest.mark.asyncio
    async def test_import_csv_skips_header_and_duplicates(self):
        repo = MagicMock()
        repo.get_by_term = AsyncMock(side_effect=[None, None, MagicMock()])
        repo.create = AsyncMock()
        content = "词条,别名,分类\n甲公司,甲建设集团,公司\n乙公司,,建筑\n丙公司,丙;丙建设,"
        with patch("services.lexicon_service.LexiconRepository", return_value=repo), \
             patch("services.lexicon_service.refresh_cache", new_callable=AsyncMock):
            result = await lexicon_service.import_csv(content)
        assert result["imported"] == 2
        assert result["skipped"] == 1
        # 别名以分号分隔解析为列表；分类自动映射为枚举（公司→organization，未知→other）
        assert repo.create.call_args_list[0].args == ("甲公司", ["甲建设集团"], "organization")
        assert repo.create.call_args_list[1].args == ("乙公司", [], "other")

    @pytest.mark.asyncio
    async def test_export_ik_dict_one_per_line(self):
        repo = MagicMock()
        repo.list_enabled = AsyncMock(
            return_value=[_entry("建筑工程施工总承包一级"), _entry("甲公司")]
        )
        with patch("services.lexicon_service.LexiconRepository", return_value=repo):
            text = await lexicon_service.export_ik_dict()
        assert text == "建筑工程施工总承包一级\n甲公司"

    @pytest.mark.asyncio
    async def test_stats(self):
        repo = MagicMock()
        repo.stats = AsyncMock(return_value={"total": 2, "enabled": 1, "categories": {"公司": 1}})
        with patch("services.lexicon_service.LexiconRepository", return_value=repo):
            result = await lexicon_service.get_stats()
        assert result["total"] == 2
