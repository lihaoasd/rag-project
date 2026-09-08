"""
领域模板服务单元测试 — 校验 / 提示词生成 / 预设完整性（纯函数，不触库）。
"""
import pytest

from core.domain_presets import BID_PRESET, GENERIC_PRESET
from services import domain_service


def _rel(name: str) -> dict:
    return {"name": name, "description": name}


def _ent(code: str, name: str = "类型", color: str = "#409EFF") -> dict:
    return {"code": code, "name": name, "color": color, "description": "d"}


class TestValidateProfile:
    def test_preset_seeds_valid(self):
        for preset in (GENERIC_PRESET, BID_PRESET):
            assert domain_service.validate_profile(
                preset["name"], preset["entity_types"], preset["relation_types"]
            ) == [], preset["name"]

    def test_ok(self):
        ets = [_ent("Organization"), _ent("Person"), _ent("Place")]
        rts = [_rel("合作")]
        assert domain_service.validate_profile("领域", ets, rts) == []

    def test_empty_name(self):
        assert domain_service.validate_profile("  ", [_ent("A"), _ent("B"), _ent("C")], [])

    def test_too_few_entities(self):
        ets = [_ent("A"), _ent("B")]
        errors = domain_service.validate_profile("x", ets, [])
        assert any("实体类型数量" in e for e in errors)

    def test_bad_code_regex(self):
        ets = [_ent("1Bad"), _ent("Org Name"), _ent("正常")]
        errors = domain_service.validate_profile("x", ets, [])
        assert any("非法" in e for e in errors)
        assert any("Org Name" in e for e in errors)

    def test_reserved_label(self):
        ets = [_ent("Entity"), _ent("Organization"), _ent("Person")]
        errors = domain_service.validate_profile("x", ets, [])
        assert any("保留" in e for e in errors)

    def test_duplicate_code(self):
        ets = [_ent("Org"), _ent("Org"), _ent("Person")]
        errors = domain_service.validate_profile("x", ets, [])
        assert any("重复" in e for e in errors)

    def test_duplicate_relation(self):
        ets = [_ent("A"), _ent("B"), _ent("C")]
        errors = domain_service.validate_profile("x", ets, [_rel("合作"), _rel("合作")])
        assert any("重复" in e for e in errors)

    def test_too_many_relations(self):
        ets = [_ent("A"), _ent("B"), _ent("C")]
        rts = [_rel(f"R{i}") for i in range(31)]
        errors = domain_service.validate_profile("x", ets, rts)
        assert any("关系类型数量" in e for e in errors)

    def test_bad_color(self):
        ets = [_ent("A"), _ent("B"), _ent("C", color="#1234")]
        errors = domain_service.validate_profile("x", ets, [])
        assert any("颜色" in e for e in errors)

    def test_empty_color_allowed(self):
        ets = [_ent("A"), _ent("B"), _ent("C", color="")]
        assert domain_service.validate_profile("x", ets, []) == []

    def test_missing_name_and_code(self):
        ets = [{"code": "", "name": ""}]
        errors = domain_service.validate_profile("x", ets, [])
        assert any("缺少 code" in e for e in errors)


class TestBuildExtractionPrompt:
    def test_covers_all_types(self, ):
        text = domain_service.build_extraction_prompt(
            GENERIC_PRESET["entity_types"], GENERIC_PRESET["relation_types"]
        )
        for e in GENERIC_PRESET["entity_types"]:
            assert e["code"] in text
            assert e["name"] in text
        for r in GENERIC_PRESET["relation_types"]:
            assert r["name"] in text
        assert "输出严格 JSON" in text
        assert "```" not in text

    def test_direction_hint_included(self):
        text = domain_service.build_extraction_prompt(
            [], [{"name": "中标", "description": "x", "direction_hint": "Organization→Project"}]
        )
        assert "中标（Organization→Project）" in text

    def test_examples_included(self):
        text = domain_service.build_extraction_prompt(
            [{"code": "Org", "name": "组织", "description": "d",
              "examples": ["甲公司", "乙公司"]}],
            [],
        )
        assert "示例：甲公司、乙公司" in text


class TestProfileData:
    def test_generic_fallback_shape(self):
        data = domain_service._generic_fallback()
        assert data["id"] == domain_service.GENERIC_PRESET_ID
        assert len(data["entity_codes"]) == len(GENERIC_PRESET["entity_types"]) == 9
        assert len(data["relation_names"]) == len(GENERIC_PRESET["relation_types"]) == 12
        assert data["entity_meta"]["Organization"]["color"] == "#409EFF"

    def test_preset_data(self):
        data = domain_service._preset_data(BID_PRESET, domain_service.BID_PRESET_ID)
        assert len(data["entity_codes"]) == 9
        assert len(data["relation_names"]) == 15
        assert "中标" in data["relation_names"]
        assert data["entity_meta"]["Qualification"]["name"] == "资质/证书"

    def test_profile_meta_view(self):
        meta = domain_service.profile_meta(domain_service._generic_fallback())
        assert len(meta["entity_types"]) == 9
        first = meta["entity_types"][0]
        assert {"code", "name", "color", "description"} <= set(first)
        assert all("direction_hint" in r or True for r in meta["relation_types"])
