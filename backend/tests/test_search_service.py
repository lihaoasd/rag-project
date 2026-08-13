"""
Search Service 业务逻辑单元测试。
"""
from unittest.mock import AsyncMock, patch

import pytest

from services import search_service
from tests.helpers import KB_ID, DOC_ID


class TestSearch:
    @pytest.mark.asyncio
    async def test_search_returns_results(self, mock_retrieve):
        result = await search_service.search(
            kb_ids=[KB_ID],
            query="测试查询",
            top_k=10,
            similarity_threshold=0.6,
            offset=0,
            limit=20,
        )
        assert result["query"] == "测试查询"
        assert result["total"] == 1
        assert len(result["results"]) == 1
        assert result["results"][0]["rank"] == 1
        assert result["results"][0]["score"] == 0.95

    @pytest.mark.asyncio
    async def test_search_empty_results(self, mock_retrieve):
        mock_retrieve.return_value = []

        result = await search_service.search(
            kb_ids=[KB_ID],
            query="无结果查询",
        )
        assert result["total"] == 0
        assert result["results"] == []

    @pytest.mark.asyncio
    async def test_search_with_pagination(self, mock_retrieve):
        """测试分页：offset + limit。"""
        hits = [
            {"score": 0.9, "text": f"文本{i}", "doc_id": DOC_ID,
             "file_name": "test.pdf", "page_number": 1, "chunk_index": i}
            for i in range(10)
        ]
        mock_retrieve.return_value = hits

        result = await search_service.search(
            kb_ids=[KB_ID],
            query="分页测试",
            top_k=10,
            offset=3,
            limit=2,
        )
        # 应返回 offset=3 开始的 2 条
        assert len(result["results"]) == 2
        assert result["results"][0]["rank"] == 4  # offset + 1
        assert result["results"][1]["rank"] == 5

    @pytest.mark.asyncio
    async def test_search_offset_exceeds_results(self, mock_retrieve):
        hits = [
            {"score": 0.9, "text": "文本", "doc_id": DOC_ID,
             "file_name": "test.pdf", "page_number": 1, "chunk_index": 0}
        ]
        mock_retrieve.return_value = hits

        result = await search_service.search(
            kb_ids=[KB_ID],
            query="超出偏移",
            offset=10,
            limit=20,
        )
        assert result["total"] == 1
        assert result["results"] == []

    @pytest.mark.asyncio
    async def test_search_uses_correct_top_k(self, mock_retrieve):
        """验证 top_k 至少取 max(top_k, offset+limit)。"""
        mock_retrieve.return_value = []

        await search_service.search(
            kb_ids=[KB_ID],
            query="test",
            top_k=5,
            offset=20,
            limit=10,
        )
        # offset + limit = 30, 应使用 30 作为 top_k
        mock_retrieve.assert_called_once()
        call_kwargs = mock_retrieve.call_args.kwargs
        assert call_kwargs["top_k"] == 30
