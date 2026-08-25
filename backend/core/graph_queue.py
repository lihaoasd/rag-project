"""
图谱任务队列 — 基于 asyncio.Queue 的 FIFO 单 worker，独立于解析队列。

图谱构建依赖"文档已解析"（读 PG chunks）且大量调用 LLM，与解析队列互不阻塞。
任务类型：
- build_file: ("build_file", kb_id, doc_id)   单文件增量构建
- build_kb:   ("build_kb", kb_id)             全量构建（清空后逐文件构建）
- clear_file: ("clear_file", kb_id, doc_id)   清除单文件图谱
"""
import asyncio

from utils.logging import get_logger

logger = get_logger(__name__)

# 最大队列容量
MAX_QUEUE_SIZE = 50

# 队列项格式: (task_type, kb_id, doc_id)
_graph_queue: asyncio.Queue[tuple] = asyncio.Queue(maxsize=MAX_QUEUE_SIZE)

# worker 运行状态
_worker_running = False


async def enqueue(kb_id: str, doc_id: str) -> int:
    """将单文件图谱构建任务加入队列。

    Returns:
        当前队列位置（1-based，1 表示下一个执行）
    """
    await _graph_queue.put(("build_file", kb_id, doc_id))
    position = _graph_queue.qsize()
    logger.info("图谱构建任务入队: doc_id=%s, queue_position=%d", doc_id, position)
    return position


async def enqueue_build_kb(kb_id: str) -> int:
    """将全量图谱构建任务加入队列。"""
    await _graph_queue.put(("build_kb", kb_id, None))
    position = _graph_queue.qsize()
    logger.info("图谱全量构建任务入队: kb_id=%s, queue_position=%d", kb_id, position)
    return position


async def enqueue_clear_file(kb_id: str, doc_id: str) -> int:
    """将清除单文件图谱任务加入队列。"""
    await _graph_queue.put(("clear_file", kb_id, doc_id))
    position = _graph_queue.qsize()
    logger.info("图谱清除任务入队: doc_id=%s, queue_position=%d", doc_id, position)
    return position


def queue_size() -> int:
    """当前队列中的任务数（不含正在执行的）。"""
    return _graph_queue.qsize()


def is_worker_running() -> bool:
    """worker 是否在运行。"""
    return _worker_running


async def start_worker() -> None:
    """启动队列 worker，从队列中逐一出队并执行图谱任务。

    需要在 FastAPI lifespan 启动阶段调用。
    """
    global _worker_running
    _worker_running = True
    logger.info("图谱队列 worker 已启动")

    from services.graph_service import build_file_graph, build_kb_graph, clear_file_graph

    while _worker_running:
        try:
            task_type, kb_id, doc_id = await _graph_queue.get()
            logger.info(
                "图谱任务出队: type=%s, doc_id=%s, queue_remaining=%d",
                task_type, doc_id, _graph_queue.qsize(),
            )

            if task_type == "build_file":
                await build_file_graph(kb_id, doc_id)
            elif task_type == "build_kb":
                await build_kb_graph(kb_id)
            elif task_type == "clear_file":
                await clear_file_graph(kb_id, doc_id)

            _graph_queue.task_done()
        except asyncio.CancelledError:
            logger.info("图谱队列 worker 已取消")
            break
        except Exception as e:
            logger.error("图谱队列 worker 任务异常: %s", e, exc_info=True)
            try:
                _graph_queue.task_done()
            except ValueError:
                pass


async def stop_worker() -> None:
    """停止队列 worker。"""
    global _worker_running
    _worker_running = False
    logger.info("图谱队列 worker 正在停止...")
