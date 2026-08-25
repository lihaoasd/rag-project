"""
图谱抽取管线单元测试 — 正则 / JSON 解析 / 合并去重 / 管线编排（mock LLM 与词库）。
"""
from unittest.mock import AsyncMock, patch

import pytest

from core import graph_extractor
from models.graph_schemas import ExtractionOutput
from services import lexicon_service
from tests.helpers import KB_ID


class TestNormalizeName:
    def test_fullwidth_to_halfwidth(self):
        assert graph_extractor._normalize_name("ＡＢＣ 公司 ") == "ABC 公司"

    def test_strip_whitespace(self):
        assert graph_extractor._normalize_name("  甲公司  ") == "甲公司"

    def test_empty(self):
        assert graph_extractor._normalize_name("") == ""


class TestRegexMatchEntities:
    def test_money(self):
        ents = graph_extractor._regex_match_entities("本项目合同金额为 3000万元")
        names = {e["name"] for e in ents}
        assert any("3000" in n and "万元" in n for n in names)

    def test_date(self):
        ents = graph_extractor._regex_match_entities("开标日期：2024年3月1日")
        names = {e["name"] for e in ents}
        assert "2024年3月1日" in names

    def test_no_match(self):
        assert graph_extractor._regex_match_entities("无金额无日期文本") == []


class TestParseLLMOutput:
    def test_plain_json(self):
        raw = '{"entities": [{"name": "甲公司", "type": "ORGANIZATION"}], "relationships": []}'
        out = graph_extractor._parse_llm_output(raw)
        assert out is not None
        assert out.entities[0].name == "甲公司"

    def test_markdown_wrapped(self):
        raw = '```json\n{"entities": [], "relationships": []}\n```'
        out = graph_extractor._parse_llm_output(raw)
        assert out is not None
        assert out.entities == []

    def test_invalid_json(self):
        assert graph_extractor._parse_llm_output("not json") is None


class TestAssignChunkIds:
    def test_hit_second_chunk(self):
        chunks = [{"text": "无实体文本"}, {"text": "甲公司中标"}]
        ids = graph_extractor._assign_chunk_ids("甲公司", chunks, ["c1", "c2"])
        assert ids == ["c2"]

    def test_hit_multiple_chunks(self):
        chunks = [{"text": "甲公司中标"}, {"text": "甲公司合作"}]
        ids = graph_extractor._assign_chunk_ids("甲公司", chunks, ["c1", "c2"])
        assert ids == ["c1", "c2"]

    def test_miss_fallback_first(self):
        """定位失败（改名/歧义）降级该批第一个 chunk。"""
        chunks = [{"text": "文本A"}, {"text": "文本B"}]
        ids = graph_extractor._assign_chunk_ids("不存在的实体", chunks, ["c1", "c2"])
        assert ids == ["c1"]

    def test_empty_batch(self):
        assert graph_extractor._assign_chunk_ids("甲公司", [], []) == []


class TestMergeWithLexicon:
    def test_exact_alias_merge(self):
        lexicon_service._cache = {
            "甲公司": {"aliases": ["甲建设集团"], "category": "organization"},
        }
        try:
            assert graph_extractor._merge_with_lexicon("甲建设集团", "Organization") == "甲公司"
        finally:
            lexicon_service._cache = None

    def test_exact_term_merge(self):
        lexicon_service._cache = {
            "甲公司": {"aliases": ["甲建设集团"], "category": "organization"},
        }
        try:
            assert graph_extractor._merge_with_lexicon("甲公司", "Organization") == "甲公司"
        finally:
            lexicon_service._cache = None

    def test_containment_merge_longest(self):
        lexicon_service._cache = {
            "甲公司": {"aliases": [], "category": "organization"},
            "甲建设集团": {"aliases": [], "category": "organization"},
        }
        try:
            # "甲建设集团施工队" 含两个标准词，取最长
            assert graph_extractor._merge_with_lexicon("甲建设集团施工队", "Organization") == "甲建设集团"
        finally:
            lexicon_service._cache = None

    def test_type_mismatch_not_merged(self):
        lexicon_service._cache = {
            "甲公司": {"aliases": [], "category": "organization"},
        }
        try:
            # LLM 标签为 Project，词库是 organization → 不归并
            assert graph_extractor._merge_with_lexicon("甲公司", "Project") == "甲公司"
        finally:
            lexicon_service._cache = None


class TestExtractFromChunks:
    @pytest.mark.asyncio
    async def test_lexicon_and_llm_merge(self, mock_async_session, mock_session):
        # 注入词库缓存（词条 → 分类）
        lexicon_service._cache = {
            "甲公司": {"aliases": ["甲建设集团"], "category": "organization"},
            "招标文件": {"aliases": [], "category": "other"},  # other 不进图
        }
        llm_output = ExtractionOutput(
            entities=[
                {"name": "XX道路改造工程", "type": "PROJECT", "description": "项目"},
            ],
            relationships=[
                {"source": "甲公司", "target": "XX道路改造工程", "type": "参与"},
            ],
        )
        chunks = [
            {"id": "c1", "text": "甲公司（甲建设集团）参与 XX道路改造工程，金额 3000万元。"},
            {"id": "c2", "text": "招标文件要求……"},
        ]
        try:
            with patch(
                "core.graph_extractor._llm_extract_batch",
                new_callable=AsyncMock,
                return_value=llm_output,
            ) as mock_llm:
                result = await graph_extractor.extract_from_chunks(KB_ID, chunks)
        finally:
            lexicon_service._cache = None

        assert mock_llm.called
        names = {e["name"] for e in result["entities"]}
        # 词库实体（别名归一） + LLM 实体 + 正则数量
        assert "甲公司" in names
        assert "XX道路改造工程" in names
        assert any("3000" in n for n in names)
        # 通用实体类型映射：PROJECT→Project、正则金额→Quantity、词库→Organization
        proj = next(e for e in result["entities"] if e["name"] == "XX道路改造工程")
        assert proj["type"] == "Project"
        qty = next(e for e in result["entities"] if "3000" in e["name"])
        assert qty["type"] == "Quantity"
        org = next(e for e in result["entities"] if e["name"] == "甲公司")
        assert org["type"] == "Organization"
        # other 分类不进图
        assert "招标文件" not in names
        # 关系保留（端点均在实体集合，通用类型白名单内）
        assert len(result["relationships"]) == 1
        assert result["relationships"][0]["type"] == "参与"
        # chunk 归属（G-06 证据前提）：LLM 实体回原文定位到 c1
        llm_ent = next(e for e in result["entities"] if e["name"] == "XX道路改造工程")
        assert llm_ent["chunk_ids"] == ["c1"]

    @pytest.mark.asyncio
    async def test_short_chunks_skipped_by_caller(self, mock_async_session, mock_session):
        """短 chunk 过滤由 build_file_graph 调用方负责；管线对空输入返回空结果。"""
        lexicon_service._cache = {}
        try:
            result = await graph_extractor.extract_from_chunks(KB_ID, [])
        finally:
            lexicon_service._cache = None
        assert result["entities"] == []
        assert result["relationships"] == []
