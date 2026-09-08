"""
Parse Queue 单元测试 — 任务队列的入队、出队、生命周期。
"""
import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from core import parse_queue
from tests.helpers import KB_ID, DOC_ID


class TestEnqueue:
    @pytest.mark.asyncio
    async def test_enqueue_adds_task(self):
        # 清空队列
        while not parse_queue._parse_queue.empty():
            parse_queue._parse_queue.get_nowait()

        position = await parse_queue.enqueue(KB_ID, DOC_ID)
        assert position == 1
        assert parse_queue.queue_size() == 1

    @pytest.mark.asyncio
    async def test_enqueue_multiple_tasks(self):
        while not parse_queue._parse_queue.empty():
            parse_queue._parse_queue.get_nowait()

        await parse_queue.enqueue(KB_ID, DOC_ID)
        await parse_queue.enqueue(KB_ID, "another-doc-id")
        assert parse_queue.queue_size() == 2

        # 队列位置
        position = await parse_queue.enqueue(KB_ID, "third-doc-id")
        assert position == 3


class TestEnqueueRechunk:
    @pytest.mark.asyncio
    async def test_enqueue_rechunk_adds_task(self):
        while not parse_queue._parse_queue.empty():
            parse_queue._parse_queue.get_nowait()

        position = await parse_queue.enqueue_rechunk(KB_ID, DOC_ID, 1024, 128)
        assert position == 1
        assert parse_queue.queue_size() == 1

    @pytest.mark.asyncio
    async def test_enqueue_rechunk_default_params(self):
        while not parse_queue._parse_queue.empty():
            parse_queue._parse_queue.get_nowait()

        position = await parse_queue.enqueue_rechunk(KB_ID, DOC_ID)
        assert position == 1


class TestEnqueueResume:
    @pytest.mark.asyncio
    async def test_enqueue_resume_adds_task(self):
        while not parse_queue._parse_queue.empty():
            parse_queue._parse_queue.get_nowait()

        position = await parse_queue.enqueue_resume(KB_ID, DOC_ID)
        assert position == 1
        assert parse_queue.queue_size() == 1

        # 队列项为 ("resume", kb_id, doc_id, None, None)
        task = parse_queue._parse_queue.get_nowait()
        assert task[0] == "resume"
        assert task[1] == KB_ID
        assert task[2] == DOC_ID


class TestQueueSize:
    def test_queue_size_empty(self):
        while not parse_queue._parse_queue.empty():
            parse_queue._parse_queue.get_nowait()
        assert parse_queue.queue_size() == 0

    @pytest.mark.asyncio
    async def test_queue_size_after_enqueue(self):
        while not parse_queue._parse_queue.empty():
            parse_queue._parse_queue.get_nowait()

        await parse_queue.enqueue(KB_ID, DOC_ID)
        assert parse_queue.queue_size() == 1


class TestWorkerRunning:
    def test_is_worker_running_default_false(self):
        # 模块级变量默认 False（除非已在其他地方启动）
        # 重置状态
        parse_queue._worker_running = False
        assert parse_queue.is_worker_running() is False

    def test_is_worker_running_when_started(self):
        parse_queue._worker_running = True
        assert parse_queue.is_worker_running() is True
        parse_queue._worker_running = False  # 恢复


class TestStartStopWorker:
    @pytest.mark.asyncio
    async def test_stop_worker_stops_loop(self):
        """测试 stop_worker 设置标志位。"""
        # 重置
        parse_queue._worker_running = True
        await parse_queue.stop_worker()
        assert parse_queue._worker_running is False

    @pytest.mark.asyncio
    async def test_start_worker_processes_tasks(self):
        """测试 worker 能处理入队的任务。"""
        while not parse_queue._parse_queue.empty():
            parse_queue._parse_queue.get_nowait()

        # Mock 依赖
        with patch("services.document_service.parse_document", new_callable=AsyncMock) as mock_parse:
            await parse_queue.enqueue(KB_ID, DOC_ID)

            # 启动 worker 并快速停止
            parse_queue._worker_running = True

            # 创建 worker 任务，让它处理完一个任务后退出
            async def run_one():
                task_type, kb_id, doc_id, cs, co = await parse_queue._parse_queue.get()
                assert task_type == "parse"
                assert kb_id == KB_ID
                assert doc_id == DOC_ID
                await mock_parse(doc_id, kb_id)
                parse_queue._parse_queue.task_done()

            await run_one()

            mock_parse.assert_called_once()
