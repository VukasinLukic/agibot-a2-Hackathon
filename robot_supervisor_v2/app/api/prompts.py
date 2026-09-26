import logging
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from livekit_config import PromptBuilder
from content_service.content_store.prompts.service import PromptService
from robot_services.gestures import (
    DEFAULT_GESTURE_SAFETY_POOL,
    build_gesture_policy_prompt,
    normalize_gesture_safety_pool,
)
from ..models.conversation import AgentCommandRequest
from ..utils.agent_commands import send_agent_command


router = APIRouter(prefix="/api/prompts", tags=["prompts"])
prompt_content_service = PromptService(include_examples=False)
prompt_preview_builder = PromptBuilder(include_examples=False)
logger = logging.getLogger(__name__)


class PromptItemCreateRequest(BaseModel):
    slug: str
    title: str
    prompt_text: str = ""
    initial_greeting: str = ""
    goodbye_text: str = ""


class PromptItemUpdateRequest(PromptItemCreateRequest):
    is_archived: bool = False


class EventMomentCreateRequest(PromptItemCreateRequest):
    order_index: int = 0


class EventMomentUpdateRequest(EventMomentCreateRequest):
    is_archived: bool = False


class MainPromptUpdateRequest(BaseModel):
    title: str
    prompt_text: str
    initial_greeting: str = ""
    goodbye_text: str = ""


PROMPT_RELOAD_COMMAND = "__PROMPT_RELOAD__"


def _raise_prompt_http_exception(exc: Exception) -> None:
    if isinstance(exc, LookupError):
        raise HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, ValueError):
        raise HTTPException(status_code=400, detail=str(exc))
    raise HTTPException(status_code=500, detail=f"Prompt operation failed: {str(exc)}")


def _prompt_extra_variables(request: Request) -> dict[str, str]:
    service_manager = getattr(request.app.state, "service_manager", None)
    robot_context = getattr(request.app.state, "robot_context", None)
    pool = DEFAULT_GESTURE_SAFETY_POOL

    if service_manager:
        voice_agent = service_manager.get("voice-agent")
        if voice_agent:
            pool = normalize_gesture_safety_pool(
                voice_agent.get_config().get("gesture_safety_pool", DEFAULT_GESTURE_SAFETY_POOL)
            )

    variables = {
        "gesture_policy_prompt": build_gesture_policy_prompt(pool),
    }
    if isinstance(robot_context, dict):
        robot_name = str(robot_context.get("name", "")).strip()
        if robot_name:
            variables["robot_name"] = robot_name
    return variables


async def _notify_active_agent(request: Request) -> bool:
    """Apply YAML prompt changes to the current agent without restarting it."""
    manual_controller = getattr(request.app.state, "manual_controller", None)
    if not manual_controller:
        return False
    status = manual_controller.get_status()
    state = status.get("state")
    state_value = getattr(state, "value", state)
    if state_value != "engaged" or not status.get("room"):
        return False
    try:
        await send_agent_command(
            AgentCommandRequest(
                text=PROMPT_RELOAD_COMMAND,
                plain_text=True,
                room=str(status["room"]),
            ),
            room=str(status["room"]),
        )
        return True
    except Exception as exc:
        # The YAML write is authoritative. A transient LiveKit failure must not
        # turn a successful save into an ambiguous API failure.
        logger.warning("Prompt saved, but live agent reload failed: %s", exc)
        return False


@router.get("/main")
async def get_main_prompt():
    try:
        return {"item": prompt_content_service.get_main_prompt()}
    except Exception as e:
        _raise_prompt_http_exception(e)


@router.put("/main")
async def update_main_prompt(request: Request, payload: MainPromptUpdateRequest):
    try:
        item = prompt_content_service.update_main_prompt(
            slug="main",
            is_archived=False,
            **payload.model_dump(),
        )
        runtime_applied = await _notify_active_agent(request)
        return {"item": item, "runtime_applied": runtime_applied}
    except Exception as e:
        _raise_prompt_http_exception(e)


# X2/A2 handover compatibility: both names address the same protected YAML row.
@router.get("/main-behavior")
async def get_main_behavior_prompt():
    return await get_main_prompt()


@router.put("/main-behavior")
async def update_main_behavior_prompt(request: Request, payload: MainPromptUpdateRequest):
    return await update_main_prompt(request, payload)


@router.delete("/main-behavior")
async def delete_main_behavior_prompt():
    raise HTTPException(status_code=409, detail="The main behavior prompt is required and cannot be deleted.")


@router.get("/personas")
async def list_personas(include_archived: bool = False, locale: str | None = None):
    try:
        if (locale or "").lower().startswith("en"):
            return {
                "items": prompt_content_service.list_english_personas(
                    include_archived=include_archived
                ),
                "active": prompt_content_service.get_active_selection().get("persona", ""),
            }
        return {
            "items": prompt_content_service.list_personas(include_archived=include_archived),
            "active": prompt_content_service.get_active_selection().get("persona", ""),
        }
    except Exception as e:
        _raise_prompt_http_exception(e)


@router.post("/personas")
async def create_persona(payload: PromptItemCreateRequest):
    try:
        item = prompt_content_service.create_speaking_style(**payload.model_dump())
        item.update({"active": False, "protected": False, "role": "persona"})
        return {"item": item}
    except Exception as e:
        _raise_prompt_http_exception(e)


@router.put("/personas/{persona_id}")
async def update_persona(
    request: Request,
    persona_id: str,
    payload: PromptItemUpdateRequest,
    locale: str | None = None,
):
    try:
        if (locale or "").lower().startswith("en"):
            item = prompt_content_service.update_english_persona(
                persona_id, **payload.model_dump()
            )
        else:
            item = prompt_content_service.update_speaking_style(
                persona_id,
                **payload.model_dump(),
            )
        active = prompt_content_service.get_active_selection().get("persona", "")
        item.update({"active": item["slug"] == active, "protected": False, "role": "persona"})
        runtime_applied = await _notify_active_agent(request) if item["active"] else False
        return {"item": item, "runtime_applied": runtime_applied}
    except Exception as e:
        _raise_prompt_http_exception(e)


@router.delete("/personas/{persona_id}")
async def delete_persona(request: Request, persona_id: str):
    try:
        prompt_content_service.delete_speaking_style(persona_id)
        runtime_applied = await _notify_active_agent(request)
        return {
            "success": True,
            "active": prompt_content_service.get_active_selection().get("persona", ""),
            "runtime_applied": runtime_applied,
        }
    except Exception as e:
        _raise_prompt_http_exception(e)


@router.post("/personas/{persona_id}/activate")
async def activate_persona(request: Request, persona_id: str):
    try:
        active = prompt_content_service.set_active_persona(persona_id)
        preview = prompt_preview_builder.build(extra_variables=_prompt_extra_variables(request))
        runtime_applied = await _notify_active_agent(request)
        return {
            "success": True,
            "active": active,
            "runtime_applied": runtime_applied,
            "prompt_preview": preview[:500] + "..." if len(preview) > 500 else preview,
        }
    except Exception as e:
        _raise_prompt_http_exception(e)


@router.get("/modes")
async def list_prompt_modes(include_archived: bool = False):
    try:
        return {"items": prompt_content_service.list_modes(include_archived=include_archived)}
    except Exception as e:
        _raise_prompt_http_exception(e)


@router.post("/modes")
async def create_prompt_mode(payload: PromptItemCreateRequest):
    try:
        return {"item": prompt_content_service.create_mode(**payload.model_dump())}
    except Exception as e:
        _raise_prompt_http_exception(e)


@router.put("/modes/{mode_id}")
async def update_prompt_mode(mode_id: str, payload: PromptItemUpdateRequest):
    try:
        return {"item": prompt_content_service.update_mode(mode_id, **payload.model_dump())}
    except Exception as e:
        _raise_prompt_http_exception(e)


@router.delete("/modes/{mode_id}")
async def delete_prompt_mode(mode_id: str):
    try:
        prompt_content_service.delete_mode(mode_id)
        return {"success": True}
    except Exception as e:
        _raise_prompt_http_exception(e)


@router.get("/speaking-styles")
async def list_prompt_speaking_styles(include_archived: bool = False):
    try:
        return {"items": prompt_content_service.list_speaking_styles(include_archived=include_archived)}
    except Exception as e:
        _raise_prompt_http_exception(e)


@router.post("/speaking-styles")
async def create_prompt_speaking_style(payload: PromptItemCreateRequest):
    try:
        return {
            "item": prompt_content_service.create_speaking_style(
                **payload.model_dump(exclude={"initial_greeting", "goodbye_text"})
            )
        }
    except Exception as e:
        _raise_prompt_http_exception(e)


@router.put("/speaking-styles/{speaking_style_id}")
async def update_prompt_speaking_style(speaking_style_id: str, payload: PromptItemUpdateRequest):
    try:
        return {
            "item": prompt_content_service.update_speaking_style(
                speaking_style_id,
                **payload.model_dump(exclude={"initial_greeting", "goodbye_text"}),
            )
        }
    except Exception as e:
        _raise_prompt_http_exception(e)


@router.delete("/speaking-styles/{speaking_style_id}")
async def delete_prompt_speaking_style(speaking_style_id: str):
    try:
        prompt_content_service.delete_speaking_style(speaking_style_id)
        return {"success": True}
    except Exception as e:
        _raise_prompt_http_exception(e)


@router.get("/event-settings")
async def list_prompt_event_settings(include_archived: bool = False):
    try:
        return {"items": prompt_content_service.list_event_settings(include_archived=include_archived)}
    except Exception as e:
        _raise_prompt_http_exception(e)


@router.post("/event-settings")
async def create_prompt_event_setting(payload: PromptItemCreateRequest):
    try:
        return {"item": prompt_content_service.create_event_setting(**payload.model_dump())}
    except Exception as e:
        _raise_prompt_http_exception(e)


@router.put("/event-settings/{event_setting_id}")
async def update_prompt_event_setting(event_setting_id: str, payload: PromptItemUpdateRequest):
    try:
        return {"item": prompt_content_service.update_event_setting(event_setting_id, **payload.model_dump())}
    except Exception as e:
        _raise_prompt_http_exception(e)


@router.delete("/event-settings/{event_setting_id}")
async def delete_prompt_event_setting(event_setting_id: str):
    try:
        prompt_content_service.delete_event_setting(event_setting_id)
        return {"success": True}
    except Exception as e:
        _raise_prompt_http_exception(e)


@router.get("/event-settings/{event_setting_id}/event-moments")
async def list_prompt_event_moments(event_setting_id: str, include_archived: bool = False):
    try:
        return {
            "items": prompt_content_service.list_event_moments(
                event_setting_id,
                include_archived=include_archived,
            )
        }
    except Exception as e:
        _raise_prompt_http_exception(e)


@router.post("/event-settings/{event_setting_id}/event-moments")
async def create_prompt_event_moment(event_setting_id: str, payload: EventMomentCreateRequest):
    try:
        return {
            "item": prompt_content_service.create_event_moment(
                event_setting_id=event_setting_id,
                **payload.model_dump(),
            )
        }
    except Exception as e:
        _raise_prompt_http_exception(e)


@router.put("/event-moments/{event_moment_id}")
async def update_prompt_event_moment(event_moment_id: str, payload: EventMomentUpdateRequest):
    try:
        return {"item": prompt_content_service.update_event_moment(event_moment_id, **payload.model_dump())}
    except Exception as e:
        _raise_prompt_http_exception(e)


@router.delete("/event-moments/{event_moment_id}")
async def delete_prompt_event_moment(event_moment_id: str):
    try:
        prompt_content_service.delete_event_moment(event_moment_id)
        return {"success": True}
    except Exception as e:
        _raise_prompt_http_exception(e)


@router.get("/options")
async def get_prompt_options():
    try:
        return {
            "options": prompt_content_service.get_available_options(),
            "active": prompt_content_service.get_active_selection(),
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to get prompt options: {str(e)}")


@router.get("/active")
async def get_active_prompt():
    try:
        return prompt_content_service.get_active_selection()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to get active prompt: {str(e)}")


@router.post("/update")
async def update_prompt_config(
    request: Request,
    core_mode: Optional[str] = None,
    persona: Optional[str] = None,
    context: Optional[str] = None,
    phase: Optional[str] = None,
):
    try:
        new_prompt = prompt_preview_builder.update_and_build(
            core_mode=core_mode,
            persona=persona,
            context=context,
            phase=phase,
            extra_variables=_prompt_extra_variables(request),
        )

        return {
            "success": True,
            "active": prompt_content_service.get_active_selection(),
            "prompt_preview": new_prompt[:500] + "..." if len(new_prompt) > 500 else new_prompt,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to update prompt: {str(e)}")


@router.post("/preview")
async def preview_prompt(
    request: Request,
    core_mode: Optional[str] = None,
    persona: Optional[str] = None,
    context: Optional[str] = None,
    phase: Optional[str] = None,
):
    try:
        prompt = prompt_preview_builder.build(
            core_mode=core_mode,
            persona=persona,
            context=context,
            phase=phase,
            extra_variables=_prompt_extra_variables(request),
        )
        return {"prompt": prompt}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to preview prompt: {str(e)}")
