"""
领域模板 Repository 单元测试。
"""
import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from models.domain import DomainProfile
from repositories.domain_repository import DomainRepository


def _configure_scalars(mock_session, first=None, one=None, all_rows=None):
    """mock_session.scalars() 是可等待 AsyncMock：await 后返回其 return_value。"""
    result = MagicMock()
    result.first.return_value = first
    result.one.return_value = one
    result.all.return_value = all_rows if all_rows is not None else []
    mock_session.scalars.return_value = result
    return result


class TestDomainRepository:
    @pytest.fixture
    def repo(self, mock_session):
        return DomainRepository(mock_session)

    # ---- create ----
    @pytest.mark.asyncio
    async def test_create_persists_fields(self, repo, mock_session):
        et = [{"code": "Org", "name": "组织"}]
        rt = [{"name": "属于", "description": "x"}]
        mock_session.refresh = AsyncMock()

        row = await repo.create(name="我的领域", entity_types=et, relation_types=rt,
                                description="desc", is_preset=False)

        added = mock_session.add.call_args[0][0]
        assert isinstance(added, DomainProfile)
        assert added.name == "我的领域"
        assert added.entity_types == et and added.relation_types == rt
        assert added.is_preset is False
        mock_session.commit.assert_awaited_once()
        assert row.name == "我的领域"

    # ---- get_by_id ----
    @pytest.mark.asyncio
    async def test_get_by_id_found(self, repo, mock_session):
        row = DomainProfile(id=str(uuid.uuid4()), name="投标")
        _configure_scalars(mock_session, first=row)
        result = await repo.get_by_id(row.id)
        assert result is row

    @pytest.mark.asyncio
    async def test_get_by_id_none(self, repo, mock_session):
        _configure_scalars(mock_session, first=None)
        assert await repo.get_by_id(str(uuid.uuid4())) is None

    @pytest.mark.asyncio
    async def test_get_by_id_bad_uuid(self, repo, mock_session):
        with pytest.raises(ValueError):
            await repo.get_by_id("not-a-uuid")

    # ---- list_all ----
    @pytest.mark.asyncio
    async def test_list_all(self, repo, mock_session):
        rows = [DomainProfile(id=str(uuid.uuid4()), name="A"),
                DomainProfile(id=str(uuid.uuid4()), name="B")]
        _configure_scalars(mock_session, all_rows=rows)
        assert await repo.list_all() == rows

    # ---- update ----
    @pytest.mark.asyncio
    async def test_update_fields(self, repo, mock_session):
        row = DomainProfile(id=str(uuid.uuid4()), name="旧名")
        _configure_scalars(mock_session, first=row)
        mock_session.refresh = AsyncMock()

        result = await repo.update(row.id, name="新名", entity_types=[{"code": "X"}])
        assert result is row
        assert row.name == "新名" and row.entity_types == [{"code": "X"}]
        mock_session.commit.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_update_missing_returns_none(self, repo, mock_session):
        _configure_scalars(mock_session, first=None)
        assert await repo.update(str(uuid.uuid4()), name="x") is None

    # ---- delete ----
    @pytest.mark.asyncio
    async def test_delete_found(self, repo, mock_session):
        row = DomainProfile(id=str(uuid.uuid4()), name="A")
        _configure_scalars(mock_session, first=row)
        result = await repo.delete(row.id)
        assert result is row
        mock_session.delete.assert_awaited_once()
        mock_session.commit.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_delete_missing_returns_none(self, repo, mock_session):
        _configure_scalars(mock_session, first=None)
        assert await repo.delete(str(uuid.uuid4())) is None

    # ---- KB 引用与绑定 ----
    @pytest.mark.asyncio
    async def test_count_kb_refs(self, repo, mock_session):
        _configure_scalars(mock_session, one=3)
        assert await repo.count_kb_refs(str(uuid.uuid4())) == 3

    @pytest.mark.asyncio
    async def test_list_kb_ids_by_profile(self, repo, mock_session):
        _configure_scalars(mock_session, all_rows=["kb1", "kb2"])
        assert await repo.list_kb_ids_by_profile(str(uuid.uuid4())) == ["kb1", "kb2"]

    @pytest.mark.asyncio
    async def test_bind_kb(self, repo, mock_session):
        from tests.helpers import MockKB

        kb = MockKB()
        _configure_scalars(mock_session, first=kb)
        mock_session.refresh = AsyncMock()
        profile_id = str(uuid.uuid4())

        result = await repo.bind_kb(kb.id, profile_id)
        assert result is kb and kb.domain_profile_id == profile_id
        mock_session.commit.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_bind_kb_missing(self, repo, mock_session):
        _configure_scalars(mock_session, first=None)
        assert await repo.bind_kb("missing", str(uuid.uuid4())) is None

    @pytest.mark.asyncio
    async def test_get_kb_domain_profile_id(self, repo, mock_session):
        pid = str(uuid.uuid4())
        _configure_scalars(mock_session, first=pid)
        assert await repo.get_kb_domain_profile_id("kb1") == pid
