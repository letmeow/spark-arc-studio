from fastapi import APIRouter, Depends, HTTPException, Query, Body, File, UploadFile
from fastapi.responses import StreamingResponse
from starlette.concurrency import run_in_threadpool

from typing import Optional, Dict, Any, List

import json

import asyncio

from pydantic import BaseModel, ConfigDict



from core.auth import get_current_user, require_admin
from core.request_context import set_current_context
from core.search_provider_settings import (
    SearchProviderConfigError,
    get_search_provider_user_view,
    reset_search_provider_user_settings,
    update_search_provider_user_settings,
)

from llm.agen_matchbox import matchbox

from llm.agen_matchbox.utils import parse_extra_body as _parse_extra_body_util



llm_router = APIRouter()


def _run_with_user_context(user: dict, callback, *args, **kwargs):

    """在线程池里恢复当前用户身份。

    设置页有些 Matchbox 调用会通过 run_in_threadpool 执行。不同线程不能假定
    ContextVar 一定沿用主请求线程的值；如果漏掉 is_admin，站长在关闭“全体用户共享”
    后会被当成普通用户，从而误报系统托管 Key 不可用。
    """

    set_current_context(str(user["user_id"]), None, bool(user.get("is_admin")))

    return callback(*args, **kwargs)



TEMP_MIN = 0.3

TEMP_MAX = 1.5



def try_parse_extra_body(raw_str: str) -> dict:

    """尝试解析 extra_body，委托给 utils.parse_extra_body() 统一处理。



    支持：Python 注释、大写 True/False/None、自动补全外层 {}、赋值前缀剥离。

    空字符串返回 {}（路由层兼容旧行为）。

    """

    result = _parse_extra_body_util(raw_str)

    return result if result is not None else {}





def validate_temperature_or_raise(temperature: Optional[float]) -> Optional[float]:

    if temperature is None:

        return None

    try:

        value = float(temperature)

    except (TypeError, ValueError):

        raise HTTPException(status_code=400, detail="Temperature 必须是数字")

    if value < TEMP_MIN or value > TEMP_MAX:

        raise HTTPException(status_code=400, detail=f"Temperature 必须在 {TEMP_MIN} 到 {TEMP_MAX} 之间")

    return value



# ==================== Pydantic Models ====================



class UserSelectionRequest(BaseModel):

    platform_id: int

    model_id: int

    usage_key: Optional[str] = None



class UsageSlotCreateRequest(BaseModel):

    usage_key: str

    usage_label: Optional[str] = None

    platform_id: Optional[int] = None

    model_id: Optional[int] = None



class UsageSlotUpdateRequest(BaseModel):

    usage_key: str

    new_usage_key: Optional[str] = None

    new_usage_label: Optional[str] = None



class UsageSlotDeleteRequest(BaseModel):

    usage_key: str



class PlatformConfigRequest(BaseModel):

    platform_id: int

    api_key: Optional[str] = None



class PlatformCreateRequest(BaseModel):

    name: str

    base_url: str

    api_key: Optional[str] = None

    recharge_url: Optional[str] = None



class PlatformUpdateRequest(BaseModel):

    id: int

    name: str

    base_url: str

    recharge_url: Optional[str] = None



class ModelCreateRequest(BaseModel):

    platform_id: int

    model_name: str

    display_name: str

    extra_body: Optional[str] = None

    image_generation_adapter: Optional[str] = None

    temperature: Optional[float] = None

    max_context_tokens: Optional[int] = None

    max_output_tokens: Optional[int] = None

    input_modalities: Optional[List[str]] = None

    output_modalities: Optional[List[str]] = None



class ModelUpdateRequest(BaseModel):

    id: int

    display_name: Optional[str] = None

    extra_body: Optional[str] = None

    image_generation_adapter: Optional[str] = None

    temperature: Optional[float] = None

    max_context_tokens: Optional[int] = None

    max_output_tokens: Optional[int] = None

    input_modalities: Optional[List[str]] = None

    output_modalities: Optional[List[str]] = None



class ModelDeleteRequest(BaseModel):

    id: int



class EmbeddingCreateRequest(BaseModel):

    platform_id: int

    model_name: str

    display_name: str

    extra_body: Optional[str] = None



class EmbeddingUpdateRequest(BaseModel):

    id: int

    display_name: Optional[str] = None

    extra_body: Optional[str] = None



class EmbeddingSelectRequest(BaseModel):

    platform_id: int

    model_id: int



class TestEmbeddingRequest(BaseModel):

    model_name: str



class AgentBindingSaveRequest(BaseModel):

    agent_name: str

    target_type: str  # 'usage' or 'direct'

    usage_key: Optional[str] = None

    platform_id: Optional[int] = None

    model_id: Optional[int] = None



# ==================== Routes ====================



@llm_router.get('/api/ai/user-platforms-models')

async def get_user_platforms_and_models(user: dict = Depends(get_current_user)):

    """获取用户所有可用平台及对应的模型列表（打平结构）。"""

    try:

        user_id = str(user['user_id'])

        data = matchbox().get_platform_models(user_id)

        return data

    except Exception as e:

        print(f"Failed to get user platform models: {e}")

        raise HTTPException(status_code=500, detail=str(e))



@llm_router.get('/api/ai/platforms')

async def get_platforms(user: dict = Depends(get_current_user)):

    """获取用户所有可用平台列表（用于平台管理界面）。"""

    try:

        user_id = str(user['user_id'])

        data = matchbox().get_platforms(user_id)

        return data

    except Exception as e:

        print(f"Failed to get platforms: {e}")

        raise HTTPException(status_code=500, detail=str(e))



@llm_router.get('/api/ai/platforms-with-models')

async def get_platforms_with_models(

    only_custom: bool = Query(False, description="是否只返回自定义平台"),

    user: dict = Depends(get_current_user)

):

    """获取平台列表，包含嵌套的模型数组（用于模型管理界面）。"""

    try:

        user_id = str(user['user_id'])

        data = matchbox().get_platforms_with_models(user_id, only_custom=only_custom)

        return data

    except Exception as e:

        print(f"Failed to get platforms and models: {e}")

        raise HTTPException(status_code=500, detail=str(e))





@llm_router.get('/api/ai/platforms-with-embeddings')

async def get_platforms_with_embeddings(

    only_custom: bool = Query(False, description="是否只返回自定义平台"),

    user: dict = Depends(get_current_user)

):

    """获取平台列表，包含嵌套的 Embedding 模型数组"""

    try:

        user_id = str(user['user_id'])

        data = matchbox().get_platforms_with_embeddings(user_id, only_custom=only_custom)

        return data

    except Exception as e:

        print(f"Failed to get platforms and embeddings: {e}")

        raise HTTPException(status_code=500, detail=str(e))





@llm_router.get('/api/ai/embedding-status')

async def get_embedding_status(user: dict = Depends(get_current_user)):

    """获取 Embedding 可用性状态（前端静默检测）"""

    try:

        user_id = str(user['user_id'])

        manager = matchbox()
        platforms = manager.get_platforms_with_embeddings(user_id)

        selection_detail = manager.get_user_embedding_detail(user_id)
        selection = selection_detail.get("current")



        recommended = None

        for p in platforms:

            embeddings = p.get("embeddings") or []

            if embeddings:

                first = embeddings[0]

                recommended = {

                    "platform_id": p.get("platform_id"),

                    "model_id": first.get("model_id"),

                    "display_name": first.get("display_name"),

                }

                break



        return {

            "has_embeddings": any((p.get("embeddings") or []) for p in platforms),

            "has_selection": bool(selection_detail.get("has_selection", False)),

            "current": selection,

            "source": selection_detail.get("source", "default"),

            "recommended": recommended,

        }

    except Exception as e:

        print(f"Failed to get embedding status: {e}")

        raise HTTPException(status_code=500, detail=str(e))





@llm_router.get('/api/ai/user-embedding')

async def get_user_embedding_selection(user: dict = Depends(get_current_user)):

    """获取用户 Embedding 选择配置"""

    try:

        user_id = str(user['user_id'])

        selection = matchbox().get_user_embedding_detail(user_id)

        return selection

    except Exception as e:

        print(f"Failed to get user embedding selection: {e}")

        raise HTTPException(status_code=500, detail=str(e))





@llm_router.post('/api/ai/user-embedding')

async def save_user_embedding_selection(

    data: EmbeddingSelectRequest,

    user: dict = Depends(get_current_user)

):

    """保存用户 Embedding 选择配置"""

    user_id = str(user['user_id'])

    try:

        detail = matchbox().save_user_embedding_selection(user_id, data.platform_id, data.model_id)

        return detail

    except Exception as e:

        print(f"Failed to save user embedding selection: {e}")

        raise HTTPException(status_code=400, detail=str(e))





@llm_router.post('/api/ai/platform/{platform_id}/list-models')

async def list_remote_models(

    platform_id: int,

    user: dict = Depends(get_current_user)

):

    """代理调用远程平台获取可用模型列表"""

    try:

        user_id = str(user['user_id'])

        models = await run_in_threadpool(
            _run_with_user_context,
            user,
            matchbox().proxy_list_models,
            user_id,
            platform_id,
        )

        return {"models": models}

    except Exception as e:

        print(f"Failed to list remote models: {e}")

        # 如果是已知错误（如无权、认证失败），返回 400 会更合适，以便前端显示详细信息

        raise HTTPException(status_code=400, detail=str(e))



class TestModelRequest(BaseModel):

    model_name: str

    extra_body: Optional[str] = None



@llm_router.post('/api/ai/platform/{platform_id}/test-model')

async def test_remote_model(

    platform_id: int,

    data: TestModelRequest,

    user: dict = Depends(get_current_user)

):

    """测试模型连接"""

    try:

        user_id = str(user['user_id'])

        

        # 处理可能的临时 extra_body (用于添加模型时的测试)

        extra_body_dict = None

        if data.extra_body:

            try:

                extra_body_dict = try_parse_extra_body(data.extra_body)

            except:

                pass

        

        # 如果没有传入临时 extra_body，proxy_test_chat 会尝试从数据库读取

        response = await run_in_threadpool(

            matchbox().proxy_test_chat,

            user_id,

            platform_id,

            data.model_name,

            extra_body_override=extra_body_dict

        )

        return {"response": response}

    except Exception as e:

        print(f"Failed to test remote model: {e}")

        raise HTTPException(status_code=400, detail=str(e))





@llm_router.post('/api/ai/platform/{platform_id}/test-embedding')

async def test_remote_embedding(

    platform_id: int,

    data: TestEmbeddingRequest,

    user: dict = Depends(get_current_user)

):

    """测试 Embedding 连接"""

    try:

        user_id = str(user['user_id'])

        response = await run_in_threadpool(

            matchbox().proxy_test_embedding,

            user_id,

            platform_id,

            data.model_name

        )

        return {"response": response}

    except Exception as e:

        print(f"Failed to test remote embedding: {e}")

        raise HTTPException(status_code=400, detail=str(e))



@llm_router.post('/api/ai/platform/{platform_id}/speed-test')

async def speed_test_remote_model(

    platform_id: int,

    data: TestModelRequest,

    user: dict = Depends(get_current_user)

):

    """测速模型连接 (SSE)"""

    user_id = str(user['user_id'])

    

    def generate():

        try:

            generator = matchbox().proxy_speed_test(user_id, platform_id, data.model_name)

            for item in generator:

                yield f"data: {json.dumps(item, ensure_ascii=False)}\n\n"

        except Exception as e:

            yield f"data: {json.dumps({'error': str(e)}, ensure_ascii=False)}\n\n"



    return StreamingResponse(generate(), media_type="text/event-stream")



@llm_router.get('/api/ai/user-selection')

async def get_user_selection(

    usage_key: Optional[str] = Query(None),

    user: dict = Depends(get_current_user)

):

    """获取用户的AI模型选择。"""

    try:

        user_id = str(user['user_id'])

        selection = await run_in_threadpool(
            _run_with_user_context,
            user,
            matchbox().get_user_selection_detail,
            user_id,
            usage_key=usage_key,
        )

        return selection

    except ValueError as e:

        # 捕获 ValueError (如配置错误) 并返回 400，而不是 500

        raise HTTPException(status_code=400, detail=str(e))

    except Exception as e:

        print(f"Failed to get user selection: {e}")

        raise HTTPException(status_code=500, detail=str(e))



@llm_router.post('/api/ai/user-selection')

async def update_user_selection(

    data: UserSelectionRequest,

    user: dict = Depends(get_current_user)

):

    """更新用户的AI模型选择。"""

    user_id = str(user['user_id'])

    try:

        success = matchbox().save_user_selection(

            user_id,

            data.platform_id,

            data.model_id,

            usage_key=data.usage_key,

        )

        if success:

            return {"success": True}

        else:

            raise HTTPException(status_code=400, detail="保存失败")

    except Exception as e:

        print(f"Failed to save user selection: {e}")

        raise HTTPException(status_code=400, detail=str(e))



# Usage Slots Management

@llm_router.post('/api/ai/user-selection/usage')

async def create_usage_slot(

    data: UsageSlotCreateRequest,

    user: dict = Depends(get_current_user)

):

    """创建用户用途槽"""

    user_id = str(user['user_id'])

    try:

        detail = matchbox().create_user_usage_slot(

            user_id,

            data.usage_key,

            usage_label=data.usage_label,

            platform_id=data.platform_id,

            model_id=data.model_id,

        )

        return detail

    except Exception as e:

        print(f"Failed to create usage slot: {e}")

        raise HTTPException(status_code=400, detail=str(e))



@llm_router.put('/api/ai/user-selection/usage')

async def update_usage_slot(

    data: UsageSlotUpdateRequest,

    user: dict = Depends(get_current_user)

):

    """编辑用途（重命名 key 或更改 label）"""

    user_id = str(user['user_id'])

    try:

        detail = matchbox().rename_user_usage_slot(

            user_id, 

            data.usage_key, 

            new_usage_key=data.new_usage_key, 

            new_label=data.new_usage_label

        )

        return detail

    except Exception as e:

        print(f"Failed to update usage slot: {e}")

        raise HTTPException(status_code=400, detail=str(e))



@llm_router.delete('/api/ai/user-selection/usage')

async def delete_usage_slot(

    data: UsageSlotDeleteRequest,

    user: dict = Depends(get_current_user)

):

    """删除用途"""

    user_id = str(user['user_id'])

    try:

        matchbox().delete_user_usage_slot(user_id, data.usage_key)

        return {"success": True}

    except Exception as e:

        print(f"Failed to delete usage slot: {e}")

        raise HTTPException(status_code=400, detail=str(e))



@llm_router.post('/api/ai/platform-config')

async def update_platform_config(

    data: PlatformConfigRequest,

    user: dict = Depends(get_current_user)

):

    """更新用户平台的配置，如 API Key。"""

    user_id = str(user['user_id'])

    

    try:

        success = matchbox().update_platform_config(user_id, data.platform_id, data.api_key)

        if success:

            return {"success": True}

        else:

            return {"success": True, "message": "No changes applied"}

    except Exception as e:

        print(f"Failed to update platform config: {e}")

        raise HTTPException(status_code=400, detail=str(e))



# Platform Management

@llm_router.post('/api/ai/platform')

async def create_platform(

    data: PlatformCreateRequest,

    user: dict = Depends(get_current_user)

):

    """添加平台"""

    user_id = str(user['user_id'])

    

    try:

        plat = matchbox().add_platform(
            data.name,
            data.base_url,
            data.api_key,
            user_id,
            recharge_url=data.recharge_url,
        )

        # 同时返回旧版 id 与明确的 platform_id，避免前端把 Base URL 当作身份。
        return {
            "success": True,
            "id": plat.id,
            "platform_id": plat.id,
            "platform_key": plat.platform_key,
        }

    except Exception as e:

        print(f"Failed to create platform: {e}")

        raise HTTPException(status_code=400, detail=str(e))



@llm_router.put('/api/ai/platform')

async def update_platform(

    data: PlatformUpdateRequest,

    user: dict = Depends(get_current_user)

):

    """更新平台信息（重命名 + 修改 URL）"""

    user_id = str(user['user_id'])

    try:

        fields_set = getattr(data, "model_fields_set", set())

        matchbox().update_platform_details(
            user_id,
            data.id,
            data.name,
            data.base_url,
            recharge_url=data.recharge_url,
            update_recharge_url="recharge_url" in fields_set,
        )

        return {"success": True}

    except Exception as e:

        print(f"Failed to update platform: {e}")

        raise HTTPException(status_code=400, detail=str(e))



@llm_router.delete('/api/ai/platform')

async def delete_platform(

    id: int = Query(..., description="Platform ID"),

    user: dict = Depends(get_current_user)

):

    """软禁用平台"""

    user_id = str(user['user_id'])

    try:

        matchbox().disable_platform(id, user_id=user_id)

        return {"success": True}

    except Exception as e:

        print(f"Failed to disable platform: {e}")

        raise HTTPException(status_code=400, detail=str(e))



# Model Management

@llm_router.post('/api/ai/model')

async def create_model(

    data: ModelCreateRequest,

    user: dict = Depends(get_current_user)

):

    """添加模型"""

    user_id = str(user['user_id'])

    

    extra_body_dict = None

    if data.extra_body:

        try:

            extra_body_dict = try_parse_extra_body(data.extra_body)

        except json.JSONDecodeError as e:

            raise HTTPException(status_code=400, detail=f"Extrabody JSON解析失败: {str(e)}。请确保参数为合法的JSON格式。")



    try:

        temperature = validate_temperature_or_raise(data.temperature)

        model = matchbox().add_model(

            data.platform_id, 

            data.model_name, 

            data.display_name,

            user_id=user_id, 

            extra_body=extra_body_dict,
            image_generation_adapter=data.image_generation_adapter,

            temperature=temperature,

            max_context_tokens=data.max_context_tokens,

            max_output_tokens=data.max_output_tokens,

            input_modalities=data.input_modalities,

            output_modalities=data.output_modalities,

        )

        return {"success": True, "id": model.id}

    except Exception as e:

        print(f"Failed to create model: {e}")

        raise HTTPException(status_code=400, detail=str(e))





@llm_router.post('/api/ai/embedding')

async def create_embedding(

    data: EmbeddingCreateRequest,

    user: dict = Depends(get_current_user)

):

    """添加 Embedding 模型"""

    user_id = str(user['user_id'])



    extra_body_dict = None

    if data.extra_body:

        try:

            extra_body_dict = try_parse_extra_body(data.extra_body)

        except json.JSONDecodeError as e:

            raise HTTPException(status_code=400, detail=f"Extrabody JSON解析失败: {str(e)}。请确保参数为合法的JSON格式。")



    try:

        model = matchbox().add_embedding(

            data.platform_id,

            data.model_name,

            data.display_name,

            user_id=user_id,

            extra_body=extra_body_dict

        )

        return {"success": True, "id": model.id}

    except Exception as e:

        print(f"Failed to create embedding: {e}")

        raise HTTPException(status_code=400, detail=str(e))



@llm_router.put('/api/ai/model')

async def update_model(

    data: ModelUpdateRequest,

    user: dict = Depends(get_current_user)

):

    """更新模型"""

    user_id = str(user['user_id'])

    

    # 检查字段是否在请求中显式设置

    fields_set = getattr(data, "model_fields_set", set())

    

    display_name = data.display_name if 'display_name' in fields_set else None

    update_temperature = 'temperature' in fields_set

    new_temperature = validate_temperature_or_raise(data.temperature) if update_temperature else None

    

    extra_body_dict = None

    # 只有当 extra_body 显式包含在请求中时，才进行处理

    if 'extra_body' in fields_set:

        if data.extra_body:

            try:

                extra_body_dict = try_parse_extra_body(data.extra_body)

            except json.JSONDecodeError as e:

                raise HTTPException(status_code=400, detail=f"Extrabody JSON解析失败: {str(e)}。请确保参数为合法的JSON格式。")

        else:

            # 显式传递了 null 或空字符串，意为清空 -> 传递空字典给 admin 以触发清空逻辑

            extra_body_dict = {} 

    else:

        # 未包含在请求中，传递 None 给 admin，admin 会跳过更新

        extra_body_dict = None



    update_max_context = 'max_context_tokens' in fields_set

    update_max_output = 'max_output_tokens' in fields_set

    update_modalities = 'input_modalities' in fields_set or 'output_modalities' in fields_set

    update_image_generation_adapter = 'image_generation_adapter' in fields_set



    try:

        matchbox().update_model(

            data.id,

            new_display_name=display_name,

            new_extra_body=extra_body_dict,

            new_temperature=new_temperature,

            update_temperature=update_temperature,

            user_id=user_id,

            max_context_tokens=data.max_context_tokens if update_max_context else None,

            max_output_tokens=data.max_output_tokens if update_max_output else None,

            update_max_context_tokens=update_max_context,

            update_max_output_tokens=update_max_output,

            input_modalities=data.input_modalities if update_modalities else None,

            output_modalities=data.output_modalities if update_modalities else None,

            update_modalities=update_modalities,

            image_generation_adapter=data.image_generation_adapter if update_image_generation_adapter else None,

            update_image_generation_adapter=update_image_generation_adapter,

        )

        return {"success": True}

    except Exception as e:

        print(f"Failed to update model: {e}")

        raise HTTPException(status_code=400, detail=str(e))





@llm_router.put('/api/ai/embedding')

async def update_embedding(

    data: EmbeddingUpdateRequest,

    user: dict = Depends(get_current_user)

):

    """更新 Embedding 模型"""

    user_id = str(user['user_id'])



    fields_set = getattr(data, "model_fields_set", set())



    display_name = data.display_name if 'display_name' in fields_set else None



    extra_body_dict = None

    if 'extra_body' in fields_set:

        if data.extra_body:

            try:

                extra_body_dict = try_parse_extra_body(data.extra_body)

            except json.JSONDecodeError as e:

                raise HTTPException(status_code=400, detail=f"Extrabody JSON解析失败: {str(e)}。请确保参数为合法的JSON格式。")

        else:

            extra_body_dict = {}

    else:

        extra_body_dict = None



    try:

        matchbox().update_embedding(data.id, new_display_name=display_name, new_extra_body=extra_body_dict, user_id=user_id)

        return {"success": True}

    except Exception as e:

        print(f"Failed to update embedding: {e}")

        raise HTTPException(status_code=400, detail=str(e))



@llm_router.post('/api/ai/model/delete')

async def delete_model_post(

    data: ModelDeleteRequest,

    user: dict = Depends(get_current_user)

):

    """删除模型（POST 兼容入口）"""

    user_id = str(user['user_id'])

    try:

        matchbox().disable_model(data.id, user_id=user_id)

        return {"success": True}

    except Exception as e:

        print(f"Failed to delete model: {e}")

        raise HTTPException(status_code=400, detail=str(e))





@llm_router.post('/api/ai/embedding/delete')

async def delete_embedding_post(

    data: ModelDeleteRequest,

    user: dict = Depends(get_current_user)

):

    """删除 Embedding 模型（POST 兼容入口）"""

    user_id = str(user['user_id'])

    try:

        matchbox().disable_model(data.id, user_id=user_id)

        return {"success": True}

    except Exception as e:

        print(f"Failed to delete embedding: {e}")

        raise HTTPException(status_code=400, detail=str(e))



# Agent Bindings Management

@llm_router.get('/api/ai/agent-bindings')

async def get_agent_bindings(user: dict = Depends(get_current_user)):

    """获取用户的 Agent 绑定配置"""

    user_id = str(user['user_id'])

    try:

        return matchbox().get_agent_bindings(user_id)

    except Exception as e:

        raise HTTPException(status_code=500, detail=str(e))



@llm_router.post('/api/ai/agent-bindings')

async def save_agent_binding(

    data: AgentBindingSaveRequest,

    user: dict = Depends(get_current_user)

):

    """保存 Agent 绑定配置"""

    user_id = str(user['user_id'])

    try:

        success = matchbox().save_agent_binding(

            user_id,

            data.agent_name,

            data.target_type,

            usage_key=data.usage_key,

            platform_id=data.platform_id,

            model_id=data.model_id

        )

        return {"success": success}

    except Exception as e:

        print(f"Failed to save agent binding: {e}")

        raise HTTPException(status_code=400, detail=str(e))



# ==================== Usage Statistics ====================



@llm_router.get('/api/ai/usage-stats')

async def get_usage_stats(user: dict = Depends(get_current_user)):

    """获取用户的模型使用统计"""

    user_id = str(user['user_id'])

    try:

        stats = matchbox().get_user_usage_stats(user_id)

        return {"stats": stats}

    except Exception as e:

        raise HTTPException(status_code=500, detail=str(e))



@llm_router.delete('/api/ai/usage-stats')

async def reset_usage_stats(

    model_id: Optional[int] = Query(None, description="Model ID to reset, or all if not provided"),

    user: dict = Depends(get_current_user)

):

    """重置用户的模型使用统计"""

    user_id = str(user['user_id'])

    try:

        success = matchbox().reset_user_usage_stats(user_id, model_id)

        return {"success": success}

    except Exception as e:

        print(f"Failed to reset usage stats: {e}")

        raise HTTPException(status_code=400, detail=str(e))





@llm_router.get('/api/ai/system-config')

async def get_system_config(user: dict = Depends(get_current_user)):

    """获取系统级配置 (LLM_AUTO_KEY, USE_SYS_LLM_CONFIG)"""

    try:

        config = matchbox().get_system_config()

        return config

    except Exception as e:

        print(f"Failed to get system config: {e}")

        raise HTTPException(status_code=500, detail=str(e))





class SystemConfigUpdateRequest(BaseModel):

    use_sys_llm_config: Optional[bool] = None

    billing_enabled: Optional[bool] = None


class SearchProviderUserUpdateRequest(BaseModel):

    provider: str

    url: str

    api_key: Optional[str] = None



@llm_router.post('/api/ai/system-config')

async def update_system_config(

    data: SystemConfigUpdateRequest,

    user: dict = Depends(require_admin)

):

    """更新系统级配置"""

    try:

        matchbox().set_system_config(

            use_sys_llm_config=data.use_sys_llm_config,

            billing_enabled=data.billing_enabled,

        )

        return {"success": True}

    except Exception as e:

        print(f"Failed to update system config: {e}")

        raise HTTPException(status_code=500, detail=str(e))


@llm_router.get('/api/ai/search-providers')

async def get_user_search_providers(user: dict = Depends(get_current_user)):

    """读取当前用户的搜索覆盖与系统回退状态。"""

    try:

        return get_search_provider_user_view(str(user['user_id']))

    except Exception as e:

        raise HTTPException(status_code=500, detail=str(e))


@llm_router.post('/api/ai/search-providers')

async def update_user_search_provider(

    data: SearchProviderUserUpdateRequest,

    user: dict = Depends(get_current_user),

):

    """保存当前用户对一个搜索提供商的覆盖。"""

    try:

        return update_search_provider_user_settings(

            str(user['user_id']),

            data.provider,

            url=data.url,

            api_key=data.api_key,

        )

    except SearchProviderConfigError as e:

        raise HTTPException(status_code=400, detail=str(e))

    except Exception as e:

        raise HTTPException(status_code=500, detail=str(e))


@llm_router.delete('/api/ai/search-providers/{provider}')

async def reset_user_search_provider(

    provider: str,

    user: dict = Depends(get_current_user),

):

    """删除当前用户覆盖，恢复系统回退或不可用状态。"""

    try:

        return reset_search_provider_user_settings(str(user['user_id']), provider)

    except SearchProviderConfigError as e:

        raise HTTPException(status_code=400, detail=str(e))

    except Exception as e:

        raise HTTPException(status_code=500, detail=str(e))





# ==================== Admin: System Model Management ====================



from core.auth import require_admin



class AdminSysModelRequest(BaseModel):

    platform_id: int

    model_name: str

    display_name: str

    extra_body: Optional[str] = None

    image_generation_adapter: Optional[str] = None

    temperature: Optional[float] = None
    sys_credit_input_price_per_million: Optional[float] = None
    sys_credit_cached_input_price_per_million: Optional[float] = None
    sys_credit_output_price_per_million: Optional[float] = None
    max_context_tokens: Optional[int] = None

    max_output_tokens: Optional[int] = None

    input_modalities: Optional[List[str]] = None

    output_modalities: Optional[List[str]] = None



class AdminSysModelUpdateRequest(BaseModel):

    model_config = ConfigDict(extra='ignore')

    id: int

    display_name: Optional[str] = None

    extra_body: Optional[str] = None

    image_generation_adapter: Optional[str] = None

    temperature: Optional[float] = None
    sys_credit_input_price_per_million: Optional[float] = None
    sys_credit_cached_input_price_per_million: Optional[float] = None
    sys_credit_output_price_per_million: Optional[float] = None
    max_context_tokens: Optional[int] = None

    max_output_tokens: Optional[int] = None

    input_modalities: Optional[List[str]] = None

    output_modalities: Optional[List[str]] = None





@llm_router.post('/api/ai/admin/sys-model')

async def admin_create_sys_model(

    data: AdminSysModelRequest,

    admin_user: dict = Depends(require_admin)

):

    """管理员：添加系统模型"""

    extra_body_dict = None

    if data.extra_body:

        try:

            extra_body_dict = try_parse_extra_body(data.extra_body)

        except json.JSONDecodeError as e:

            raise HTTPException(status_code=400, detail=f"Extrabody JSON解析失败: {str(e)}。请确保参数为合法的JSON格式。")

    

    try:

        temperature = validate_temperature_or_raise(data.temperature)

        model = matchbox().add_model(

            data.platform_id,

            data.model_name,

            data.display_name,

            extra_body=extra_body_dict,
            image_generation_adapter=data.image_generation_adapter,

            temperature=temperature,
            sys_credit_input_price_per_million=data.sys_credit_input_price_per_million,
            sys_credit_cached_input_price_per_million=data.sys_credit_cached_input_price_per_million,
            sys_credit_output_price_per_million=data.sys_credit_output_price_per_million,
            admin_mode=True,

            max_context_tokens=data.max_context_tokens,

            max_output_tokens=data.max_output_tokens,

            input_modalities=data.input_modalities,

            output_modalities=data.output_modalities,

        )

        return {"success": True, "id": model.id}

    except Exception as e:

        print(f"Admin failed to create system model: {e}")

        raise HTTPException(status_code=400, detail=str(e))





@llm_router.put('/api/ai/admin/sys-model')

async def admin_update_sys_model(

    data: AdminSysModelUpdateRequest,

    admin_user: dict = Depends(require_admin)

):

    """管理员：更新系统模型"""

    fields_set = getattr(data, "model_fields_set", set())

    

    display_name = data.display_name if 'display_name' in fields_set else None

    update_temperature = 'temperature' in fields_set

    new_temperature = validate_temperature_or_raise(data.temperature) if update_temperature else None

    update_credit_price = (
        'sys_credit_input_price_per_million' in fields_set
        or 'sys_credit_cached_input_price_per_million' in fields_set
        or 'sys_credit_output_price_per_million' in fields_set
    )
    

    extra_body_dict = None

    if 'extra_body' in fields_set:

        if data.extra_body:

            try:

                extra_body_dict = try_parse_extra_body(data.extra_body)

            except json.JSONDecodeError as e:

                raise HTTPException(status_code=400, detail=f"Extrabody JSON解析失败: {str(e)}。请确保参数为合法的JSON格式。")

        else:

            extra_body_dict = {}

    else:

        extra_body_dict = None

    

    update_max_context = 'max_context_tokens' in fields_set

    update_max_output = 'max_output_tokens' in fields_set

    update_modalities = 'input_modalities' in fields_set or 'output_modalities' in fields_set

    update_image_generation_adapter = 'image_generation_adapter' in fields_set



    try:

        matchbox().update_model(

            data.id,

            new_display_name=display_name,

            new_extra_body=extra_body_dict,

            new_temperature=new_temperature,
            sys_credit_input_price_per_million=data.sys_credit_input_price_per_million,
            sys_credit_cached_input_price_per_million=data.sys_credit_cached_input_price_per_million,
            sys_credit_output_price_per_million=data.sys_credit_output_price_per_million,
            update_credit_price=update_credit_price,

            update_temperature=update_temperature,

            admin_mode=True,

            max_context_tokens=data.max_context_tokens if update_max_context else None,

            max_output_tokens=data.max_output_tokens if update_max_output else None,

            update_max_context_tokens=update_max_context,

            update_max_output_tokens=update_max_output,

            input_modalities=data.input_modalities if update_modalities else None,

            output_modalities=data.output_modalities if update_modalities else None,

            update_modalities=update_modalities,

            image_generation_adapter=data.image_generation_adapter if update_image_generation_adapter else None,

            update_image_generation_adapter=update_image_generation_adapter,

        )

        return {"success": True}

    except Exception as e:

        print(f"Admin failed to update system model: {e}")

        raise HTTPException(status_code=400, detail=str(e))





@llm_router.post('/api/ai/admin/sys-model/delete')

async def admin_delete_sys_model_post(

    data: ModelDeleteRequest,

    admin_user: dict = Depends(require_admin)

):

    """管理员：删除系统模型（POST 兼容入口）"""

    try:

        matchbox().disable_model(data.id, admin_mode=True)

        return {"success": True}

    except Exception as e:

        print(f"Admin failed to delete system model: {e}")

        raise HTTPException(status_code=400, detail=str(e))





@llm_router.post('/api/ai/admin/sys-embedding')

async def admin_create_sys_embedding(

    data: AdminSysModelRequest,

    admin_user: dict = Depends(require_admin)

):

    """管理员：添加系统 Embedding"""

    extra_body_dict = None

    if data.extra_body:

        try:

            extra_body_dict = try_parse_extra_body(data.extra_body)

        except json.JSONDecodeError as e:

            raise HTTPException(status_code=400, detail=f"Extrabody JSON解析失败: {str(e)}。请确保参数为合法的JSON格式。")

    

    try:

        temperature = validate_temperature_or_raise(data.temperature)

        model = matchbox().add_embedding(

            data.platform_id,

            data.model_name,

            data.display_name,

            extra_body=extra_body_dict,

            temperature=temperature,

            admin_mode=True,

        )

        return {"success": True, "id": model.id}

    except Exception as e:

        print(f"Admin failed to create system embedding: {e}")

        raise HTTPException(status_code=400, detail=str(e))





@llm_router.put('/api/ai/admin/sys-embedding')

async def admin_update_sys_embedding(

    data: AdminSysModelUpdateRequest,

    admin_user: dict = Depends(require_admin)

):

    """管理员：更新系统 Embedding"""

    fields_set = getattr(data, "model_fields_set", set())

    

    display_name = data.display_name if 'display_name' in fields_set else None

    update_temperature = 'temperature' in fields_set

    new_temperature = validate_temperature_or_raise(data.temperature) if update_temperature else None

    

    extra_body_dict = None

    if 'extra_body' in fields_set:

        if data.extra_body:

            try:

                extra_body_dict = try_parse_extra_body(data.extra_body)

            except json.JSONDecodeError as e:

                raise HTTPException(status_code=400, detail=f"Extrabody JSON解析失败: {str(e)}。请确保参数为合法的JSON格式。")

        else:

            extra_body_dict = {}

    else:

        extra_body_dict = None

    

    try:

        matchbox().update_embedding(

            data.id,

            new_display_name=display_name,

            new_extra_body=extra_body_dict,

            new_temperature=new_temperature,

            update_temperature=update_temperature,

            admin_mode=True,

        )

        return {"success": True}

    except Exception as e:

        print(f"Admin failed to update system embedding: {e}")

        raise HTTPException(status_code=400, detail=str(e))





@llm_router.post('/api/ai/admin/sys-embedding/delete')

async def admin_delete_sys_embedding_post(

    data: ModelDeleteRequest,

    admin_user: dict = Depends(require_admin)

):

    """管理员：删除系统 Embedding（POST 兼容入口）"""

    try:

        matchbox().disable_model(data.id, admin_mode=True)

        return {"success": True}

    except Exception as e:

        print(f"Admin failed to delete system embedding: {e}")

        raise HTTPException(status_code=400, detail=str(e))





# ==================== Admin: System Platform Management ====================

#

# ⚠️ 数据源说明：

# - 这些 API 直接操作数据库，修改即时生效，无需重启服务

# - YAML 文件 (matchbox_cfg.yaml) 仅作为初始化模板 or 备份/分享工具

# - 使用 /api/ai/admin/reload-from-yaml 可从 YAML 强制重置配置（用于导入）

# - 使用 /api/ai/admin/export-to-yaml 可将当前配置回写至 YAML（用于导出分享）

#



class AdminSysPlatformCreateRequest(BaseModel):

    name: str

    base_url: str

    api_key: Optional[str] = None

    sys_credit_balance: Optional[float] = None

    recharge_url: Optional[str] = None



class AdminSysPlatformUpdateRequest(BaseModel):

    platform_id: int

    name: Optional[str] = None

    base_url: Optional[str] = None

    sys_credit_balance: Optional[float] = None

    recharge_url: Optional[str] = None



class AdminSysPlatformApiKeyRequest(BaseModel):

    platform_id: int

    api_key: Optional[str] = None





@llm_router.get('/api/ai/admin/sys-platforms')

async def admin_get_sys_platforms(

    admin_user: dict = Depends(require_admin)

):

    """

    管理员：获取所有系统平台列表

    返回未禁用的系统平台，按 sort_order 排序

    """

    try:

        platforms = matchbox().admin_get_sys_platforms()

        return {"success": True, "platforms": platforms}

    except Exception as e:

        print(f"Failed to get system platforms: {e}")

        raise HTTPException(status_code=500, detail=str(e))





@llm_router.post('/api/ai/admin/sys-platform')

async def admin_create_sys_platform(

    data: AdminSysPlatformCreateRequest,

    admin_user: dict = Depends(require_admin)

):

    """

    管理员：添加系统平台

    直接写入数据库，即时生效，无需重启服务

    """

    try:

        plat = matchbox().admin_add_sys_platform(

            data.name,

            data.base_url,

            data.api_key,

            sys_credit_balance=data.sys_credit_balance,

            recharge_url=data.recharge_url,

        )

        return {
            "success": True,
            "platform_id": plat.id,
            "platform_key": plat.platform_key,
        }

    except Exception as e:

        print(f"Admin failed to create system platform: {e}")

        raise HTTPException(status_code=400, detail=str(e))





@llm_router.put('/api/ai/admin/sys-platform')

async def admin_update_sys_platform(

    data: AdminSysPlatformUpdateRequest,

    admin_user: dict = Depends(require_admin)

):

    """

    管理员：更新系统平台信息（名称、URL）

    """

    try:

        fields_set = getattr(data, "model_fields_set", set())

        matchbox().admin_update_sys_platform(

            data.platform_id,

            new_name=data.name,

            new_base_url=data.base_url,

            sys_credit_balance=data.sys_credit_balance if "sys_credit_balance" in fields_set else None,

            update_sys_credit_balance="sys_credit_balance" in fields_set,

            recharge_url=data.recharge_url if "recharge_url" in fields_set else None,

            update_recharge_url="recharge_url" in fields_set,

        )

        return {"success": True}

    except Exception as e:

        print(f"Admin failed to update system platform: {e}")

        raise HTTPException(status_code=400, detail=str(e))





@llm_router.post('/api/ai/admin/sys-platform/api-key')

async def admin_update_sys_platform_api_key(

    data: AdminSysPlatformApiKeyRequest,

    admin_user: dict = Depends(require_admin)

):

    """

    管理员：更新系统平台的默认 API Key

    此 Key 作为系统默认 Key，当用户未设置自己的 Key 且 LLM_AUTO_KEY=True 时使用

    """

    try:

        matchbox().admin_update_sys_platform_api_key(

            data.platform_id,

            data.api_key

        )

        return {"success": True}

    except Exception as e:

        print(f"Admin failed to update system platform API key: {e}")

        raise HTTPException(status_code=400, detail=str(e))





@llm_router.delete('/api/ai/admin/sys-platform')

async def admin_delete_sys_platform(

    id: int = Query(..., description="System Platform ID"),

    admin_user: dict = Depends(require_admin)

):

    """

    管理员：软禁用系统平台

    平台及其模型不会被硬删除，仅标记为 disable=1

    """

    try:

        matchbox().disable_platform(id, admin_mode=True)

        return {"success": True}

    except Exception as e:

        print(f"Admin failed to delete system platform: {e}")

        raise HTTPException(status_code=400, detail=str(e))





@llm_router.post('/api/ai/admin/import-from-yaml')

async def admin_import_from_yaml(

    file: UploadFile = File(..., description="平台配置文件（YAML 格式）"),

    admin_user: dict = Depends(require_admin)

):

    """

    管理员：上传配置文件并增量同步系统平台配置。



    支持两种格式：

    - .yaml / .yml：仅导入结构，密钥从服务器本地 matchbox_key.yaml 获取

    - .matchbox：ZIP 压缩包（含 matchbox_cfg.yaml + matchbox_key.yaml），同时导入结构与密钥



    说明：此操作只增量添加/更新上传文件中的系统平台与模型，不删除已有平台，不覆盖已有密钥。

    """

    import yaml

    import io

    import zipfile

    try:

        filename = file.filename or ""

        is_matchbox = filename.endswith('.matchbox')
        is_yaml = filename.endswith('.yaml') or filename.endswith('.yml')

        if not (is_matchbox or is_yaml):
            raise HTTPException(status_code=400, detail="请上传 .yaml / .yml 或 .matchbox 文件")



        content = await file.read()

        uploaded_key_data = None

        configs = None

        if is_matchbox:

            with zipfile.ZipFile(io.BytesIO(content), "r") as zf:

                names = zf.namelist()

                cfg_name = next((n for n in names if n.endswith('matchbox_cfg.yaml')), None)

                key_name = next((n for n in names if n.endswith('matchbox_key.yaml')), None)

                if cfg_name:

                    configs = yaml.safe_load(zf.read(cfg_name).decode('utf-8')) or {}

                if key_name:

                    uploaded_key_data = yaml.safe_load(zf.read(key_name).decode('utf-8')) or {}

        else:

            configs = yaml.safe_load(content.decode('utf-8')) or {}



        if not isinstance(configs, dict):

            raise HTTPException(status_code=400, detail="配置文件顶层结构必须是字典")



        result = matchbox().admin_import_from_yaml(configs, uploaded_key_data=uploaded_key_data)

        return result

    except HTTPException:

        raise

    except Exception as e:

        print(f"Failed to import YAML: {e}")

        raise HTTPException(status_code=500, detail=str(e))





@llm_router.post('/api/ai/admin/reload-from-yaml')

async def admin_reload_from_yaml(

    admin_user: dict = Depends(require_admin)

):

    """

    管理员：从 YAML 文件强制重新加载系统平台配置

    

    ⚠️ 警告：此操作会重置数据库中的系统平台配置

    - 软禁用 YAML 中不存在的平台

    - 更新已存在平台的名称和模型

    - 用户为系统平台设置的自定义 API Key 会被保留

    

    💡 用途：当你手动编辑了 server/llm/agen_matchbox/matchbox_cfg.yaml 文件，希望将其应用到系统时使用。

    """

    try:

        matchbox().admin_reload_from_yaml()

        return {"success": True, "message": "系统平台配置已从 YAML 重新加载"}

    except Exception as e:

        print(f"Failed to reload from YAML: {e}")

        raise HTTPException(status_code=500, detail=str(e))





@llm_router.get('/api/ai/admin/export-to-yaml')

async def admin_export_to_yaml(

    admin_user: dict = Depends(require_admin)

):

    """

    管理员：获取当前系统平台配置的 YAML 数据（不写入文件）



    📌 只读接口，将数据库中的系统平台配置转换为 YAML 格式并返回内容字符串。

    注意：返回的 YAML 不再包含 api_key，密钥请单独维护在 matchbox_key.yaml 中。

    若需同时导出密钥文件，请调用 GET /api/ai/admin/export-to-yaml/download。

    若需覆盖服务器文件，请调用 POST /api/ai/admin/save-to-yaml。

    """

    import yaml

    try:

        data = matchbox().admin_build_export_data()

        yaml_str = yaml.dump(data, allow_unicode=True, sort_keys=False, default_flow_style=False)

        return {"success": True, "yaml": yaml_str, "platform_count": len(data)}

    except Exception as e:

        print(f"Failed to get YAML config: {e}")

        raise HTTPException(status_code=500, detail=str(e))





@llm_router.post('/api/ai/admin/save-to-yaml')

async def admin_save_to_yaml(

    admin_user: dict = Depends(require_admin)

):

    """

    管理员：将当前系统平台配置写入并覆盖 matchbox_cfg.yaml



    ⚠️ 警告：此操作不可逆，会完整覆盖 matchbox_cfg.yaml 的现有内容。

    💡 用途：在管理界面完成平台配置后，将配置回写到服务器文件。

    注意：导出的 YAML 不再包含 api_key，密钥会同步写入 matchbox_key.yaml。

    """

    try:

        paths = matchbox().admin_save_to_yaml()

        return {"success": True, "message": f"配置已覆盖写入 {paths['config_path']}，密钥已写入 {paths['key_path']}"}

    except Exception as e:

        print(f"Failed to save YAML: {e}")

        raise HTTPException(status_code=500, detail=str(e))





from fastapi.responses import FileResponse, Response



@llm_router.get('/api/ai/admin/export-to-yaml/download')

async def admin_export_and_download_yaml(

    admin_user: dict = Depends(require_admin)

):

    """

    管理员：将当前系统平台配置打包为 matchbox_cfg.yaml + matchbox_key.yaml 并下载。



    📌 内存直出，不覆盖服务器文件。ZIP 内包含：

    - matchbox_cfg.yaml：平台结构配置（不含密钥）

    - matchbox_key.yaml：各平台加密后的 API Key

    """

    import yaml

    import io

    import zipfile

    try:

        config_data = matchbox().admin_build_export_data()

        key_data = matchbox().admin_build_key_export_data()

        config_yaml = yaml.dump(config_data, allow_unicode=True, sort_keys=False, default_flow_style=False)

        key_yaml = yaml.dump(key_data, allow_unicode=True, sort_keys=False, default_flow_style=False)

        zip_buffer = io.BytesIO()

        with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:

            zf.writestr("matchbox_cfg.yaml", config_yaml.encode("utf-8"))

            zf.writestr("matchbox_key.yaml", key_yaml.encode("utf-8"))

        zip_buffer.seek(0)

        return Response(

            content=zip_buffer.read(),

            media_type="application/zip",

            headers={"Content-Disposition": 'attachment; filename="matchbox_config.matchbox"'},

        )

    except Exception as e:

        print(f"Failed to export and download YAML: {e}")

        raise HTTPException(status_code=500, detail=str(e))





# ==================== Admin: 排序与同步 ====================



from typing import List



class ReorderPlatformsRequest(BaseModel):

    ordered_ids: List[int]



class ReorderModelsRequest(BaseModel):

    platform_id: int

    ordered_ids: List[int]



class SetDefaultPlatformRequest(BaseModel):

    platform_id: int





@llm_router.post('/api/ai/admin/reorder-platforms')

async def admin_reorder_platforms(

    data: ReorderPlatformsRequest,

    admin_user: dict = Depends(require_admin)

):

    """管理员：重新排序系统平台"""

    try:

        matchbox().admin_reorder_sys_platforms(data.ordered_ids)

        return {"success": True}

    except Exception as e:

        print(f"Failed to reorder platforms: {e}")

        raise HTTPException(status_code=400, detail=str(e))





@llm_router.post('/api/ai/admin/reorder-models')

async def admin_reorder_models(

    data: ReorderModelsRequest,

    admin_user: dict = Depends(require_admin)

):

    """管理员：重新排序指定平台下的模型"""

    try:

        matchbox().admin_reorder_sys_models(data.platform_id, data.ordered_ids)

        return {"success": True}

    except Exception as e:

        print(f"Failed to reorder models: {e}")

        raise HTTPException(status_code=400, detail=str(e))





@llm_router.post('/api/ai/admin/set-default-platform')

async def admin_set_default_platform(

    data: SetDefaultPlatformRequest,

    admin_user: dict = Depends(require_admin)

):

    """管理员：将指定系统平台设为默认（sort_order=0）"""

    try:

        matchbox().admin_set_sys_platform_default(data.platform_id)

        return {"success": True}

    except Exception as e:

        print(f"Failed to set default platform: {e}")

        raise HTTPException(status_code=400, detail=str(e))





# ==================== Token 估算 ====================



class EstimateTokensRequest(BaseModel):

    texts: list[str]

    model: Optional[str] = None

    agent_id: Optional[str] = None





@llm_router.post('/api/ai/estimate-tokens')

async def estimate_tokens_endpoint(

    data: EstimateTokensRequest,

    user: dict = Depends(get_current_user),

):

    """估算一组文本在指定模型下的 token 总数。



    优先级：

    1. 若提供 agent_id，从数据库解析该 Agent 绑定的模型名

    2. 若直接提供 model，使用该模型名

    3. 否则使用默认估算（cl100k）

    """

    from core.file_ingest.chunking import estimate_text_tokens as estimate_tokens



    model_name = data.model



    # 尝试从 agent 绑定解析模型名

    if not model_name and data.agent_id:

        try:

            client = matchbox().get_user_llm(str(user['user_id']), agent_name=data.agent_id)

            model_name = client.llm.model_name

        except Exception:

            pass



    total = 0

    for text in data.texts:

        if text:

            total += estimate_tokens(text, model=model_name)



    return {"total_tokens": total, "model": model_name or "default"}



