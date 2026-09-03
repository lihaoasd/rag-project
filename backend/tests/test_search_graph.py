"""
检索集成知识图谱（graph 通道）测试 — 单选/多选 RRF 融合/未启用降级。
"""
from unittest.mock import AsyncMock, patch

import pytest

from services import search_service
from tests.helpers import DOC_ID, KB_ID


def _graph_evidence() -> dict:
    return {
        "nodes": [{"id": "Project_X", "name": "X", "type": "Project", "degree": 10}],
        "edges": [],
        "evidence": [
            {
                "entity": "甲公司",
                "chunks": [
                    {
                        "doc_id": DOC_ID, "file_name": "test.pdf", "chunk_id": "c1",
                        "chunk_index": 0, "page_number": 1, "text": "甲公司相关内容",
                    },
                ],
            },
        ],
    }


class TestGraphMethod:
    @pytest.mark.asyncio
    async def test_graph_single_returns_evidence(self, mock_retrieve):
        with patch("config.settings.graph_enabled", True), \
             patch("services.graph_service.search_subgraph", new_callable=AsyncMock,
                   return_value=_graph_evidence()) as mock_graph:
            result = await search_service.search(
                kb_ids=[KB_ID], query="甲公司", retrieval_methods=["graph"], top_k=10
            )
        mock_graph.assert_awaited_once()
        assert result["total"] == 1
        item = result["results"][0]
        assert item["graph_entity"] == "甲公司"
        assert item["text"] == "甲公司相关内容"
        assert item["doc_id"] == DOC_ID

    @pytest.mark.asyncio
    async def test_graph_disabled_returns_empty(self, mock_retrieve):
        # conftest 默认 graph_enabled=False：图通道降级为空，不调图检索
        with patch("services.graph_service.search_subgraph", new_callable=AsyncMock) as mock_graph:
            result = await search_service.search(
                kb_ids=[KB_ID], query="甲公司", retrieval_methods=["graph"], top_k=10
            )
        mock_graph.assert_not_awaited()
        assert result["total"] == 0

    @pytest.mark.asyncio
    async def test_dense_plus_graph_rrf(self, mock_retrieve):
        mock_retrieve.return_value = [
            {"score": 0.9, "text": "向量结果", "doc_id": DOC_ID,
             "file_name": "a.pdf", "page_number": 1, "chunk_index": 3},  # 与图证据不同切片
        ]
        with patch("config.settings.graph_enabled", True), \
             patch("services.graph_service.search_subgraph", new_callable=AsyncMock,
                   return_value=_graph_evidence()):
            result = await search_service.search(
                kb_ids=[KB_ID], query="甲公司", retrieval_methods=["dense", "graph"], top_k=10
            )
        assert result["total"] == 2
        entities = {r.get("graph_entity") for r in result["results"]}
        assert "甲公司" in entities
        assert None in entities  # 向量结果无 graph_entity

    @pytest.mark.asyncio
    async def test_graph_dedup_same_chunk(self, mock_retrieve):
        """同一切片被多个实体提到时去重，graph_entity 合并。"""
        evidence = _graph_evidence()
        evidence["evidence"].append({
            "entity": "乙公司",
            "chunks": [
                {"doc_id": DOC_ID, "file_name": "test.pdf", "chunk_id": "c1",
                 "chunk_index": 0, "page_number": 1, "text": "甲公司相关内容"},
            ],
        })
        with patch("config.settings.graph_enabled", True), \
             patch("services.graph_service.search_subgraph", new_callable=AsyncMock,
                   return_value=evidence):
            result = await search_service.search(
                kb_ids=[KB_ID], query="甲", retrieval_methods=["graph"], top_k=10
            )
        assert result["total"] == 1
        assert result["results"][0]["graph_entity"] == "甲公司、乙公司"
