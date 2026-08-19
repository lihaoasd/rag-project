"""
ES 对账与重建业务逻辑 — PG（权威源）vs ES（派生数据）一致性维护。

- reconcile_status(): 只读体检，输出各知识库 PG/ES 切片数差异；
- rebuild_es(): 修复动作，按知识库从 PG 全量重灌 ES（治愈双写漂移）。
"""
from config import settings
from core import es_store
from core.database import async_session
from repositories.document_repository import DocumentRepository
from utils.logging import get_logger

logger = get_logger(__name__)

_REBUILD_BATCH = 100


async def reconcile_status() -> dict:
    """对账：对比 PG 与 ES 各知识库切片数（只读）。"""
    healthy = await es_store.ping()
    es_counts = await es_store.count_by_kb()
    async with async_session() as session:
        pg_counts = await DocumentRepository(session).count_chunks_by_kb()

    all_kbs = sorted(set(pg_counts) | set(es_counts))
    differences = [
        {"kb_id": kb, "pg": pg_counts.get(kb, 0), "es": es_counts.get(kb, 0)}
        for kb in all_kbs
        if pg_counts.get(kb, 0) != es_counts.get(kb, 0)
    ]
    return {
        "healthy": healthy,
        "index": settings.es_index_name,
        "pg_total": sum(pg_counts.values()),
        "es_total": sum(es_counts.values()),
        "diff_count": len(differences),
        "differences": differences,
    }


async def rebuild_es(kb_id: str | None = None) -> dict:
    """从 PG 全量重建 ES：删除目标知识库 ES 数据 → 读 PG 切片 → bulk 写入。

    Args:
        kb_id: 指定知识库；None 表示重建全部知识库。

    Returns:
        {kb_id 或 "all", rebuilt_chunks, skipped_kbs(无切片)}
    """
    # 1. 确定重建范围
    async with async_session() as session:
        doc_repo = DocumentRepository(session)
        target_kbs = [kb_id] if kb_id is not None else sorted(
            (await doc_repo.count_chunks_by_kb()).keys()
        )

    total = 0
    skipped: list[str] = []
    for kid in target_kbs:
        # 2. 清理旧 ES 数据
        await es_store.delete_by_kb(kid)

        # 3. 读 PG 切片 + 文档名映射
        async with async_session() as session:
            doc_repo = DocumentRepository(session)
            chunks = await doc_repo.list_chunks_by_kb(kid)
            docs = await doc_repo.list_by_kb(kid)
        file_names = {str(d.id): d.file_name for d in docs}
        if not chunks:
            skipped.append(kid)
            continue

        # 4. 分批 bulk 写入
        for i in range(0, len(chunks), _REBUILD_BATCH):
            batch = chunks[i : i + _REBUILD_BATCH]
            texts: list[str] = []
            metadatas: list[dict] = []
            for c in batch:
                meta = c.chunk_metadata or {}
                texts.append(c.text_content or "")
                metadatas.append(
                    {
                        "doc_id": str(c.doc_id),
                        "file_name": file_names.get(str(c.doc_id), ""),
                        "page_number": c.page_number,
                        "chunk_index": c.chunk_index,
                        "chunk_type": meta.get("chunk_type", "text"),
                        "source_chunk_index": meta.get("source_chunk_index", -1),
                        "heading": meta.get("heading", ""),
                        "heading_path": meta.get("heading_path", ""),
                    }
                )
            await es_store.bulk_index(texts, metadatas, kid)
        total += len(chunks)
        logger.info("ES rebuild 完成: kb_id=%s, chunks=%d", kid, len(chunks))

    return {
        "kb_id": kb_id or "all",
        "rebuilt_chunks": total,
        "skipped_kbs": skipped,
    }
