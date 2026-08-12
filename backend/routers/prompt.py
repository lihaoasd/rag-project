"""
提示词模板管理 API。
"""
from fastapi import APIRouter, HTTPException, Query

from models.schemas import (
    PromptTemplateCreate,
    PromptTemplateResponse,
    PromptTemplateUpdate,
    PromptPreviewRequest,
    PromptPreviewResponse,
)
from services import prompt_service
from utils.logging import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/api/prompts", tags=["提示词管理"])


@router.get("", response_model=list[PromptTemplateResponse])
async def list_prompts(scenario: str | None = Query(None, description="按场景筛选")):
    """获取提示词模板列表"""
    items = await prompt_service.list_prompts(scenario=scenario)
    return items


@router.post("", response_model=PromptTemplateResponse, status_code=201)
async def create_prompt(body: PromptTemplateCreate):
    """创建提示词模板"""
    try:
        return await prompt_service.create_prompt(
            name=body.name,
            scenario=body.scenario,
            system_prompt=body.system_prompt,
            description=body.description,
            user_prompt=body.user_prompt,
            variables=[v.model_dump() for v in body.variables] if body.variables else None,
            is_active=body.is_active,
            sort_order=body.sort_order,
        )
    except Exception as e:
        logger.error("创建提示词模板失败: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{prompt_id}", response_model=PromptTemplateResponse)
async def get_prompt(prompt_id: str):
    """获取提示词模板详情"""
    result = await prompt_service.get_prompt(prompt_id)
    if result is None:
        raise HTTPException(status_code=404, detail="模板不存在")
    return result


@router.put("/{prompt_id}", response_model=PromptTemplateResponse)
async def update_prompt(prompt_id: str, body: PromptTemplateUpdate):
    """更新提示词模板"""
    kwargs = body.model_dump(exclude_unset=True)
    if body.variables is not None:
        kwargs["variables"] = [v.model_dump() for v in body.variables]
    result = await prompt_service.update_prompt(prompt_id, **kwargs)
    if result is None:
        raise HTTPException(status_code=404, detail="模板不存在")
    return result


@router.delete("/{prompt_id}", status_code=204)
async def delete_prompt(prompt_id: str):
    """删除提示词模板（默认模板不可删除）"""
    success, error = await prompt_service.delete_prompt(prompt_id)
    if not success:
        raise HTTPException(status_code=400 if error == "默认模板不可删除" else 404, detail=error)
    logger.info("提示词模板已删除: id=%s", prompt_id)


@router.post("/{prompt_id}/activate", response_model=PromptTemplateResponse)
async def activate_prompt(prompt_id: str):
    """激活模板（同一场景下其他模板自动取消激活）"""
    result = await prompt_service.set_active(prompt_id)
    if result is None:
        raise HTTPException(status_code=404, detail="模板不存在")
    return result


@router.post("/preview", response_model=PromptPreviewResponse)
async def preview_prompt(body: PromptPreviewRequest):
    """预览渲染后的提示词"""
    result = await prompt_service.preview(
        template_id=body.template_id,
        system_prompt=body.system_prompt,
        variables=body.variables,
    )
    if result.get("error"):
        raise HTTPException(status_code=400, detail=result["error"])
    return result
