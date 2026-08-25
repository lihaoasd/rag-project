"""
图谱任务队列单元测试 — 入队/状态（不启动 worker）。
"""
import pytest

from core import graph_queue
from tests.helpers import KB_ID, DOC_ID


def _drain_queue():
    """清空队列，避免测试间相互影响。"""
    while not graph_queue._graph_queue.empty():
        graph_queue._graph_queue.get_nowait()
        graph_queue._graph_queue.task_done()


@pytest.fixture(autouse=True)
def clean_queue():
    yield
    _drain_queue()


class TestGraphQueue:
    @pytest.mark.asyncio
    async def test_enqueue_build_file(self):
        pos = await graph_queue.enqueue(KB_ID, DOC_ID)
        assert pos >= 1
        assert graph_queue.queue_size() >= 1

    @pytest.mark.asyncio
    async def test_enqueue_build_kb(self):
        pos = await graph_queue.enqueue_build_kb(KB_ID)
        assert pos >= 1

    @pytest.mark.asyncio
    async def test_enqueue_clear_file(self):
        pos = await graph_queue.enqueue_clear_file(KB_ID, DOC_ID)
        assert pos >= 1

    @pytest.mark.asyncio
    async def test_worker_not_running_by_default(self):
        assert graph_queue.is_worker_running() is False

    @pytest.mark.asyncio
    async def test_queue_item_format(self):
        await graph_queue.enqueue(KB_ID, DOC_ID)
        task_type, kb_id, doc_id = graph_queue._graph_queue.get_nowait()
        assert task_type == "build_file"
        assert kb_id == KB_ID
        assert doc_id == DOC_ID
        graph_queue._graph_queue.task_done()
