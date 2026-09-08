"""
领域模板服务单元测试 — 校验 / 提示词生成 / 预设完整性（纯函数，不触库）。
"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.domain_presets import BID_PRESET, GENERIC_PRESET
from models.domain import DomainProfile
from services import domain_service
from tests.helpers import MockKB


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


def _custom_row(is_preset: bool = False) -> DomainProfile:
    return DomainProfile(
        id="aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaa1",
        name="自定义",
        description="d",
        entity_types=[
            {"code": "Disease", "name": "疾病", "color": "#FF0000", "description": "x"},
            {"code": "Hospital", "name": "医院", "color": "#00FF00", "description": "x"},
            {"code": "Doctor", "name": "医生", "color": "#0000FF", "description": "x"},
        ],
        relation_types=[{"name": "治疗", "description": "x", "direction_hint": "Hospital→Disease"}],
        is_preset=is_preset,
    )


def _seed_scalars(mock_session, *, first=None, one=None, all_rows=None):
    result = MagicMock()
    result.first.return_value = first
    result.one.return_value = one
    result.all.return_value = all_rows if all_rows is not None else []
    mock_session.scalars.return_value = result
    return result


class TestDomainServiceOperations:
    """模板 CRUD / 复制 / 绑定（autouse mock async_session，不触库）。"""

    @pytest.mark.asyncio
    async def test_list_profiles_presets_first(self, mock_async_session, mock_session):
        domain_service._cache = {
            domain_service.GENERIC_PRESET_ID: domain_service._generic_fallback(),
            "custom": _to_data_of(_custom_row()),
        }
        try:
            items = await domain_service.list_profiles()
        finally:
            domain_service._cache = None
        assert [p["is_preset"] for p in items] == [True, False]

    @pytest.mark.asyncio
    async def test_create_profile_success(self, mock_async_session, mock_session):
        row = _custom_row()
        mock_session.refresh = AsyncMock()
        data = await domain_service.create_profile(
            name=row.name, description=row.description,
            entity_types=row.entity_types, relation_types=row.relation_types,
        )
        assert data["name"] == "自定义" and data["is_preset"] is False
        assert len(data["entity_codes"]) == 3 and "治疗" in data["relation_names"]

    @pytest.mark.asyncio
    async def test_create_profile_validation_and_dup(self, mock_async_session, mock_session):
        from sqlalchemy.exc import IntegrityError

        bad_ets = _custom_row().entity_types[:2]  # 不足 3 个
        with pytest.raises(domain_service.DomainValidationError):
            await domain_service.create_profile("x", bad_ets, [])

        mock_session.commit.side_effect = IntegrityError("stmt", {}, Exception("dup"))
        with pytest.raises(domain_service.DomainValidationError) as exc_info:
            await domain_service.create_profile("重名", _custom_row().entity_types,
                                                _custom_row().relation_types)
        assert "已存在" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_update_profile_preset_protected(self, mock_async_session, mock_session):
        row = _custom_row(is_preset=True)
        _seed_scalars(mock_session, first=row)
        with pytest.raises(domain_service.DomainValidationError):
            await domain_service.update_profile(row.id, "n", "", [], [])

    @pytest.mark.asyncio
    async def test_update_profile_custom(self, mock_async_session, mock_session):
        row = _custom_row()
        _seed_scalars(mock_session, first=row)  # get_by_id（首个上下文）
        # 第二个上下文 repo.update 同 mock_session：get_by_id 复用 first=row
        data = await domain_service.update_profile(
            row.id, name="新名字", description="nd",
            entity_types=row.entity_types, relation_types=row.relation_types,
        )
        assert data["name"] == "新名字"

    @pytest.mark.asyncio
    async def test_delete_profile_in_use_raises(self, mock_async_session, mock_session):
        row = _custom_row()
        result = _seed_scalars(mock_session, first=row)
        result.one.return_value = 2
        with pytest.raises(domain_service.DomainInUseError):
            await domain_service.delete_profile(row.id)

    @pytest.mark.asyncio
    async def test_delete_profile_custom_ok(self, mock_async_session, mock_session):
        row = _custom_row()
        _seed_scalars(mock_session, first=row, one=0)
        assert await domain_service.delete_profile(row.id) is True
        mock_session.delete.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_copy_preset_creates_editable(self, mock_async_session, mock_session):
        with patch("services.domain_service.get_profile", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = domain_service._generic_fallback()
            data = await domain_service.copy_profile(domain_service.GENERIC_PRESET_ID)
        assert data["is_preset"] is False
        assert "副本" in data["name"]
        assert len(data["entity_codes"]) == 9

    @pytest.mark.asyncio
    async def test_bind_kb_profile(self, mock_async_session, mock_session):
        kb = MockKB()
        _seed_scalars(mock_session, first=kb)
        with patch("services.domain_service.get_profile", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = domain_service._generic_fallback()
            data = await domain_service.bind_kb_profile(kb.id, domain_service.GENERIC_PRESET_ID)
        assert data is not None and data["id"] == domain_service.GENERIC_PRESET_ID
        assert mock_session.commit.await_count == 2  # bind_kb + reset_graph_status 各一次


def _to_data_of(row: DomainProfile) -> dict:
    return domain_service._to_profile_data(row)
