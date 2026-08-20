"""
ES 对账与重建业务逻辑 — PG（权威源）vs ES（派生数据）一致性维护。

- reconcile_status(): 只读体检，输出各知识库 PG/ES 切片数差异；
- rebuild_es(): 修复动作，按知识库从 PG 全量重灌 ES（治愈双写漂移）。
"""
from config import settings
from core import es_store
from core.database import async_session
from repositories.document_repository import DocumentRepository
from repositories.kb_repository import KnowledgeBaseRepository
from utils.logging import get_logger

logger = get_logger(__name__)

_REBUILD_BATCH = 100
_DEFAULT_PAGE_SIZE = 20


async def reconcile_status(page: int = 1, page_size: int = _DEFAULT_PAGE_SIZE) -> dict:
    """对账：PG（权威源）vs ES（派生数据）的切片数/文档级差异（只读）。

    Args:
        page: 知识库列表页码（从 1 开始）。
        page_size: 每页知识库数量。

    Returns:
        {
          healthy, index, index_stats,           # ES 自身健康/存储
          pg_total, es_total, diff_count,         # 总数与差异库数
          kbs: [{kb_id, kb_name, pg, es, status, pg_docs, es_docs}],  # 全库三态（分页）
          kb_total,                              # 知识库总数（分页用）
          differences: [{kb_id, pg, es}],          # 兼容旧字段（非 ok 的库）
          doc_diffs: [{kb_id, doc_id, file_name, pg, es}],    # 文档级差异
          doc_diff_count,
        }
    """
    healthy = await es_store.ping()

    async with async_session() as session:
        repo = DocumentRepository(session)
        pg_kb_chunks = await repo.count_chunks_by_kb()

    # ES 不可达：连接问题单独表达（healthy=False），不混入"数据差异"误报
    if not healthy:
        return {
            "healthy": False,
            "index": settings.es_index_name,
            "index_stats": None,
            "pg_total": sum(pg_kb_chunks.values()),
            "es_total": 0,
            "diff_count": 0,
            "differences": [],
            "kbs": [],
            "kb_total": 0,
            "doc_diffs": [],
            "doc_diff_count": 0,
        }

    es_kb = await es_store.kb_stats()
    es_doc_counts = await es_store.count_by_doc()
    index_stats = await es_store.index_stats()

    async with async_session() as session:
        repo = DocumentRepository(session)
        pg_kb_docs = await repo.count_docs_by_kb()
        pg_doc_counts = await repo.count_chunks_by_doc()
        doc_meta = await repo.list_doc_meta()
        kb_names = {kb.id: kb.name for kb in await KnowledgeBaseRepository(session).list_all()}

    # 1. 全库三态
    kbs: list[dict] = []
    for kb in sorted(set(pg_kb_chunks) | set(es_kb)):
        pg = pg_kb_chunks.get(kb, 0)
        es = es_kb.get(kb, {}).get("chunks", 0)
        if pg == es:
            status = "ok"
        elif pg > es:
            status = "missing"
        else:
            status = "residual"
        kbs.append({
            "kb_id": kb,
            "kb_name": kb_names.get(kb, ""),
            "pg": pg,
            "es": es,
            "status": status,
            "pg_docs": pg_kb_docs.get(kb, 0),
            "es_docs": es_kb.get(kb, {}).get("docs", 0),
        })

    differences = [
        {"kb_id": k["kb_id"], "pg": k["pg"], "es": k["es"]}
        for k in kbs
        if k["status"] != "ok"
    ]
    kb_total = len(kbs)

    # 2. 知识库列表分页（diff_count / kb_total 仍按全量统计）
    start = (page - 1) * page_size
    kbs = kbs[start : start + page_size]

    # 3. 文档级差异（输出上限 500 条防超长响应）
    doc_diffs: list[dict] = []
    for doc_id in sorted(set(pg_doc_counts) | set(es_doc_counts)):
        pg = pg_doc_counts.get(doc_id, 0)
        es = es_doc_counts.get(doc_id, 0)
        if pg != es:
            meta = doc_meta.get(doc_id, {})
            doc_diffs.append({
                "kb_id": meta.get("kb_id", ""),
                "doc_id": doc_id,
                "file_name": meta.get("file_name", ""),
                "pg": pg,
                "es": es,
            })
            if len(doc_diffs) >= 500:
                break

    return {
        "healthy": healthy,
        "index": settings.es_index_name,
        "index_stats": index_stats,
        "pg_total": sum(pg_kb_chunks.values()),
        "es_total": sum(v["chunks"] for v in es_kb.values()),
        "diff_count": len(differences),
        "differences": differences,
        "kbs": kbs,
        "kb_total": kb_total,
        "doc_diffs": doc_diffs,
        "doc_diff_count": len(doc_diffs),
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
