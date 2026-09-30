"""
模型配置管理 API — 模型供应商 + 模型配置 + 连接测试。
"""
from fastapi import APIRouter, HTTPException, Query

from models.schemas import (
    ModelConfigCreate,
    ModelConfigResponse,
    ModelConfigUpdate,
    ModelProviderCreate,
    ModelProviderResponse,
    ModelProviderUpdate,
    ProviderModelListResponse,
    ModelTestRequest,
    ModelTestResponse,
)
from services import model_service
from utils.logging import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/api/models", tags=["模型管理"])


# ============================================================
# 模型供应商
# ============================================================
@router.get("/providers", response_model=list[ModelProviderResponse])
async def list_providers():
    """模型供应商列表（含各供应商下模型数）"""
    return await model_service.list_providers()


@router.post("/providers", response_model=ModelProviderResponse, status_code=201)
async def create_provider(body: ModelProviderCreate):
    """创建模型供应商"""
    try:
        return await model_service.create_provider(
            name=body.name,
            platform=body.platform,
            api_base=body.api_base,
            api_key=body.api_key,
            params=body.params,
            is_active=body.is_active,
        )
    except Exception as e:
        logger.error("创建模型供应商失败: %s", e, exc_info=True)
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/providers/{provider_id}", response_model=ModelProviderResponse)
async def get_provider(provider_id: str):
    """模型供应商详情"""
    result = await model_service.get_provider(provider_id)
    if result is None:
        raise HTTPException(status_code=404, detail="供应商不存在")
    return result


@router.put("/providers/{provider_id}", response_model=ModelProviderResponse)
async def update_provider(provider_id: str, body: ModelProviderUpdate):
    """更新模型供应商"""
    kwargs = body.model_dump(exclude_unset=True)
    try:
        result = await model_service.update_provider(provider_id, **kwargs)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
    if result is None:
        raise HTTPException(status_code=404, detail="供应商不存在")
    return result


@router.delete("/providers/{provider_id}", status_code=204)
async def delete_provider(provider_id: str):
    """删除模型供应商（供应商下仍有模型时禁止删除）"""
    success, error = await model_service.delete_provider(provider_id)
    if not success:
        raise HTTPException(status_code=400, detail=error)
    logger.info("模型供应商已删除: id=%s", provider_id)


@router.post("/providers/{provider_id}/fetch-models", response_model=ProviderModelListResponse)
async def fetch_provider_models(provider_id: str):
    """从供应商 OpenAI 兼容 /models 端点拉取可用模型列表（不落库）"""
    try:
        return await model_service.fetch_provider_models(provider_id)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


# ============================================================
# 模型配置
# ============================================================
@router.get("", response_model=list[ModelConfigResponse])
async def list_models(
    model_type: str | None = Query(None, description="按类型筛选"),
    platform: str | None = Query(None, description="按平台筛选"),
):
    """模型配置列表"""
    return await model_service.list_models(model_type=model_type, platform=platform)


@router.post("", response_model=ModelConfigResponse, status_code=201)
async def create_model(body: ModelConfigCreate):
    """创建模型配置（可选挂载到供应商，继承其连接信息）"""
    try:
        return await model_service.create_model(
            name=body.name,
            model_type=body.model_type,
            platform=body.platform or "",
            model=body.model,
            api_base=body.api_base,
            api_key=body.api_key,
            params=body.params,
            is_default=body.is_default,
            is_active=body.is_active,
            provider_id=body.provider_id,
        )
    except Exception as e:
        logger.error("创建模型配置失败: %s", e, exc_info=True)
        raise HTTPException(status_code=400, detail=str(e))


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
