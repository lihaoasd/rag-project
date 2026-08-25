"""
图谱业务服务单元测试 — 写入语句构造 / 构建 / 清除（mock Neo4j 与 LLM）。
"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services import graph_service
from tests.helpers import KB_ID, DOC_ID, MockChunk, MockDocument


def _enable_graph():
    return patch.object(graph_service.settings, "graph_enabled", True)


class TestBuildWriteStatements:
    def test_entities_and_relations(self):
        entities = [
            {"name": "甲公司", "type": "Organization", "description": "建筑公司", "chunk_ids": ["c1"]},
            {"name": "XX项目", "type": "Project", "description": "", "chunk_ids": ["c1"]},
        ]
        relationships = [
            {"source": "甲公司", "target": "XX项目", "type": "参与",
             "a_type": "Organization", "b_type": "Project", "description": ""},
        ]
        stmts = graph_service._build_write_statements(KB_ID, DOC_ID, "test.pdf", entities, relationships)
        # 实体 MERGE ×2 + 提及 ×2 + 关系 ×1 + Document 锚点 ×1 = 6
        assert len(stmts) == 6
        cyphers = [c for c, _ in stmts]
        assert any(":Organization" in c for c in cyphers)
        assert any(":Project" in c for c in cyphers)
        assert any("参与" in c for c in cyphers)
        assert any("提及" in c for c in cyphers)

    def test_illegal_entity_type_skipped(self):
        entities = [
            {"name": "未知", "type": "Hacker", "description": "", "chunk_ids": ["c1"]},
        ]
        stmts = graph_service._build_write_statements(KB_ID, DOC_ID, "f.pdf", entities, [])
        # 非法类型实体跳过：仅 Document 锚点
        assert len(stmts) == 1

    def test_illegal_relation_type_skipped(self):
        """通用化：旧投标关系类型（中标/招标/分包等）已不在白名单，应被丢弃。"""
        entities = [
            {"name": "甲公司", "type": "Organization", "description": "", "chunk_ids": ["c1"]},
            {"name": "乙公司", "type": "Organization", "description": "", "chunk_ids": ["c1"]},
        ]
        relationships = [
            {"source": "甲公司", "target": "乙公司", "type": "中标",
             "a_type": "Organization", "b_type": "Organization", "description": ""},
        ]
        stmts = graph_service._build_write_statements(KB_ID, DOC_ID, "f.pdf", entities, relationships)
        # 2 实体 + 2 提及 + 0 关系 + 1 锚点 = 5
        assert len(stmts) == 5


class TestBuildFileGraph:
    @pytest.mark.asyncio
    async def test_success(self, mock_async_session, mock_session):
        repo = MagicMock()
        repo.get_by_id = AsyncMock(return_value=MockDocument(status="completed"))
        repo.list_chunks_for_graph = AsyncMock(return_value=[
            MockChunk(text_content="甲公司中标 XX项目，金额 1000万元"),
            MockChunk(id="c2", chunk_index=1, text_content="另一段文字"),
        ])
        repo.update_graph_status = AsyncMock()
        extract_result = {
            "entities": [
                {"name": "甲公司", "type": "Organization", "description": "", "chunk_ids": ["c1"]},
            ],
            "relationships": [],
        }
        with _enable_graph(), \
             patch("services.graph_service.DocumentRepository", return_value=repo), \
             patch("services.graph_service._clear_file_contributions", new_callable=AsyncMock), \
             patch("services.graph_service.extract_from_chunks", new_callable=AsyncMock, return_value=extract_result), \
             patch("services.graph_service.Neo4jStore.execute_write_many", new_callable=AsyncMock) as mock_write:
            await graph_service.build_file_graph(KB_ID, DOC_ID)

        mock_write.assert_awaited_once()
        # 状态落库：building → completed
        calls = [c.args for c in repo.update_graph_status.await_args_list]
        assert calls[0][0] == DOC_ID and calls[0][1] == "building"
        assert calls[-1][0] == DOC_ID and calls[-1][1] == "completed"

    @pytest.mark.asyncio
    async def test_skips_unparsed_document(self, mock_async_session, mock_session):
        repo = MagicMock()
        repo.get_by_id = AsyncMock(return_value=MockDocument(status="uploaded"))
        repo.update_graph_status = AsyncMock()
        with _enable_graph(), \
             patch("services.graph_service.DocumentRepository", return_value=repo), \
             patch("services.graph_service.Neo4jStore.execute_write_many", new_callable=AsyncMock) as mock_write:
            await graph_service.build_file_graph(KB_ID, DOC_ID)
        # 未解析文档：不写图、不改状态
        mock_write.assert_not_called()
        repo.update_graph_status.assert_not_called()

    @pytest.mark.asyncio
    async def test_disabled_raises(self, mock_async_session, mock_session):
        from core.neo4j_store import GraphDisabledError

        repo = MagicMock()
        repo.get_by_id = AsyncMock(return_value=MockDocument(status="completed"))
        repo.update_graph_status = AsyncMock()
        with patch("services.graph_service.DocumentRepository", return_value=repo):
            with pytest.raises(GraphDisabledError):
                await graph_service.build_file_graph(KB_ID, DOC_ID)


class TestClearFileGraph:
    @pytest.mark.asyncio
    async def test_clear_success(self, mock_async_session, mock_session):
        repo = MagicMock()
        repo.update_graph_status = AsyncMock()
        with _enable_graph(), \
             patch("services.graph_service.DocumentRepository", return_value=repo), \
             patch("services.graph_service._clear_file_contributions", new_callable=AsyncMock) as mock_clear:
            await graph_service.clear_file_graph(KB_ID, DOC_ID)
        mock_clear.assert_awaited_once_with(KB_ID, DOC_ID)
        repo.update_graph_status.assert_awaited_once_with(DOC_ID, "none")


class TestBuildKbGraph:
    @pytest.mark.asyncio
    async def test_build_all_completed(self, mock_async_session, mock_session):
        repo = MagicMock()
        repo.list_by_kb = AsyncMock(return_value=[
            MockDocument(status="completed"),
            MockDocument(id="doc2", status="completed"),
            MockDocument(id="doc3", status="uploaded"),  # 未解析不构建
        ])
        repo.reset_graph_status_by_kb = AsyncMock(return_value=3)
        with _enable_graph(), \
             patch("services.graph_service.DocumentRepository", return_value=repo), \
             patch("services.graph_service.Neo4jStore.execute_write", new_callable=AsyncMock), \
             patch("services.graph_service.build_file_graph", new_callable=AsyncMock) as mock_build:
            await graph_service.build_kb_graph(KB_ID)
        # 仅 completed 的 2 个文件构建
        assert mock_build.await_count == 2
