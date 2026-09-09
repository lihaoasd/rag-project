"""
图谱抽取管线单元测试 — 正则 / JSON 解析 / 合并去重 / 管线编排（mock LLM 与词库）。

v2：抽取提示词由领域模板生成、LLM 输出类型/关系按模板过滤（docs/10 §3.2/§3.3）。
"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core import graph_extractor
from core.domain_presets import GENERIC_PRESET
from models.graph_schemas import ExtractionOutput
from services import domain_service, lexicon_service
from tests.helpers import KB_ID


@pytest.fixture(autouse=True)
def _mock_graph_langfuse_client():
    """统一隔离 graph_extractor 的 langfuse client（本模块用例不触达真实 Langfuse）。"""
    with patch("core.graph_extractor.get_client", return_value=MagicMock()):
        yield


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
        raw = '{"entities": [{"name": "甲公司", "type": "Organization"}], "relationships": []}'
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


class TestMergeByCode:
    """LLM 实体名归并：仅当词条在 KB 模板下映射 code == LLM 类型（v2 §7.3）。"""

    @staticmethod
    def _terms():
        return [
            {"term": "甲公司", "aliases": ["甲建设集团"], "code": "Organization"},
            {"term": "甲建设集团", "aliases": [], "code": "Organization"},
        ]

    def test_exact_alias_merge(self):
        terms = [self._terms()[0]]
        assert graph_extractor._merge_by_code("甲建设集团", "Organization", terms) == "甲公司"

    def test_exact_term_merge(self):
        terms = [self._terms()[0]]
        assert graph_extractor._merge_by_code("甲公司", "Organization", terms) == "甲公司"

    def test_containment_merge_longest(self):
        terms = self._terms()
        # "甲建设集团施工队" 含两个标准词，取最长
        assert graph_extractor._merge_by_code("甲建设集团施工队", "Organization", terms) == "甲建设集团"

    def test_type_mismatch_not_merged(self):
        terms = [self._terms()[0]]
        # LLM 标签为 Project，词条在模板下映射为 Organization → 不归并
        assert graph_extractor._merge_by_code("甲公司", "Project", terms) == "甲公司"

    def test_code_mismatch_other_domain_mapping_not_merged(self):
        # 同一词条在“医疗”模板映射 Disease，但当前 KB 模板为通用（Organization）→ 无匹配
        terms = [{"term": "甲公司", "aliases": [], "code": "Disease"}]
        assert graph_extractor._merge_by_code("甲公司", "Organization", terms) == "甲公司"


class TestExtractionPromptFromProfile:
    """实体抽取提示词由领域模板生成；None → 内置通用预设兜底。"""

    @staticmethod
    def _mock_llm_with_output():
        """mock create_llm，achat 返回合法抽取 JSON。"""
        llm = MagicMock()
        resp = MagicMock()
        resp.message.content = '{"entities": [], "relationships": []}'
        llm.achat = AsyncMock(return_value=resp)
        return llm

    @staticmethod
    def _patched_env(llm):
        patches = [
            patch("core.graph_extractor.resolve_model_config",
                  new_callable=AsyncMock, return_value={"model": "mock"}),
            patch("core.graph_extractor.create_llm", return_value=llm),
        ]
        for p in patches:
            p.start()
        return patches

    @pytest.mark.asyncio
    async def test_default_uses_generic_preset_prompt(self):
        llm = self._mock_llm_with_output()
        patches = self._patched_env(llm)
        try:
            await graph_extractor._llm_extract_batch(KB_ID, [{"text": "测试文本"}])
        finally:
            for p in patches:
                p.stop()
        args, _ = llm.achat.await_args
        expected = domain_service.build_extraction_prompt(
            GENERIC_PRESET["entity_types"], GENERIC_PRESET["relation_types"]
        )
        assert args[0][0].content == expected
        assert "Organization(组织/机构)" in args[0][0].content

    @pytest.mark.asyncio
    async def test_custom_profile_prompt(self):
        llm = self._mock_llm_with_output()
        patches = self._patched_env(llm)
        custom = {
            "name": "医疗",
            "entity_types": [{"code": "Disease", "name": "疾病", "color": "#F00",
                              "description": "疾病名", "examples": ["高血压"]}],
            "relation_types": [{"name": "治疗", "description": "治疗关系",
                                "direction_hint": "Organization→Disease"}],
        }
        profile = domain_service._preset_data(custom, "custom-id")
        try:
            await graph_extractor._llm_extract_batch(KB_ID, [{"text": "x"}], profile)
        finally:
            for p in patches:
                p.stop()
        args, _ = llm.achat.await_args
        assert "Disease(疾病)" in args[0][0].content
        assert "治疗（Organization→Disease）" in args[0][0].content
        assert "中标" not in args[0][0].content


class TestExtractFromChunks:
    @pytest.mark.asyncio
    async def test_lexicon_and_llm_merge(self, mock_async_session, mock_session):
        # 词条×领域映射：仅“甲公司→Organization”有映射（招标文件无映射 → 不进图）
        mapped_terms = [
            {"term": "甲公司", "aliases": ["甲建设集团"], "code": "Organization"},
        ]
        llm_output = ExtractionOutput(
            entities=[
                {"name": "XX道路改造工程", "type": "Project", "description": "项目"},
            ],
            relationships=[
                {"source": "甲公司", "target": "XX道路改造工程", "type": "参与"},
            ],
        )
        chunks = [
            {"id": "c1", "text": "甲公司（甲建设集团）参与 XX道路改造工程，金额 3000万元。"},
            {"id": "c2", "text": "招标文件要求……"},
        ]
        lexicon_service._cache = {}
        try:
            with patch(
                "core.graph_extractor.lexicon_service.profile_terms",
                new_callable=AsyncMock,
                return_value=mapped_terms,
            ), patch(
                "core.graph_extractor._llm_extract_batch",
                new_callable=AsyncMock,
                # 逐 chunk（默认 batch=1）：c1 报实体，c2 无实体 → 实体仅归属 c1
                side_effect=[llm_output, ExtractionOutput(entities=[], relationships=[])],
            ) as mock_llm:
                result = await graph_extractor.extract_from_chunks(KB_ID, chunks)
        finally:
            lexicon_service._cache = None

        assert mock_llm.called
        names = {e["name"] for e in result["entities"]}
        # 词库映射实体（别名归一） + LLM 实体 + 正则数量
        assert "甲公司" in names
        assert "XX道路改造工程" in names
        assert any("3000" in n for n in names)
        # 通用预设类型：Project/Quantity（正则）/Organization（词库）
        proj = next(e for e in result["entities"] if e["name"] == "XX道路改造工程")
        assert proj["type"] == "Project"
        qty = next(e for e in result["entities"] if "3000" in e["name"])
        assert qty["type"] == "Quantity"
        org = next(e for e in result["entities"] if e["name"] == "甲公司")
        assert org["type"] == "Organization"
        # 无映射词条不进图
        assert "招标文件" not in names
        # 关系保留（端点均在实体集合，类型在通用模板关系清单内）
        assert len(result["relationships"]) == 1
        assert result["relationships"][0]["type"] == "参与"
        # chunk 归属（G-06 证据前提）：LLM 实体回原文定位到 c1
        llm_ent = next(e for e in result["entities"] if e["name"] == "XX道路改造工程")
        assert llm_ent["chunk_ids"] == ["c1"]

    @pytest.mark.asyncio
    async def test_out_of_profile_types_dropped(self, mock_async_session, mock_session):
        """模板外的实体类型 / 关系类型被丢弃（跨领域防污染）。"""
        lexicon_service._cache = {}
        llm_output = ExtractionOutput(
            entities=[
                {"name": "甲公司", "type": "Organization", "description": ""},
                {"name": "XX道路改造工程", "type": "Project", "description": ""},
                {"name": "建筑一级资质", "type": "Qualification", "description": ""},  # 通用预设无此类型
            ],
            relationships=[
                {"source": "甲公司", "target": "XX道路改造工程", "type": "参与"},   # 通用模板有
                {"source": "甲公司", "target": "XX道路改造工程", "type": "中标"},   # 投标专用，通用模板无
            ],
        )
        chunks = [{"id": "c1", "text": "甲公司中标 XX道路改造工程，具备建筑一级资质。"}]
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
        types = {e["type"] for e in result["entities"]}
        assert types == {"Organization", "Project"}  # Qualification 不在通用预设 → 丢弃
        # 中标 不在通用模板关系清单 → 丢弃；参与 端点齐全且类型在模板 → 保留
        assert {r["type"] for r in result["relationships"]} == {"参与"}

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

    @pytest.mark.asyncio
    async def test_single_chunk_extraction_when_batch_1(self, mock_async_session, mock_session):
        """逐 chunk 提取（graph_extraction_batch=1）：每个 chunk 各触发一次 LLM 调用，chunk 归属准确。"""
        lexicon_service._cache = {}
        chunks = [
            {"id": "c1", "text": "甲公司中标 XX项目。"},
            {"id": "c2", "text": "乙公司负责 YY工程。"},
            {"id": "c3", "text": "丙公司参与 ZZ活动。"},
        ]
        outputs = [
            ExtractionOutput(entities=[{"name": "甲公司", "type": "Organization"}], relationships=[]),
            ExtractionOutput(entities=[{"name": "乙公司", "type": "Organization"}], relationships=[]),
            ExtractionOutput(entities=[{"name": "丙公司", "type": "Organization"}], relationships=[]),
        ]
        original_batch = graph_extractor.settings.graph_extraction_batch
        graph_extractor.settings.graph_extraction_batch = 1
        try:
            with patch(
                "core.graph_extractor._llm_extract_batch",
                new_callable=AsyncMock,
                side_effect=outputs,
            ) as mock_llm:
                result = await graph_extractor.extract_from_chunks(KB_ID, chunks)
        finally:
            graph_extractor.settings.graph_extraction_batch = original_batch
            lexicon_service._cache = None

        assert mock_llm.await_count == 3
        # 每次调用仅传入 1 个 chunk
        for call in mock_llm.await_args_list:
            assert len(call.args[1]) == 1
        # 实体 chunk 归属：各自落在当前 chunk
        names = {e["name"]: e["chunk_ids"] for e in result["entities"]}
        assert names["甲公司"] == ["c1"]
        assert names["乙公司"] == ["c2"]
        assert names["丙公司"] == ["c3"]


class TestExtractionLangfuseRecording:
    """_llm_extract_batch：每次实际 LLM 调用记录 langfuse generation（model/input/output/usage）。"""

    @staticmethod
    def _patched_env(llm):
        return [
            patch("core.graph_extractor.resolve_model_config",
                  new_callable=AsyncMock, return_value={"model": "mock-model"}),
            patch("core.graph_extractor.create_llm", return_value=llm),
        ]

    def _start(self, patches):
        for p in patches:
            p.start()
        return patches

    @pytest.mark.asyncio
    async def test_success_records_generation_with_usage(self):
        llm = MagicMock()
        resp = MagicMock()
        resp.message.content = '{"entities": [], "relationships": []}'
        resp.additional_kwargs = {"prompt_tokens": 50, "completion_tokens": 10, "total_tokens": 60}
        llm.achat = AsyncMock(return_value=resp)

        mock_lf = MagicMock()
        patches = self._patched_env(llm) + [
            patch("core.graph_extractor.get_client", return_value=mock_lf),
        ]
        self._start(patches)
        try:
            out = await graph_extractor._llm_extract_batch(KB_ID, [{"text": "测试文本"}])
        finally:
            for p in patches:
                p.stop()

        assert out is not None
        mock_lf.update_current_generation.assert_called_once()
        kwargs = mock_lf.update_current_generation.call_args.kwargs
        assert kwargs["model"] == "mock-model"
        assert len(kwargs["input"]) == 2  # system + user
        assert kwargs["output"].startswith("{")
        assert kwargs["usage_details"] == {
            "prompt_tokens": 50,
            "completion_tokens": 10,
            "total_tokens": 60,
        }

    @pytest.mark.asyncio
    async def test_all_attempts_fail_records_error_generation(self):
        llm = MagicMock()
        llm.achat = AsyncMock(side_effect=RuntimeError("llm down"))

        mock_lf = MagicMock()
        patches = self._patched_env(llm) + [
            patch("core.graph_extractor.get_client", return_value=mock_lf),
        ]
        self._start(patches)
        try:
            out = await graph_extractor._llm_extract_batch(KB_ID, [{"text": "x"}])
        finally:
            for p in patches:
                p.stop()

        assert out is None
        # 两次尝试均异常：循环内不入账，结束后统一记一条 ERROR 级 generation
        mock_lf.update_current_generation.assert_called_once()
        kwargs = mock_lf.update_current_generation.call_args.kwargs
        assert kwargs["level"] == "ERROR"
        assert kwargs["model"] == "mock-model"
        assert "失败" in kwargs["status_message"]
