"""
文档解析任务队列 — 基于 asyncio.Queue 的 FIFO 单 worker。

特点：
- 逐个解析，避免多文档同时解析抢占 CPU / Embedding API 资源
- 服务重启时队列清空（内存队列），但 PG 中 queued 状态的文档可通过重新触发解析恢复
"""
import asyncio

from utils.logging import get_logger

logger = get_logger(__name__)

# 最大队列容量
MAX_QUEUE_SIZE = 100

# 解析任务队列：(kb_id, doc_id)
_parse_queue: asyncio.Queue[tuple[str, str]] = asyncio.Queue(maxsize=MAX_QUEUE_SIZE)

# worker 运行状态
_worker_running = False


async def enqueue(kb_id: str, doc_id: str) -> int:
    """将解析任务加入队列。

    Returns:
        当前队列位置（1-based，1 表示下一个执行）
    """
    await _parse_queue.put((kb_id, doc_id))
    position = _parse_queue.qsize()
    logger.info("解析任务入队: doc_id=%s, queue_position=%d", doc_id, position)
    return position


def queue_size() -> int:
    """当前队列中的任务数（不含正在执行的）。"""
    return _parse_queue.qsize()


def is_worker_running() -> bool:
    """worker 是否在运行。"""
    return _worker_running


async def start_worker() -> None:
    """启动队列 worker，从队列中逐一出队并执行解析。

    需要在 FastAPI lifespan 启动阶段调用。
    """
    global _worker_running
    _worker_running = True
    logger.info("解析队列 worker 已启动")

    from services.document_service import parse_document

    while _worker_running:
        try:
            kb_id, doc_id = await _parse_queue.get()
            logger.info("解析任务出队: doc_id=%s, queue_remaining=%d", doc_id, _parse_queue.qsize())
            await parse_document(kb_id, doc_id)
            _parse_queue.task_done()
        except asyncio.CancelledError:
            logger.info("解析队列 worker 已取消")
            break
        except Exception as e:
            logger.error("解析队列 worker 异常: %s", e, exc_info=True)
            # 单个任务失败不影响后续任务
            try:
                _parse_queue.task_done()
            except ValueError:
                pass  # 如果 task_done 调用次数超过 put，忽略


async def stop_worker() -> None:
    """停止队列 worker。"""
    global _worker_running
    _worker_running = False
    logger.info("解析队列 worker 正在停止...")
