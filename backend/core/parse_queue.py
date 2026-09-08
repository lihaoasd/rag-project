"""
文档解析任务队列 — 基于 asyncio.Queue 的 FIFO 单 worker。

特点：
- 逐个解析，避免多文档同时解析抢占 CPU / Embedding API 资源
- 服务重启时队列清空（内存队列），但 PG 中 queued 状态的文档可通过重新触发解析恢复
- 支持两种任务类型：parse（解析文档）和 rechunk（重新切片）
"""
import asyncio

from utils.logging import get_logger

logger = get_logger(__name__)

# 最大队列容量
MAX_QUEUE_SIZE = 100

# 队列项格式: (task_type, kb_id, doc_id, chunk_size, chunk_overlap)
# task_type: "parse" | "rechunk" | "reparse" | "resume"
_parse_queue: asyncio.Queue[tuple] = asyncio.Queue(maxsize=MAX_QUEUE_SIZE)

# worker 运行状态
_worker_running = False


async def enqueue(kb_id: str, doc_id: str) -> int:
    """将解析任务加入队列。

    Returns:
        当前队列位置（1-based，1 表示下一个执行）
    """
    await _parse_queue.put(("parse", kb_id, doc_id, None, None))
    position = _parse_queue.qsize()
    logger.info("解析任务入队: doc_id=%s, queue_position=%d", doc_id, position)
    return position


async def enqueue_rechunk(
    kb_id: str, doc_id: str, chunk_size: int | None = None, chunk_overlap: int | None = None
) -> int:
    """将重新切片任务加入队列。

    Returns:
        当前队列位置（1-based，1 表示下一个执行）
    """
    await _parse_queue.put(("rechunk", kb_id, doc_id, chunk_size, chunk_overlap))
    position = _parse_queue.qsize()
    logger.info("重切片任务入队: doc_id=%s, queue_position=%d", doc_id, position)
    return position


async def enqueue_reparse(kb_id: str, doc_id: str) -> int:
    """将重新解析任务加入队列。

    Returns:
        当前队列位置（1-based，1 表示下一个执行）
    """
    await _parse_queue.put(("reparse", kb_id, doc_id, None, None))
    position = _parse_queue.qsize()
    logger.info("重新解析任务入队: doc_id=%s, queue_position=%d", doc_id, position)
    return position


async def enqueue_resume(kb_id: str, doc_id: str) -> int:
    """将“继续解析（续跑 partial 文档）”任务加入队列。

    Returns:
        当前队列位置（1-based，1 表示下一个执行）
    """
    await _parse_queue.put(("resume", kb_id, doc_id, None, None))
    position = _parse_queue.qsize()
    logger.info("续跑任务入队: doc_id=%s, queue_position=%d", doc_id, position)
    return position


def queue_size() -> int:
    """当前队列中的任务数（不含正在执行的）。"""
    return _parse_queue.qsize()


def is_worker_running() -> bool:
    """worker 是否在运行。"""
    return _worker_running


async def start_worker() -> None:
    """启动队列 worker，从队列中逐一出队并执行解析或重切片。

    需要在 FastAPI lifespan 启动阶段调用。
    """
    global _worker_running
    _worker_running = True
    logger.info("解析队列 worker 已启动")

    from services.document_service import (
        parse_document,
        rechunk_document,
        reparse_document,
        resume_document,
    )

    while _worker_running:
        try:
            task_type, kb_id, doc_id, chunk_size, chunk_overlap = await _parse_queue.get()
            logger.info(
                "任务出队: type=%s, doc_id=%s, queue_remaining=%d",
                task_type, doc_id, _parse_queue.qsize(),
            )

            if task_type == "rechunk":
                await rechunk_document(kb_id, doc_id, chunk_size, chunk_overlap)
            elif task_type == "reparse":
                await reparse_document(kb_id, doc_id)
            elif task_type == "resume":
                await resume_document(kb_id, doc_id)
            else:
                await parse_document(kb_id, doc_id)

            _parse_queue.task_done()
        except asyncio.CancelledError:
            logger.info("解析队列 worker 已取消")
            break
        except Exception as e:
            logger.error("队列 worker 任务异常: %s", e, exc_info=True)
            try:
                _parse_queue.task_done()
            except ValueError:
                pass


async def stop_worker() -> None:
    """停止队列 worker。"""
    global _worker_running
    _worker_running = False
    logger.info("解析队列 worker 正在停止...")
