"""
模型配置管理 API。
"""
from fastapi import APIRouter, HTTPException, Query

from models.schemas import (
    ModelConfigCreate,
    ModelConfigResponse,
    ModelConfigUpdate,
    ModelTestRequest,
    ModelTestResponse,
)
from services import model_service
from utils.logging import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/api/models", tags=["模型管理"])


@router.get("", response_model=list[ModelConfigResponse])
async def list_models(
    model_type: str | None = Query(None, description="按类型筛选"),
    platform: str | None = Query(None, description="按平台筛选"),
):
    """模型配置列表"""
    return await model_service.list_models(model_type=model_type, platform=platform)


@router.post("", response_model=ModelConfigResponse, status_code=201)
async def create_model(body: ModelConfigCreate):
    """创建模型配置"""
    try:
        return await model_service.create_model(
            name=body.name,
            model_type=body.model_type,
            platform=body.platform,
            model=body.model,
            api_base=body.api_base,
            api_key=body.api_key,
            params=body.params,
            is_default=body.is_default,
        )
    except Exception as e:
        logger.error("创建模型配置失败: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/test", response_model=ModelTestResponse)
async def test_model_connection(body: ModelTestRequest):
    """测试模型连接可用性（不落库）"""
    return await model_service.test_model(
        model_type=body.model_type,
        platform=body.platform,
        model=body.model,
        api_base=body.api_base,
        api_key=body.api_key,
        params=body.params,
    )


@router.get("/{model_id}", response_model=ModelConfigResponse)
async def get_model(model_id: str):
    """模型配置详情"""
    result = await model_service.get_model(model_id)
    if result is None:
        raise HTTPException(status_code=404, detail="模型配置不存在")
    return result


@router.put("/{model_id}", response_model=ModelConfigResponse)
async def update_model(model_id: str, body: ModelConfigUpdate):
    """更新模型配置"""
    kwargs = body.model_dump(exclude_unset=True)
    result = await model_service.update_model(model_id, **kwargs)
    if result is None:
        raise HTTPException(status_code=404, detail="模型配置不存在")
    return result


@router.delete("/{model_id}", status_code=204)
async def delete_model(model_id: str):
    """删除模型配置（默认/被绑定模型不可删除）"""
    success, error = await model_service.delete_model(model_id)
    if not success:
        raise HTTPException(status_code=400, detail=error)
    logger.info("模型配置已删除: id=%s", model_id)


@router.post("/{model_id}/set-default", response_model=ModelConfigResponse)
async def set_default(model_id: str):
    """设为该类型全局默认（同类型互斥）"""
    result = await model_service.set_default(model_id)
    if result is None:
        raise HTTPException(status_code=404, detail="模型配置不存在")
    return result
