"""
词库管理 API — 词条 CRUD、词条×领域映射、CSV 导入导出、IK 远程词典导出、统计。
"""
import csv
import io

from fastapi import APIRouter, HTTPException
from fastapi.responses import PlainTextResponse

from models.schemas import (
    LexiconImportRequest,
    LexiconImportResponse,
    LexiconMappingsResponse,
    LexiconMappingsUpdate,
    LexiconStatsResponse,
    LexiconTermCreate,
    LexiconTermListResponse,
    LexiconTermResponse,
    LexiconTermUpdate,
)
from services import lexicon_service
from utils.logging import get_logger

logger = get_logger(__name__)

router = APIRouter(tags=["词库"])


@router.get("/api/lexicon/terms", response_model=LexiconTermListResponse)
async def list_terms(
    page: int = 1,
    page_size: int = 20,
    search: str = "",
):
    """词条分页列表（支持词条/别名搜索；附带 mappings_count）。"""
    result = await lexicon_service.list_terms(page, page_size, search)
    return result


@router.post("/api/lexicon/terms", response_model=LexiconTermResponse)
async def create_term(body: LexiconTermCreate):
    """新增词条。"""
    try:
        return await lexicon_service.create_term(
            body.term, body.aliases, body.enabled
        )
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))


@router.put("/api/lexicon/terms/{term_id}", response_model=LexiconTermResponse)
async def update_term(term_id: str, body: LexiconTermUpdate):
    """更新词条。"""
    try:
        return await lexicon_service.update_term(
            term_id,
            body.model_dump(exclude_unset=True),
        )
    except ValueError as e:
        raise HTTPException(status_code=404 if "不存在" in str(e) else 409, detail=str(e))


@router.delete("/api/lexicon/terms/{term_id}", status_code=204)
async def delete_term(term_id: str):
    """删除词条（连带删除其领域映射）。"""
    try:
        await lexicon_service.delete_term(term_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.get("/api/lexicon/terms/{term_id}/domain-mappings", response_model=LexiconMappingsResponse)
async def get_term_mappings(term_id: str):
    """词条领域映射（词条 × 领域模板 → 实体 code，含模板/实体中文名）。"""
    try:
        items = await lexicon_service.list_term_mappings(term_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return {"items": items}


@router.put("/api/lexicon/terms/{term_id}/domain-mappings", response_model=LexiconMappingsResponse)
async def put_term_mappings(term_id: str, body: LexiconMappingsUpdate):
    """整包替换词条领域映射（同一领域至多 1 个实体 code，code 须属于该领域模板）。"""
    try:
        items = await lexicon_service.replace_term_mappings(
            term_id, [m.model_dump() for m in body.mappings]
        )
    except ValueError as e:
        msg = str(e)
        status = 404 if "词条不存在" in msg else 400
        raise HTTPException(status_code=status, detail=msg)
    return {"items": items}


@router.post("/api/lexicon/import", response_model=LexiconImportResponse)
async def import_terms(body: LexiconImportRequest):
    """CSV 批量导入（每行：词条,别名1;别名2；旧文件第 3 列分类自动忽略；首行表头自动跳过）。"""
    return await lexicon_service.import_csv(body.content)


@router.get("/api/lexicon/export/ik-dict", response_class=PlainTextResponse)
async def export_ik_dict():
    """IK 远程词典：一行一个启用词条（ES 分词，Monitor 轮询热更新）。"""
    return PlainTextResponse(await lexicon_service.export_ik_dict())


@router.get("/api/lexicon/export/csv", response_class=PlainTextResponse)
async def export_csv():
    """导出全部词条 CSV（词条,别名）。"""
    return PlainTextResponse(await lexicon_service.export_csv())


@router.get("/api/lexicon/stats", response_model=LexiconStatsResponse)
async def stats():
    """词库统计（总数/启用数）。"""
    return await lexicon_service.get_stats()
