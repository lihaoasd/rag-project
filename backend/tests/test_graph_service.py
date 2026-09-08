"""
图谱业务服务单元测试 — 写入语句构造 / 构建 / 清除（mock Neo4j 与 LLM）。
"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services import graph_service
from tests.helpers import KB_ID, DOC_ID, MockChunk, MockDocument


def _enable_graph():
    return patch.object(graph_service.settings, "graph_enabled", True)


@pytest.fixture(autouse=True)
def _mock_domain_profile():
    """v2：build_file_graph / update_entity 按 KB 解析领域模板 —— 单测固定回退内置通用预设。"""
    from services import domain_service

    with patch(
        "services.graph_service.domain_service.get_profile_for_kb",
        new_callable=AsyncMock,
        return_value=domain_service.default_profile(),
    ):
        yield


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
        stmts = graph_service._build_write_statements(
            KB_ID, DOC_ID, "test.pdf", entities, relationships,
            entity_codes={"Organization", "Project"}, valid_relations={"参与"},
        )
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
        stmts = graph_service._build_write_statements(
            KB_ID, DOC_ID, "f.pdf", entities, [],
            entity_codes={"Organization"}, valid_relations=set(),
        )
        # 模板外类型实体跳过：仅 Document 锚点
        assert len(stmts) == 1

    def test_illegal_relation_type_skipped(self):
        """实体在模板内但关系类型不在模板（如投标"中标"对通用模板）→ 关系丢弃。"""
        entities = [
            {"name": "甲公司", "type": "Organization", "description": "", "chunk_ids": ["c1"]},
            {"name": "乙公司", "type": "Organization", "description": "", "chunk_ids": ["c1"]},
        ]
        relationships = [
            {"source": "甲公司", "target": "乙公司", "type": "中标",
             "a_type": "Organization", "b_type": "Organization", "description": ""},
        ]
        stmts = graph_service._build_write_statements(
            KB_ID, DOC_ID, "f.pdf", entities, relationships,
            entity_codes={"Organization"}, valid_relations={"参与"},
        )
        # 2 实体 + 2 提及 + 0 关系 + 1 锚点 = 5
        assert len(stmts) == 5

    def test_relation_missing_endpoint_type_skipped(self):
        """v1 遗留回归：端点类型缺失不再默认补 "Event"，直接丢弃（端点存在性保证）。"""
        entities = [
            {"name": "甲公司", "type": "Organization", "description": "", "chunk_ids": ["c1"]},
            {"name": "乙公司", "type": "Organization", "description": "", "chunk_ids": ["c1"]},
        ]
        relationships = [
            {"source": "甲公司", "target": "乙公司", "type": "参与",
             "a_type": "Organization", "description": ""},  # b_type 缺失
        ]
        stmts = graph_service._build_write_statements(
            KB_ID, DOC_ID, "f.pdf", entities, relationships,
            entity_codes={"Organization"}, valid_relations={"参与"},
        )
        assert len(stmts) == 5  # 关系语句被丢弃


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


class TestListEntities:
    @pytest.mark.asyncio
    async def test_pagination_and_search(self):
        with _enable_graph(), \
             patch("services.graph_service.Neo4jStore.execute_read", new_callable=AsyncMock,
                   side_effect=[
                       [{"c": 3}],  # total
                       [
                           {"name": "甲公司", "type": "Organization", "description": "建筑公司",
                            "relation_count": 5, "chunk_count": 3},
                           {"name": "乙公司", "type": "Organization", "description": "",
                            "relation_count": 2, "chunk_count": 1},
                       ],
                   ]) as mock_read:
            result = await graph_service.list_entities(KB_ID, page=1, page_size=2, q="公司")

        assert result["total"] == 3
        assert len(result["items"]) == 2
        assert result["items"][0]["name"] == "甲公司"
        assert result["items"][0]["relation_count"] == 5
        assert result["items"][0]["chunk_count"] == 3
        # 搜索词与分页参数传入 Neo4j
        first_args = mock_read.await_args_list[0]
        assert first_args.args[1]["q"] == "公司"
        second_args = mock_read.await_args_list[1]
        assert second_args.args[1]["skip"] == 0 and second_args.args[1]["limit"] == 2

    @pytest.mark.asyncio
    async def test_empty(self):
        with _enable_graph(), \
             patch("services.graph_service.Neo4jStore.execute_read", new_callable=AsyncMock,
                   side_effect=[[{"c": 0}], []]) as mock_read:
            result = await graph_service.list_entities(KB_ID, q="不存在")
        assert result["total"] == 0 and result["items"] == []
        assert mock_read.await_count == 2

    @pytest.mark.asyncio
    async def test_disabled_raises(self):
        from core.neo4j_store import GraphDisabledError

        with pytest.raises(GraphDisabledError):
            await graph_service.list_entities(KB_ID)


class TestUpdateEntity:
    @pytest.mark.asyncio
    async def test_update_description_only(self):
        with _enable_graph(), \
             patch("services.graph_service.Neo4jStore.execute_read", new_callable=AsyncMock,
                   return_value=[{"c": 1}]) as mock_read, \
             patch("services.graph_service.Neo4jStore.execute_write", new_callable=AsyncMock) as mock_write:
            result = await graph_service.update_entity(
                KB_ID, "甲公司", "Organization", "甲公司", "Organization", "新描述"
            )
        assert result == {"name": "甲公司", "type": "Organization", "description": "新描述"}
        # 键未变化：只查一次（无冲突检测）
        assert mock_read.await_count == 1
        cypher = mock_write.await_args.args[0]
        assert "SET n.type = $new_type, n.name = $new_name" in cypher
        assert "REMOVE" not in cypher

    @pytest.mark.asyncio
    async def test_rename_with_type_change(self):
        with _enable_graph(), \
             patch("services.graph_service.Neo4jStore.execute_read", new_callable=AsyncMock,
                   side_effect=[[{"c": 1}], [{"c": 0}]]) as mock_read, \
             patch("services.graph_service.Neo4jStore.execute_write", new_callable=AsyncMock) as mock_write:
            result = await graph_service.update_entity(
                KB_ID, "甲公司", "Organization", "甲建设集团", "Project", ""
            )
        assert result["name"] == "甲建设集团" and result["type"] == "Project"
        assert mock_read.await_count == 2  # 存在性 + 冲突检测
        cypher = mock_write.await_args.args[0]
        assert "REMOVE n:`Organization` SET n:`Project`" in cypher
        assert "SET n.type = $new_type, n.name = $new_name" in cypher

    @pytest.mark.asyncio
    async def test_conflict_raises(self):
        from services.graph_service import EntityConflictError

        with _enable_graph(), \
             patch("services.graph_service.Neo4jStore.execute_read", new_callable=AsyncMock,
                   side_effect=[[{"c": 1}], [{"c": 1}]]) as mock_read:
            with pytest.raises(EntityConflictError):
                await graph_service.update_entity(
                    KB_ID, "甲公司", "Organization", "乙公司", "Organization", ""
                )

    @pytest.mark.asyncio
    async def test_not_found_returns_none(self):
        with _enable_graph(), \
             patch("services.graph_service.Neo4jStore.execute_read", new_callable=AsyncMock,
                   return_value=[{"c": 0}]) as mock_read:
            result = await graph_service.update_entity(
                KB_ID, "不存在", "Organization", "甲公司", "Organization", ""
            )
        assert result is None
        assert mock_read.await_count == 1

    @pytest.mark.asyncio
    async def test_illegal_type_raises(self):
        with _enable_graph():
            with pytest.raises(ValueError):
                await graph_service.update_entity(
                    KB_ID, "甲公司", "Organization", "甲公司", "Hacker", ""
                )
