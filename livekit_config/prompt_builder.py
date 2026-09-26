import logging
import os
from pathlib import Path
from typing import Optional

import yaml

from content_service.content_store.prompts.service import PromptService
from robot_services.gestures import build_gesture_policy_prompt, get_configured_gesture_safety_pool
from .voice_canary import english_voice_enabled

logger = logging.getLogger(__name__)

_GESTURE_POLICY_PLACEHOLDER = "{gesture_policy_prompt}"
_LEGACY_GESTURE_BLOCK = """  Gestures:
  - You have access to a few standard gestures you can perform. 
  - Available gestures you can perform (and only these):
    - release arm
    - left kiss
    - right kiss
    - hands up
    - clap
    - high five
    - hug
    - right heart
    - reject
    - right hand up
    - x-ray
    - face wave
    - high wave
    - shake hand
  - You should trigger appropriate gestures when requested.
  - You should also trigger appropriate gestures by yourself when the situation is relevant (e.g. greeting, goodbye, congratulations - clap,...)"""
_COMPAT_GESTURE_BLOCK = f"""  Gestures:
  {_GESTURE_POLICY_PLACEHOLDER}"""
_PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"
_ENGLISH_PROMPT_FILES = {
    "mode": _PROMPTS_DIR / "base.en.yaml",
    "speaking_style": _PROMPTS_DIR / "personas.en.yaml",
    "event_setting": _PROMPTS_DIR / "contexts.en.yaml",
    "event_moment": _PROMPTS_DIR / "event_parts.en.yaml",
}


def _env_first_nonempty(*names: str, default: str) -> str:
    for name in names:
        value = os.getenv(name)
        if value and value.strip():
            return value.strip()
    return default


class PromptBuilder:
    """Build system prompts from local prompt files."""

    def __init__(self, *, include_examples: bool = True):
        self.prompt_service = PromptService(include_examples=include_examples)
        gesture_pool = get_configured_gesture_safety_pool()
        self.default_variables = {
            "robot_name": _env_first_nonempty("HUMANOID_ROBOT_NAME", "ROBOT_NAME", default="Primus"),
            "company_name": os.getenv("COMPANY_NAME", "Petrol"),
            "gesture_policy_prompt": build_gesture_policy_prompt(gesture_pool),
        }

    def _substitute_variables(self, text: str, extra_vars: Optional[dict] = None) -> str:
        variables = self.default_variables.copy()
        if extra_vars:
            variables.update(extra_vars)

        for key, value in variables.items():
            text = text.replace(f"{{{key}}}", str(value))
        return text

    def _apply_gesture_policy_compat(self, text: str) -> str:
        if _GESTURE_POLICY_PLACEHOLDER in text:
            return text
        if _LEGACY_GESTURE_BLOCK in text:
            return text.replace(_LEGACY_GESTURE_BLOCK, _COMPAT_GESTURE_BLOCK)
        return text

    @staticmethod
    def _english_entry(section: str, slug: str, *, context: str | None = None) -> dict[str, str]:
        payload = yaml.safe_load(_ENGLISH_PROMPT_FILES[section].read_text(encoding="utf-8")) or {}
        if section == "event_moment":
            payload = (payload.get("contexts") or {}).get(context or "", {})
        entry = payload.get(slug)
        if entry is None:
            raise ValueError(f"Missing English {section} prompt for {slug!r}")
        if isinstance(entry, dict):
            return {
                "prompt_text": str(entry.get("prompt_text") or ""),
                "initial_greeting": str(entry.get("initial_greeting") or ""),
                "goodbye_text": str(entry.get("goodbye_text") or ""),
            }
        return {"prompt_text": str(entry), "initial_greeting": "", "goodbye_text": ""}

    def _english_parts(self, *, mode: str, persona: str, context: str, phase: str) -> dict[str, dict[str, str]]:
        return {
            "mode": self._english_entry("mode", mode),
            "speaking_style": self._english_entry("speaking_style", persona),
            "event_setting": self._english_entry("event_setting", context),
            "event_moment": self._english_entry("event_moment", phase, context=context),
        }

    def build(
        self,
        core_mode: Optional[str] = None,
        persona: Optional[str] = None,
        context: Optional[str] = None,
        phase: Optional[str] = None,
        extra_variables: Optional[dict] = None,
    ) -> str:
        active = self.prompt_service.get_active_selection()
        effective_core_mode = core_mode or active.get("core_mode")
        effective_persona = persona or active.get("persona")
        effective_context = context or active.get("context")
        effective_phase = phase or active.get("phase")

        if english_voice_enabled():
            localized = self._english_parts(
                mode=effective_core_mode,
                persona=effective_persona,
                context=effective_context,
                phase=effective_phase,
            )
            parts = {key: value["prompt_text"] for key, value in localized.items()}
        else:
            parts = self.prompt_service.get_prompt_parts(
                core_mode=effective_core_mode,
                persona=effective_persona,
                context=effective_context,
                phase=effective_phase,
            )

        prompt_sections = [
            parts["mode"],
            parts["speaking_style"],
            parts["event_setting"],
            parts["event_moment"],
        ]
        full_prompt = "\n\n---\n\n".join(
            part for part in prompt_sections if part and part.strip()
        )
        full_prompt = self._apply_gesture_policy_compat(full_prompt)
        full_prompt = self._substitute_variables(full_prompt, extra_variables)

        logger.info(
            "Built prompt: core_mode=%s, persona=%s, context=%s, phase=%s",
            effective_core_mode,
            effective_persona,
            effective_context,
            effective_phase,
        )
        return full_prompt

    def build_initial_greeting(
        self,
        core_mode: Optional[str] = None,
        persona: Optional[str] = None,
        context: Optional[str] = None,
        phase: Optional[str] = None,
        extra_variables: Optional[dict] = None,
    ) -> str:
        active = self.prompt_service.get_active_selection()
        effective_core_mode = core_mode or active.get("core_mode")
        effective_persona = persona or active.get("persona")
        effective_context = context or active.get("context")
        effective_phase = phase or active.get("phase")

        if english_voice_enabled():
            parts = self._english_parts(
                mode=effective_core_mode,
                persona=effective_persona,
                context=effective_context,
                phase=effective_phase,
            )
            greeting = next(
                (parts[key]["initial_greeting"] for key in ("event_moment", "event_setting", "speaking_style", "mode") if parts[key]["initial_greeting"]),
                "Hello. How can I help you today?",
            )
        else:
            greeting = self.prompt_service.get_initial_greeting(
                core_mode=effective_core_mode,
                persona=effective_persona,
                context=effective_context,
                phase=effective_phase,
            )
        return self._substitute_variables(greeting, extra_variables).strip()

    def build_goodbye_text(
        self,
        core_mode: Optional[str] = None,
        persona: Optional[str] = None,
        context: Optional[str] = None,
        phase: Optional[str] = None,
        extra_variables: Optional[dict] = None,
    ) -> str:
        active = self.prompt_service.get_active_selection()
        effective_core_mode = core_mode or active.get("core_mode")
        effective_persona = persona or active.get("persona")
        effective_context = context or active.get("context")
        effective_phase = phase or active.get("phase")

        if english_voice_enabled():
            parts = self._english_parts(
                mode=effective_core_mode,
                persona=effective_persona,
                context=effective_context,
                phase=effective_phase,
            )
            goodbye_text = next(
                (parts[key]["goodbye_text"] for key in ("event_moment", "event_setting", "speaking_style", "mode") if parts[key]["goodbye_text"]),
                "Goodbye. Have a great day.",
            )
        else:
            goodbye_text = self.prompt_service.get_goodbye_text(
                core_mode=effective_core_mode,
                persona=effective_persona,
                context=effective_context,
                phase=effective_phase,
            )
        return self._substitute_variables(goodbye_text, extra_variables).strip()

    def reload(self) -> None:
        logger.info("Prompt builder reload requested; file-backed builder reloads on each read.")

    def save_config(self) -> None:
        logger.info("Prompt builder save_config requested; active prompt state is stored in local files.")

    def update_active(
        self,
        core_mode: Optional[str] = None,
        persona: Optional[str] = None,
        context: Optional[str] = None,
        phase: Optional[str] = None,
        save: bool = True,
    ) -> None:
        active = self.prompt_service.get_active_selection()

        if not active and not all([core_mode, persona, context, phase]):
            raise ValueError("No active prompt state found. Provide all selections explicitly.")

        self.prompt_service.set_active_selection(
            core_mode=core_mode or active["core_mode"],
            persona=persona or active["persona"],
            context=context or active["context"],
            phase=phase or active["phase"],
        )

    def get_available_options(self) -> dict:
        return self.prompt_service.get_available_options()

    def get_active(self) -> dict:
        return self.prompt_service.get_active_selection()

    def update_and_build(
        self,
        core_mode: Optional[str] = None,
        persona: Optional[str] = None,
        context: Optional[str] = None,
        phase: Optional[str] = None,
        extra_variables: Optional[dict] = None,
    ) -> str:
        self.update_active(
            core_mode=core_mode,
            persona=persona,
            context=context,
            phase=phase,
        )
        return self.build(extra_variables=extra_variables)


_builder: Optional[PromptBuilder] = None


def get_prompt_builder() -> PromptBuilder:
    global _builder
    if _builder is None:
        _builder = PromptBuilder()
    return _builder


def build_system_prompt(**kwargs) -> str:
    return get_prompt_builder().build(**kwargs)


def build_initial_greeting(**kwargs) -> str:
    return get_prompt_builder().build_initial_greeting(**kwargs)


def build_goodbye_text(**kwargs) -> str:
    return get_prompt_builder().build_goodbye_text(**kwargs)


def update_and_rebuild(**kwargs) -> str:
    return get_prompt_builder().update_and_build(**kwargs)
