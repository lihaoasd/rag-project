"""
词库业务逻辑单元测试 — 查询扩展、CRUD、领域映射、CSV 导入、IK 词典导出。
（v2 修订：词库全局词典仅 term+aliases；词条与图谱关联走 词条×领域模板映射。）
"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services import lexicon_service


class TestExpandQuery:
    def test_no_match_returns_original(self):
        lexicon_service._cache = {"甲公司": {"aliases": ["甲建设集团"]}}
        assert lexicon_service.expand_query("随便搜") == ["随便搜"]

    def test_alias_in_query_adds_standard(self):
        lexicon_service._cache = {"甲公司": {"aliases": ["甲建设集团", "甲建设"]}}
        assert lexicon_service.expand_query("甲建设集团中标") == ["甲建设集团中标", "甲公司"]

    def test_term_in_query_adds_aliases(self):
        lexicon_service._cache = {"甲公司": {"aliases": ["甲建设集团"]}}
        assert lexicon_service.expand_query("甲公司资质") == ["甲公司资质", "甲建设集团"]

    def test_empty_cache(self):
        lexicon_service._cache = {}
        assert lexicon_service.expand_query("x") == ["x"]

    def test_multiple_matches_collect(self):
        lexicon_service._cache = {
            "甲公司": {"aliases": ["甲建设集团"]},
            "市政总承包": {"aliases": ["市政公用工程施工总承包"]},
        }
        # 扩展词按 unicode 排序：市政总承包(市 U+5E02) < 甲公司(甲 U+7532)
        assert lexicon_service.expand_query("甲建设集团 市政公用工程施工总承包") == [
            "甲建设集团 市政公用工程施工总承包",
            "市政总承包",
            "甲公司",
        ]


def _entry(term="甲公司", aliases=None, enabled=True, term_id="id1"):
    entry = MagicMock()
    entry.id = term_id
    entry.term = term
    entry.aliases = aliases or []
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
            result = await lexicon_service.create_term("甲公司", ["甲建设集团"])
        assert result["term"] == "甲公司"
        assert result["aliases"] == ["甲建设集团"]
        repo.create.assert_awaited_once_with("甲公司", ["甲建设集团"], True)


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
        repo.get_by_id = AsyncMock(return_value=None)
        with patch("services.lexicon_service.LexiconRepository", return_value=repo):
            with pytest.raises(ValueError, match="不存在"):
                await lexicon_service.delete_term("id1")

    @pytest.mark.asyncio
    async def test_delete_success_removes_mappings(self):
        repo = MagicMock()
        repo.get_by_id = AsyncMock(return_value=_entry())
        repo.delete = AsyncMock(return_value=_entry())
        mapping_repo = MagicMock()
        mapping_repo.delete_by_term = AsyncMock()
        with patch("services.lexicon_service.LexiconRepository", return_value=repo), \
             patch("services.lexicon_service.LexiconMappingRepository", return_value=mapping_repo), \
             patch("services.lexicon_service.refresh_cache", new_callable=AsyncMock):
            await lexicon_service.delete_term("id1")
        mapping_repo.delete_by_term.assert_awaited_once_with("id1")
        repo.delete.assert_awaited_once_with("id1")


class TestProfileTermsAndMatch:
    @pytest.mark.asyncio
    async def test_profile_terms_joins_cache_aliases(self):
        lexicon_service._cache = {
            "甲公司": {"aliases": ["甲建设集团"]},
            "乙医院": {"aliases": []},
        }
        mapping_repo = MagicMock()
        mapping_repo.list_by_profile = AsyncMock(
            return_value=[("甲公司", "Organization"), ("乙医院", "Hospital")]
        )
        with patch("services.lexicon_service.LexiconMappingRepository", return_value=mapping_repo):
            items = await lexicon_service.profile_terms("pid1")
        assert items == [
            {"term": "甲公司", "aliases": ["甲建设集团"], "code": "Organization"},
            {"term": "乙医院", "aliases": [], "code": "Hospital"},
        ]

    @pytest.mark.asyncio
    async def test_profile_terms_empty_without_profile(self):
        assert await lexicon_service.profile_terms(None) == []

    def test_match_terms_standard_and_alias_longest_first(self):
        terms = [
            {"term": "甲建设集团有限公司", "aliases": [], "code": "Organization"},
            {"term": "甲公司", "aliases": ["甲建设集团"], "code": "Organization"},
            {"term": "乙医院", "aliases": [], "code": "Hospital"},
        ]
        # 长词优先：9 字标准词 >（3 字标准词经别名命中）；别名是长词前缀时两词均命中
        assert lexicon_service.match_terms("甲建设集团有限公司承建乙医院", terms) == [
            ("甲建设集团有限公司", "Organization"),
            ("甲公司", "Organization"),
            ("乙医院", "Hospital"),
        ]

    def test_match_terms_dedup_and_skip_unmapped(self):
        terms = [
            {"term": "甲公司", "aliases": ["甲建设集团"], "code": "Organization"},
            {"term": "丙医院", "aliases": [], "code": ""},  # 空 code 视为未映射
        ]
        assert lexicon_service.match_terms("甲建设集团 甲公司", terms) == [
            ("甲公司", "Organization")
        ]

    def test_match_terms_no_hit(self):
        assert lexicon_service.match_terms("随便搜", [{"term": "甲公司", "aliases": [], "code": "X"}]) == []


def _profile(pid, name, codes):
    return {
        "id": pid,
        "name": name,
        "entity_meta": {
            c: {"name": f"{name}实体{c}"} for c in codes
        },
    }


def _mapping_row(pid, code):
    row = MagicMock()
    row.domain_profile_id = pid
    row.entity_code = code
    return row


class TestMappingOps:
    @pytest.mark.asyncio
    async def test_list_term_mappings_enriches_names(self):
        mapping_repo = MagicMock()
        mapping_repo.list_by_term = AsyncMock(
            return_value=[_mapping_row("p1", "Organization"), _mapping_row("p2", "Disease")]
        )
        profiles = [
            _profile("p1", "通用", ["Organization", "Person"]),
            _profile("p2", "医疗", ["Disease"]),
        ]
        with patch("services.lexicon_service.LexiconMappingRepository", return_value=mapping_repo), \
             patch("services.lexicon_service.domain_service.list_profiles",
                   new_callable=AsyncMock, return_value=profiles):
            items = await lexicon_service.list_term_mappings("t1")
        assert items == [
            {"domain_profile_id": "p1", "domain_name": "通用",
             "entity_code": "Organization", "entity_name": "通用实体Organization"},
            {"domain_profile_id": "p2", "domain_name": "医疗",
             "entity_code": "Disease", "entity_name": "医疗实体Disease"},
        ]

    @pytest.mark.asyncio
    async def test_replace_term_mappings_ok(self):
        lexicon_repo = MagicMock()
        lexicon_repo.get_by_id = AsyncMock(return_value=_entry())
        mapping_repo = MagicMock()
        mapping_repo.replace_for_term = AsyncMock()
        mapping_repo.list_by_term = AsyncMock(
            return_value=[_mapping_row("p1", "Organization")]
        )
        profiles = [_profile("p1", "通用", ["Organization", "Person"])]
        with patch("services.lexicon_service.LexiconRepository", return_value=lexicon_repo), \
             patch("services.lexicon_service.LexiconMappingRepository", return_value=mapping_repo), \
             patch("services.lexicon_service.domain_service.list_profiles",
                   new_callable=AsyncMock, return_value=profiles), \
             patch("services.lexicon_service._invalidate_graphs_after_lexicon_change",
                   new_callable=AsyncMock):
            items = await lexicon_service.replace_term_mappings(
                "t1", [{"domain_profile_id": "p1", "entity_code": "Organization"}]
            )
        mapping_repo.replace_for_term.assert_awaited_once_with(
            "t1", [("p1", "Organization")]
        )
        assert items[0]["domain_profile_id"] == "p1"

    @pytest.mark.asyncio
    async def test_replace_rejects_unknown_code(self):
        lexicon_repo = MagicMock()
        lexicon_repo.get_by_id = AsyncMock(return_value=_entry())
        profiles = [_profile("p1", "通用", ["Organization", "Person"])]
        with patch("services.lexicon_service.LexiconRepository", return_value=lexicon_repo), \
             patch("services.lexicon_service.domain_service.list_profiles",
                   new_callable=AsyncMock, return_value=profiles):
            with pytest.raises(ValueError, match="不在领域"):
                await lexicon_service.replace_term_mappings(
                    "t1", [{"domain_profile_id": "p1", "entity_code": "BadCode"}]
                )

    @pytest.mark.asyncio
    async def test_replace_rejects_duplicate_profile(self):
        lexicon_repo = MagicMock()
        lexicon_repo.get_by_id = AsyncMock(return_value=_entry())
        profiles = [_profile("p1", "通用", ["Organization", "Person"])]
        with patch("services.lexicon_service.LexiconRepository", return_value=lexicon_repo), \
             patch("services.lexicon_service.domain_service.list_profiles",
                   new_callable=AsyncMock, return_value=profiles):
            with pytest.raises(ValueError, match="同一领域只允许一个"):
                await lexicon_service.replace_term_mappings(
                    "t1", [
                        {"domain_profile_id": "p1", "entity_code": "Organization"},
                        {"domain_profile_id": "p1", "entity_code": "Person"},
                    ]
                )

    @pytest.mark.asyncio
    async def test_replace_term_not_found(self):
        lexicon_repo = MagicMock()
        lexicon_repo.get_by_id = AsyncMock(return_value=None)
        with patch("services.lexicon_service.LexiconRepository", return_value=lexicon_repo):
            with pytest.raises(ValueError, match="词条不存在"):
                await lexicon_service.replace_term_mappings(
                    "t1", [{"domain_profile_id": "p1", "entity_code": "Organization"}]
                )


class TestImportExport:
    @pytest.mark.asyncio
    async def test_import_csv_skips_header_and_duplicates_ignores_category_col(self):
        repo = MagicMock()
        repo.get_by_term = AsyncMock(side_effect=[None, None, MagicMock()])
        repo.create = AsyncMock()
        content = "词条,别名,分类\n甲公司,甲建设集团,公司\n乙公司,,建筑\n丙公司,丙;丙建设,"
        with patch("services.lexicon_service.LexiconRepository", return_value=repo), \
             patch("services.lexicon_service.refresh_cache", new_callable=AsyncMock):
            result = await lexicon_service.import_csv(content)
        assert result["imported"] == 2
        assert result["skipped"] == 1
        # 第 3 列（旧分类）不再导入：repo.create 只收 (词条, 别名)
        assert repo.create.call_args_list[0].args == ("甲公司", ["甲建设集团"])
        assert repo.create.call_args_list[1].args == ("乙公司", [])

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
    async def test_export_csv_two_columns(self):
        repo = MagicMock()
        repo.list_page = AsyncMock(
            return_value=([_entry(aliases=["甲建设集团"])], 1)
        )
        with patch("services.lexicon_service.LexiconRepository", return_value=repo):
            text = await lexicon_service.export_csv()
        assert "词条,别名" in text
        assert "分类" not in text
        assert "甲公司,甲建设集团" in text

    @pytest.mark.asyncio
    async def test_stats(self):
        repo = MagicMock()
        repo.stats = AsyncMock(return_value={"total": 2, "enabled": 1})
        with patch("services.lexicon_service.LexiconRepository", return_value=repo):
            result = await lexicon_service.get_stats()
        assert result == {"total": 2, "enabled": 1}
