from __future__ import annotations

from typing import Any

from ..file_store import (
    REPO_ROOT,
    load_yaml,
    slug_to_title,
    stable_id,
    write_yaml_atomic,
)


BLANK_EVENT_SETTING_SLUG = "no_event_setting"
BLANK_EVENT_SETTING_TITLE = "Bez podešavanja događaja"

BLANK_EVENT_MOMENT_SLUG = "no_event_moment"
BLANK_EVENT_MOMENT_TITLE = "Bez trenutka događaja"

DEFAULT_INITIAL_GREETING = "Zdravo. Drago mi je da vas vidim. Kako mogu da vam pomognem danas?"
DEFAULT_GOODBYE_TEXT = "Pozdrav. Želim vam prijatan dan."
MAIN_PROMPT_SLUG = "main"
LEGACY_MAIN_PROMPT_SLUG = "standard"

PROMPT_CONFIG_PATH = REPO_ROOT / "livekit_config" / "prompt_config.yaml"
PROMPT_CONFIG_EXAMPLE_PATH = REPO_ROOT / "livekit_config" / "prompt_config.example.yaml"
PROMPTS_DIR = REPO_ROOT / "livekit_config" / "prompts"
ENGLISH_PERSONAS_PATH = PROMPTS_DIR / "personas.en.yaml"

PROMPT_FILES = {
    "modes": (PROMPTS_DIR / "base.yaml", PROMPTS_DIR / "base.example.yaml"),
    "speaking_styles": (PROMPTS_DIR / "personas.yaml", PROMPTS_DIR / "personas.example.yaml"),
    "event_settings": (PROMPTS_DIR / "contexts.yaml", PROMPTS_DIR / "contexts.example.yaml"),
    "event_moments": (PROMPTS_DIR / "event_parts.yaml", PROMPTS_DIR / "event_parts.example.yaml"),
}


class PromptService:
    def __init__(self, *, include_examples: bool = True):
        self.include_examples = include_examples

    def _normalize_slug(self, slug: str) -> str:
        normalized = (slug or "").strip()
        if not normalized:
            raise ValueError("Slug is required.")
        return normalized

    def _normalize_title(self, title: str) -> str:
        normalized = (title or "").strip()
        if not normalized:
            raise ValueError("Title is required.")
        return normalized

    def _load_prompt_map(self, section: str) -> dict[str, Any]:
        runtime_path, example_path = PROMPT_FILES[section]
        fallback_path = example_path if self.include_examples else None
        payload = load_yaml(runtime_path, fallback_path, {}) or {}
        return payload if isinstance(payload, dict) else {}

    def _write_prompt_map(self, section: str, payload: dict[str, Any]) -> None:
        runtime_path, example_path = PROMPT_FILES[section]
        write_yaml_atomic(runtime_path, payload)

    def _load_english_personas(self) -> dict[str, Any]:
        payload = load_yaml(ENGLISH_PERSONAS_PATH, None, {}) or {}
        return payload if isinstance(payload, dict) else {}

    def list_english_personas(self, include_archived: bool = False) -> list[dict]:
        active_slug = self.get_active_selection().get("persona", "")
        items = [
            self._entry_to_item(section="speaking_styles", slug=slug, entry=entry)
            for slug, entry in self._load_english_personas().items()
        ]
        if not include_archived:
            items = [item for item in items if not item["is_archived"]]
        for item in items:
            item.update(
                {
                    "active": item["slug"] == active_slug,
                    "protected": False,
                    "role": "persona",
                }
            )
        return items

    def update_english_persona(self, persona_id: int | str, **payload) -> dict:
        data = self._load_english_personas()
        ref_text = str(persona_id)
        slug = next(
            (
                candidate_slug
                for candidate_slug, entry in data.items()
                if candidate_slug == ref_text
                or str(
                    self._entry_to_item(
                        section="speaking_styles",
                        slug=candidate_slug,
                        entry=entry,
                    )["id"]
                )
                == ref_text
            ),
            None,
        )
        if slug is None:
            raise LookupError("English persona not found.")

        requested_slug = self._normalize_slug(payload.get("slug") or slug)
        if requested_slug != slug and requested_slug in data:
            raise ValueError(f"Persona slug already exists: {requested_slug}")
        entry = self._item_to_entry(
            title=payload.get("title") or slug_to_title(requested_slug),
            prompt_text=payload.get("prompt_text", ""),
            initial_greeting=payload.get("initial_greeting", ""),
            goodbye_text=payload.get("goodbye_text", ""),
            is_archived=bool(payload.get("is_archived", False)),
        )
        if requested_slug != slug:
            rebuilt: dict[str, Any] = {}
            for candidate_slug, candidate_entry in data.items():
                rebuilt[requested_slug if candidate_slug == slug else candidate_slug] = (
                    entry if candidate_slug == slug else candidate_entry
                )
            data = rebuilt
        else:
            data[slug] = entry
        write_yaml_atomic(ENGLISH_PERSONAS_PATH, data)
        item = self._entry_to_item(
            section="speaking_styles",
            slug=requested_slug,
            entry=entry,
        )
        item.update(
            {
                "active": requested_slug == self.get_active_selection().get("persona", ""),
                "protected": False,
                "role": "persona",
            }
        )
        return item

    def _load_active_config(self) -> dict[str, Any]:
        fallback_path = PROMPT_CONFIG_EXAMPLE_PATH if self.include_examples else None
        payload = load_yaml(PROMPT_CONFIG_PATH, fallback_path, {}) or {}
        return payload if isinstance(payload, dict) else {}

    def _write_active_config(self, payload: dict[str, Any]) -> None:
        write_yaml_atomic(PROMPT_CONFIG_PATH, payload)

    def _main_storage_slug(self) -> str:
        """Return the canonical main slug, with read compatibility for old installs."""
        modes = self._load_prompt_map("modes")
        if MAIN_PROMPT_SLUG in modes:
            return MAIN_PROMPT_SLUG
        if LEGACY_MAIN_PROMPT_SLUG in modes:
            return LEGACY_MAIN_PROMPT_SLUG
        raise LookupError("The required main prompt is missing.")

    def _entry_to_item(
        self,
        *,
        section: str,
        slug: str,
        entry: Any,
        parent_slug: str | None = None,
        order_index: int = 0,
    ) -> dict:
        if isinstance(entry, dict):
            prompt_text = str(entry.get("prompt_text", "") or "")
            title = str(entry.get("title") or slug_to_title(slug))
            initial_greeting = str(entry.get("initial_greeting", "") or "")
            goodbye_text = str(entry.get("goodbye_text", "") or "")
            is_archived = bool(entry.get("is_archived", False))
            order_index = int(entry.get("order_index", order_index) or 0)
        else:
            prompt_text = str(entry or "")
            title = slug_to_title(slug)
            initial_greeting = ""
            goodbye_text = ""
            is_archived = False

        item = {
            "id": stable_id(":".join(part for part in (section, parent_slug, slug) if part)),
            "slug": slug,
            "title": title,
            "prompt_text": prompt_text,
            "initial_greeting": initial_greeting,
            "goodbye_text": goodbye_text,
            "is_archived": is_archived,
            "created_at": None,
            "updated_at": None,
        }
        if section == "event_moments":
            item.update(
                {
                    "event_setting_id": stable_id(f"event_settings:{parent_slug or ''}"),
                    "event_setting_slug": parent_slug or "",
                    "order_index": order_index,
                }
            )
        return item

    def _item_to_entry(
        self,
        *,
        title: str,
        prompt_text: str,
        initial_greeting: str = "",
        goodbye_text: str = "",
        is_archived: bool = False,
        order_index: int | None = None,
    ) -> dict:
        payload: dict[str, Any] = {
            "title": self._normalize_title(title),
            "prompt_text": prompt_text or "",
        }
        if initial_greeting:
            payload["initial_greeting"] = initial_greeting
        if goodbye_text:
            payload["goodbye_text"] = goodbye_text
        if is_archived:
            payload["is_archived"] = True
        if order_index is not None:
            payload["order_index"] = int(order_index)
        return payload

    def _list_items(self, section: str, include_archived: bool) -> list[dict]:
        items = [
            self._entry_to_item(section=section, slug=slug, entry=entry)
            for slug, entry in self._load_prompt_map(section).items()
        ]
        if section == "event_settings" and self.include_examples:
            items.append(
                self._entry_to_item(
                    section=section,
                    slug=BLANK_EVENT_SETTING_SLUG,
                    entry={"title": BLANK_EVENT_SETTING_TITLE, "prompt_text": ""},
                )
            )
        if not include_archived:
            items = [item for item in items if not item["is_archived"]]
        return items

    def _resolve_item_slug(self, section: str, ref: int | str, *, parent_slug: str | None = None) -> str:
        candidates = (
            self.list_event_moments(parent_slug or "", include_archived=True)
            if section == "event_moments"
            else self._list_items(section, include_archived=True)
        )
        ref_text = str(ref)
        for item in candidates:
            if item["slug"] == ref_text or str(item["id"]) == ref_text:
                return item["slug"]
        raise LookupError("Prompt item not found.")

    def _get_item(self, section: str, slug: str, *, parent_slug: str | None = None) -> dict | None:
        if section == "event_moments":
            for item in self.list_event_moments(parent_slug or "", include_archived=True):
                if item["slug"] == slug:
                    return item
            return None
        for item in self._list_items(section, include_archived=True):
            if item["slug"] == slug:
                return item
        return None

    def _load_event_moment_contexts(self) -> dict[str, dict[str, Any]]:
        raw = self._load_prompt_map("event_moments")
        if isinstance(raw.get("contexts"), dict):
            return {
                str(context_slug): (moments if isinstance(moments, dict) else {})
                for context_slug, moments in raw["contexts"].items()
            }
        active_context = self.get_active_selection().get("context", "petrol_planning_conference_2026")
        return {active_context: raw}

    def _write_event_moment_contexts(self, payload: dict[str, dict[str, Any]]) -> None:
        self._write_prompt_map("event_moments", {"contexts": payload})

    def get_active_selection(self) -> dict[str, str]:
        active = self._load_active_config().get("active", {}) or {}
        if not active and not self.include_examples:
            return {}
        return {
            "core_mode": active.get("core_mode", self._main_storage_slug()),
            "persona": active.get("persona", ""),
            "context": active.get("context", "petrol_planning_conference_2026"),
            "phase": active.get("phase", BLANK_EVENT_MOMENT_SLUG),
        }

    def set_active_selection(
        self,
        *,
        core_mode: str,
        persona: str,
        context: str,
        phase: str,
    ) -> dict[str, str]:
        core_mode = self._normalize_slug(core_mode)
        persona = self._normalize_slug(persona)
        context = self._normalize_slug(context)
        phase = self._normalize_slug(phase)

        if self._get_item("modes", core_mode) is None:
            raise ValueError(f"Unknown mode: {core_mode}")
        if persona and self._get_item("speaking_styles", persona) is None:
            raise ValueError(f"Unknown speaking style: {persona}")
        if self._get_item("event_settings", context) is None:
            raise ValueError(f"Unknown event setting: {context}")
        if self._get_item("event_moments", phase, parent_slug=context) is None:
            raise ValueError(f"Unknown event moment '{phase}' for event '{context}'")

        config = self._load_active_config()
        config["active"] = {
            "context": context,
            "core_mode": core_mode,
            "persona": persona,
            "phase": phase,
        }
        config.setdefault("composition_order", ["base", "persona", "context", "phase"])
        config.setdefault("variables", {"company_name": "Petrol", "robot_name": "Robot"})
        self._write_active_config(config)
        return self.get_active_selection()

    def get_available_options(self) -> dict:
        event_settings = self.list_event_settings()
        return {
            "core_modes": [item["slug"] for item in self.list_modes()],
            "personas": [item["slug"] for item in self.list_speaking_styles()],
            "contexts": [item["slug"] for item in event_settings],
            "event_moments_by_context": {
                event["slug"]: [item["slug"] for item in self.list_event_moments(event["slug"])]
                for event in event_settings
            },
        }

    def get_prompt_parts(
        self,
        *,
        core_mode: str | None = None,
        persona: str | None = None,
        context: str | None = None,
        phase: str | None = None,
    ) -> dict[str, str]:
        active = self.get_active_selection()
        core_mode = core_mode or active["core_mode"]
        persona = persona or active["persona"]
        context = context or active["context"]
        phase = phase or active["phase"]

        mode = self._get_item("modes", core_mode)
        style = self._get_item("speaking_styles", persona) if persona else None
        event = self._get_item("event_settings", context)
        moment = self._get_item("event_moments", phase, parent_slug=context)
        if not all([mode, event, moment]):
            raise ValueError("Invalid prompt selection.")

        return {
            "mode": mode["prompt_text"],
            "speaking_style": style["prompt_text"] if style else "",
            "event_setting": event["prompt_text"],
            "event_moment": moment["prompt_text"],
        }

    def get_initial_greeting(self, **kwargs) -> str:
        return self._first_prompt_text("initial_greeting", **kwargs)

    def get_goodbye_text(self, **kwargs) -> str:
        return self._first_prompt_text("goodbye_text", **kwargs)

    def _first_prompt_text(
        self,
        field: str,
        *,
        core_mode: str | None = None,
        persona: str | None = None,
        context: str | None = None,
        phase: str | None = None,
    ) -> str:
        active = self.get_active_selection()
        core_mode = core_mode or active["core_mode"]
        persona = persona if persona is not None else active.get("persona", "")
        context = context or active["context"]
        phase = phase or active["phase"]
        items = [
            self._get_item("event_moments", phase, parent_slug=context),
            self._get_item("event_settings", context),
            self._get_item("speaking_styles", persona) if persona else None,
            self._get_item("modes", core_mode),
        ]
        for item in items:
            normalized = ((item or {}).get(field) or "").strip()
            if normalized:
                return normalized
        if field == "initial_greeting":
            return DEFAULT_INITIAL_GREETING
        if field == "goodbye_text":
            return DEFAULT_GOODBYE_TEXT
        return ""

    def list_modes(self, include_archived: bool = False) -> list[dict]:
        return self._list_items("modes", include_archived)

    def create_mode(self, **payload) -> dict:
        raise ValueError("Main is the only base prompt and cannot be duplicated.")

    def update_mode(self, mode_id: int | str, **payload) -> dict:
        main_slug = self._main_storage_slug()
        resolved_slug = self._resolve_item_slug("modes", mode_id)
        if resolved_slug != main_slug:
            raise ValueError("Only the main base prompt can be edited.")
        payload = dict(payload)
        payload["slug"] = MAIN_PROMPT_SLUG
        item = self._update_item("modes", mode_id, payload)
        item["protected"] = True
        item["role"] = "main"
        return item

    def delete_mode(self, mode_id: int | str) -> None:
        self._resolve_item_slug("modes", mode_id)
        raise ValueError("The main prompt is required and cannot be deleted.")

    def get_main_prompt(self) -> dict:
        slug = self._main_storage_slug()
        item = self._get_item("modes", slug)
        if item is None:
            raise LookupError("The required main prompt is missing.")
        item["slug"] = MAIN_PROMPT_SLUG
        item["protected"] = True
        item["active"] = True
        item["role"] = "main"
        return item

    def update_main_prompt(self, **payload) -> dict:
        return self.update_mode(self._main_storage_slug(), **payload)

    def list_speaking_styles(self, include_archived: bool = False) -> list[dict]:
        return self._list_items("speaking_styles", include_archived)

    def list_personas(self, include_archived: bool = False) -> list[dict]:
        active_slug = self.get_active_selection().get("persona", "")
        items = self.list_speaking_styles(include_archived=include_archived)
        for item in items:
            item["active"] = item["slug"] == active_slug
            item["protected"] = False
            item["role"] = "persona"
        return items

    def create_speaking_style(self, **payload) -> dict:
        return self._create_item("speaking_styles", payload)

    def update_speaking_style(self, speaking_style_id: int | str, **payload) -> dict:
        return self._update_item("speaking_styles", speaking_style_id, payload)

    def delete_speaking_style(self, speaking_style_id: int | str) -> None:
        slug = self._resolve_item_slug("speaking_styles", speaking_style_id)
        data = self._load_prompt_map("speaking_styles")
        data.pop(slug, None)
        self._write_prompt_map("speaking_styles", data)
        active = self.get_active_selection()
        if active.get("persona") == slug:
            remaining = self.list_speaking_styles()
            active["persona"] = remaining[0]["slug"] if remaining else ""
            config = self._load_active_config()
            config["active"] = active
            self._write_active_config(config)

    def set_active_persona(self, persona_id: int | str) -> dict:
        slug = self._resolve_item_slug("speaking_styles", persona_id)
        item = self._get_item("speaking_styles", slug)
        if item is None or item.get("is_archived"):
            raise ValueError("Archived or unknown personas cannot be activated.")
        active = self.get_active_selection()
        active["core_mode"] = self._main_storage_slug()
        active["persona"] = slug
        config = self._load_active_config()
        config["active"] = active
        config.setdefault("composition_order", ["base", "persona", "context", "phase"])
        self._write_active_config(config)
        return self.get_active_selection()

    def list_event_settings(self, include_archived: bool = False) -> list[dict]:
        return self._list_items("event_settings", include_archived)

    def create_event_setting(self, **payload) -> dict:
        slug = self._normalize_slug(payload.get("slug", ""))
        if slug == BLANK_EVENT_SETTING_SLUG:
            raise ValueError("The reserved blank event setting slug cannot be created manually.")
        item = self._create_item("event_settings", payload)
        contexts = self._load_event_moment_contexts()
        contexts.setdefault(slug, {})
        self._write_event_moment_contexts(contexts)
        return item

    def update_event_setting(self, event_setting_id: int | str, **payload) -> dict:
        old_slug = self._resolve_item_slug("event_settings", event_setting_id)
        if old_slug == BLANK_EVENT_SETTING_SLUG:
            raise ValueError("The reserved blank event setting cannot be updated.")
        item = self._update_item("event_settings", event_setting_id, payload)
        new_slug = item["slug"]
        if old_slug != new_slug:
            contexts = self._load_event_moment_contexts()
            contexts[new_slug] = contexts.pop(old_slug, {})
            self._write_event_moment_contexts(contexts)
        return item

    def delete_event_setting(self, event_setting_id: int | str) -> None:
        slug = self._resolve_item_slug("event_settings", event_setting_id)
        if slug == BLANK_EVENT_SETTING_SLUG:
            raise ValueError("The reserved blank event setting cannot be deleted.")
        if self.get_active_selection()["context"] == slug:
            raise ValueError("Cannot delete the active event setting.")
        data = self._load_prompt_map("event_settings")
        data.pop(slug, None)
        self._write_prompt_map("event_settings", data)
        contexts = self._load_event_moment_contexts()
        contexts.pop(slug, None)
        self._write_event_moment_contexts(contexts)

    def list_event_moments(self, event_setting_id: int | str, include_archived: bool = False) -> list[dict]:
        event_slug = self._resolve_item_slug("event_settings", event_setting_id)
        if self._get_item("event_settings", event_slug) is None:
            raise LookupError("Event setting not found.")
        raw = self._load_event_moment_contexts().get(event_slug, {})
        items = [
            self._entry_to_item(
                section="event_moments",
                slug=slug,
                entry=entry,
                parent_slug=event_slug,
                order_index=index,
            )
            for index, (slug, entry) in enumerate(raw.items())
        ]
        if not any(item["slug"] == BLANK_EVENT_MOMENT_SLUG for item in items):
            items.insert(
                0,
                self._entry_to_item(
                    section="event_moments",
                    slug=BLANK_EVENT_MOMENT_SLUG,
                    entry={"title": BLANK_EVENT_MOMENT_TITLE, "prompt_text": "", "order_index": -1},
                    parent_slug=event_slug,
                    order_index=-1,
                ),
            )
        if not include_archived:
            items = [item for item in items if not item["is_archived"]]
        return sorted(items, key=lambda item: item.get("order_index", 0))

    def create_event_moment(self, *, event_setting_id: int | str, **payload) -> dict:
        event_slug = self._resolve_item_slug("event_settings", event_setting_id)
        slug = self._normalize_slug(payload.get("slug", ""))
        if slug == BLANK_EVENT_MOMENT_SLUG:
            raise ValueError("The reserved blank event moment is created automatically.")
        contexts = self._load_event_moment_contexts()
        moments = contexts.setdefault(event_slug, {})
        if slug in moments:
            raise ValueError(f"Event moment slug '{slug}' already exists for this event setting.")
        moments[slug] = self._item_to_entry(
            title=payload.get("title", ""),
            prompt_text=payload.get("prompt_text", ""),
            initial_greeting=payload.get("initial_greeting", ""),
            goodbye_text=payload.get("goodbye_text", ""),
            order_index=payload.get("order_index", 0),
        )
        self._write_event_moment_contexts(contexts)
        return self._get_item("event_moments", slug, parent_slug=event_slug) or {}

    def update_event_moment(self, event_moment_id: int | str, **payload) -> dict:
        ref_text = str(event_moment_id)
        contexts = self._load_event_moment_contexts()
        found_context = None
        old_slug = None
        for context_slug, moments in contexts.items():
            for slug in moments:
                item_id = stable_id(f"event_moments:{context_slug}:{slug}")
                if slug == ref_text or str(item_id) == ref_text:
                    found_context = context_slug
                    old_slug = slug
                    break
            if found_context:
                break
        if not found_context or not old_slug:
            raise LookupError("Event moment not found.")
        if old_slug == BLANK_EVENT_MOMENT_SLUG:
            raise ValueError("The reserved blank event moment cannot be updated.")

        new_slug = self._normalize_slug(payload.get("slug", ""))
        if new_slug == BLANK_EVENT_MOMENT_SLUG:
            raise ValueError("The reserved blank event moment slug cannot be used.")
        moments = contexts[found_context]
        if new_slug != old_slug and new_slug in moments:
            raise ValueError(f"Event moment slug '{new_slug}' already exists for this event setting.")
        moments.pop(old_slug)
        moments[new_slug] = self._item_to_entry(
            title=payload.get("title", ""),
            prompt_text=payload.get("prompt_text", ""),
            initial_greeting=payload.get("initial_greeting", ""),
            goodbye_text=payload.get("goodbye_text", ""),
            is_archived=bool(payload.get("is_archived", False)),
            order_index=payload.get("order_index", 0),
        )
        self._write_event_moment_contexts(contexts)
        return self._get_item("event_moments", new_slug, parent_slug=found_context) or {}

    def delete_event_moment(self, event_moment_id: int | str) -> None:
        ref_text = str(event_moment_id)
        active = self.get_active_selection()
        contexts = self._load_event_moment_contexts()
        for context_slug, moments in contexts.items():
            for slug in list(moments):
                item_id = stable_id(f"event_moments:{context_slug}:{slug}")
                if slug == ref_text or str(item_id) == ref_text:
                    if slug == BLANK_EVENT_MOMENT_SLUG:
                        raise ValueError("The reserved blank event moment cannot be deleted.")
                    if active["context"] == context_slug and active["phase"] == slug:
                        raise ValueError("Cannot delete the active event moment.")
                    moments.pop(slug, None)
                    self._write_event_moment_contexts(contexts)
                    return
        raise LookupError("Event moment not found.")

    def _create_item(self, section: str, payload: dict) -> dict:
        slug = self._normalize_slug(payload.get("slug", ""))
        data = self._load_prompt_map(section)
        if slug in data:
            raise ValueError(f"Prompt slug '{slug}' already exists.")
        data[slug] = self._item_to_entry(
            title=payload.get("title", ""),
            prompt_text=payload.get("prompt_text", ""),
            initial_greeting=payload.get("initial_greeting", ""),
            goodbye_text=payload.get("goodbye_text", ""),
        )
        self._write_prompt_map(section, data)
        return self._get_item(section, slug) or {}

    def _update_item(self, section: str, ref: int | str, payload: dict) -> dict:
        old_slug = self._resolve_item_slug(section, ref)
        new_slug = self._normalize_slug(payload.get("slug", ""))
        data = self._load_prompt_map(section)
        if new_slug != old_slug and new_slug in data:
            raise ValueError(f"Prompt slug '{new_slug}' already exists.")
        data.pop(old_slug, None)
        data[new_slug] = self._item_to_entry(
            title=payload.get("title", ""),
            prompt_text=payload.get("prompt_text", ""),
            initial_greeting=payload.get("initial_greeting", ""),
            goodbye_text=payload.get("goodbye_text", ""),
            is_archived=bool(payload.get("is_archived", False)),
        )
        self._write_prompt_map(section, data)
        active = self.get_active_selection()
        active_field = {
            "modes": "core_mode",
            "speaking_styles": "persona",
            "event_settings": "context",
        }.get(section)
        if active_field and active.get(active_field) == old_slug:
            active[active_field] = new_slug
            config = self._load_active_config()
            config["active"] = active
            self._write_active_config(config)
        return self._get_item(section, new_slug) or {}
