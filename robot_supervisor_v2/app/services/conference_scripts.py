from __future__ import annotations

import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import yaml

from robot_services.gestures import normalize_gesture

from ..models.conference import (
    ConferenceActiveEventResponse,
    ConferenceEventDeleteResponse,
    ConferenceEventDocument,
    ConferenceEventListResponse,
    ConferenceEventSummary,
    ConferenceProgram,
    ConferenceRuntimeSection,
    ConferenceScenario,
    ConferenceSection,
    ConferenceStep,
)

EVENT_MANIFEST_NAME = "event.yaml"
ACTIVE_EVENT_FILE_NAME = ".active_event"
LEGACY_EVENT_KEY = "__root__"

_SAFE_PATH_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._-]*$")
_SAFE_SCENARIO_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


@dataclass(frozen=True)
class EventSource:
    key: str
    path: Path
    manifest: dict[str, Any]
    summary: ConferenceEventSummary
    scripts: list[str]


class ConferenceScriptService:
    def __init__(self, scripts_dir: Path):
        self._scripts_dir = scripts_dir
        self._active_event_path = self._scripts_dir / ACTIVE_EVENT_FILE_NAME

    def list_events(self) -> ConferenceEventListResponse:
        sources = self._discover_event_sources()
        active_event_key = self._resolve_active_event_key(sources)
        if active_event_key:
            self._write_active_event_key(active_event_key)

        return ConferenceEventListResponse(
            active_event_key=active_event_key,
            events=[source.summary for source in sources],
        )

    def get_active_event(self) -> ConferenceActiveEventResponse:
        sources = self._discover_event_sources()
        source = self._resolve_event_source(sources)
        self._write_active_event_key(source.key)
        return ConferenceActiveEventResponse(
            active_event_key=source.key,
            active_event=source.summary,
        )

    def set_active_event(self, event_key: str) -> ConferenceActiveEventResponse:
        sources = self._discover_event_sources()
        source = self._resolve_event_source(sources, requested_key=event_key)
        self._write_active_event_key(source.key)
        return ConferenceActiveEventResponse(
            active_event_key=source.key,
            active_event=source.summary,
        )

    def load_program(self, event_key: str | None = None) -> ConferenceProgram:
        sources = self._discover_event_sources()
        source = self._resolve_event_source(sources, requested_key=event_key)
        self._write_active_event_key(source.key)

        event = source.manifest.get("event", {}) or {}
        robot = source.manifest.get("robot", {}) or {}

        return ConferenceProgram(
            event_key=source.key,
            source_dir=source.summary.source_dir,
            event={key: str(value) for key, value in event.items()},
            robot={key: str(value) for key, value in robot.items()},
            scripts=list(source.scripts),
            sections=[self._load_runtime_section(source, script_id) for script_id in source.scripts],
        )

    def get_event_document(self, event_key: str) -> ConferenceEventDocument:
        source = self._resolve_editor_event_source(event_key)
        return self._build_event_document(source)

    def create_event(
        self,
        document: ConferenceEventDocument,
        *,
        allowed_gestures: set[str] | None = None,
    ) -> ConferenceEventDocument:
        normalized = self._normalize_document(document, allowed_gestures=allowed_gestures)
        target_path = self._scripts_dir / normalized.key
        if target_path.exists():
            raise FileExistsError(f"Conference event '{normalized.key}' already exists")

        self._write_event_directory(target_path, normalized)
        return self.get_event_document(normalized.key)

    def update_event(
        self,
        event_key: str,
        document: ConferenceEventDocument,
        *,
        allowed_gestures: set[str] | None = None,
    ) -> ConferenceEventDocument:
        source = self._resolve_editor_event_source(event_key)
        normalized = self._normalize_document(document, allowed_gestures=allowed_gestures)
        target_path = self._scripts_dir / normalized.key

        if normalized.key != source.key and target_path.exists():
            raise FileExistsError(f"Conference event '{normalized.key}' already exists")

        if normalized.key != source.key:
            source.path.rename(target_path)

        self._write_event_directory(target_path, normalized)

        active_key = self._read_active_event_key()
        if active_key == source.key and normalized.key != source.key:
            self._write_active_event_key(normalized.key)

        return self.get_event_document(normalized.key)

    def delete_event(self, event_key: str) -> ConferenceEventDeleteResponse:
        source = self._resolve_editor_event_source(event_key)
        shutil.rmtree(source.path)

        sources = self._discover_event_sources()
        active_event_key = self._resolve_active_event_key(sources)
        if active_event_key:
            self._write_active_event_key(active_event_key)
        else:
            self._clear_active_event_key()

        return ConferenceEventDeleteResponse(
            deleted_key=event_key,
            active_event_key=active_event_key,
        )

    def _discover_event_sources(self) -> list[EventSource]:
        if not self._scripts_dir.exists():
            return []

        sources: list[EventSource] = []
        root_manifest_path = self._scripts_dir / EVENT_MANIFEST_NAME
        if root_manifest_path.exists():
            sources.append(
                self._build_event_source(
                    key=LEGACY_EVENT_KEY,
                    directory=self._scripts_dir,
                    source_dir=".",
                )
            )

        for child in sorted(self._scripts_dir.iterdir(), key=lambda item: item.name.lower()):
            if not child.is_dir() or child.name.startswith("."):
                continue

            manifest_path = child / EVENT_MANIFEST_NAME
            if manifest_path.exists():
                sources.append(
                    self._build_event_source(
                        key=child.name,
                        directory=child,
                        source_dir=child.name,
                    )
                )
                continue

            inferred_source = self._build_inferred_event_source(
                key=child.name,
                directory=child,
                source_dir=child.name,
            )
            if inferred_source:
                sources.append(inferred_source)

        return sources

    def _build_event_source(self, *, key: str, directory: Path, source_dir: str) -> EventSource:
        manifest_path = directory / EVENT_MANIFEST_NAME
        manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8")) or {}
        return self._build_event_source_from_manifest(
            key=key,
            directory=directory,
            source_dir=source_dir,
            manifest=manifest,
        )

    def _build_inferred_event_source(
        self,
        *,
        key: str,
        directory: Path,
        source_dir: str,
    ) -> EventSource | None:
        script_files = sorted(
            path for path in directory.glob("*.yaml")
            if path.is_file() and path.name != EVENT_MANIFEST_NAME
        )
        if not script_files:
            return None

        manifest = {
            "event": {
                "id": key,
                "name": directory.name,
            },
            "robot": {},
            "scripts": [path.stem for path in script_files],
        }

        return self._build_event_source_from_manifest(
            key=key,
            directory=directory,
            source_dir=source_dir,
            manifest=manifest,
        )

    def _build_event_source_from_manifest(
        self,
        *,
        key: str,
        directory: Path,
        source_dir: str,
        manifest: dict[str, Any],
    ) -> EventSource:
        event = manifest.get("event", {}) or {}
        robot = manifest.get("robot", {}) or {}
        scripts = self._normalize_script_ids(manifest.get("scripts"))
        event_id = str(event.get("id", "")).strip() or key
        name = str(event.get("name", "")).strip() or event_id
        location = str(event.get("location", "")).strip() or None
        language = str(event.get("language", "")).strip() or None
        robot_name = str(robot.get("name", "")).strip() or None

        return EventSource(
            key=key,
            path=directory,
            manifest=manifest,
            summary=ConferenceEventSummary(
                key=key,
                event_id=event_id,
                name=name,
                location=location,
                language=language,
                robot_name=robot_name,
                source_dir=source_dir,
            ),
            scripts=scripts,
        )

    def _resolve_active_event_key(self, sources: list[EventSource]) -> str | None:
        if not sources:
            return None

        available_keys = {source.key for source in sources}
        stored_key = self._read_active_event_key()
        if stored_key and stored_key in available_keys:
            return stored_key

        return sources[0].key

    def _resolve_event_source(
        self,
        sources: list[EventSource],
        *,
        requested_key: str | None = None,
    ) -> EventSource:
        if not sources:
            raise FileNotFoundError(f"No event programs found in {self._scripts_dir}")

        source_by_key = {source.key: source for source in sources}
        candidate_key = requested_key or self._resolve_active_event_key(sources)
        if candidate_key and candidate_key in source_by_key:
            return source_by_key[candidate_key]

        if requested_key:
            raise KeyError(f"Unknown event key: {requested_key}")

        return sources[0]

    def _resolve_editor_event_source(self, event_key: str) -> EventSource:
        if event_key == LEGACY_EVENT_KEY:
            raise KeyError("The legacy root conference event is read-only")

        sources = self._discover_event_sources()
        source = self._resolve_event_source(sources, requested_key=event_key)
        if source.key == LEGACY_EVENT_KEY:
            raise KeyError("The legacy root conference event is read-only")
        return source

    def _read_active_event_key(self) -> str | None:
        if not self._active_event_path.exists():
            return None

        value = self._active_event_path.read_text(encoding="utf-8").strip()
        return value or None

    def _write_active_event_key(self, event_key: str) -> None:
        self._scripts_dir.mkdir(parents=True, exist_ok=True)
        self._active_event_path.write_text(f"{event_key}\n", encoding="utf-8")

    def _clear_active_event_key(self) -> None:
        if self._active_event_path.exists():
            self._active_event_path.unlink()

    def _build_event_document(self, source: EventSource) -> ConferenceEventDocument:
        event = source.manifest.get("event", {}) or {}
        robot = source.manifest.get("robot", {}) or {}
        sections = [self._load_editor_section(source, script_id) for script_id in source.scripts]

        return ConferenceEventDocument(
            key=source.key,
            event_id=str(event.get("id", "")).strip() or source.key,
            name=str(event.get("name", "")).strip() or source.summary.name,
            location=str(event.get("location", "")).strip() or None,
            language=str(event.get("language", "")).strip() or None,
            robot_name=str(robot.get("name", "")).strip() or None,
            robot_role=str(robot.get("role", "")).strip() or None,
            sections=sections,
        )

    def _load_runtime_section(self, source: EventSource, script_id: str) -> ConferenceRuntimeSection:
        path = source.path / f"{script_id}.yaml"
        source_file = self._format_source_file(source.summary.source_dir, path.name)
        if not path.exists():
            return ConferenceRuntimeSection(
                id=script_id,
                section=script_id,
                description=None,
                scenarios=[],
                status="missing",
                source_file=source_file,
            )

        raw = path.read_text(encoding="utf-8").strip()
        if not raw:
            return ConferenceRuntimeSection(
                id=script_id,
                section=script_id,
                description=None,
                scenarios=[],
                status="empty",
                source_file=source_file,
            )

        data = yaml.safe_load(raw) or {}
        section = self._parse_section_data(script_id, data)
        return ConferenceRuntimeSection(
            id=section.id,
            section=section.section,
            description=section.description,
            scenarios=section.scenarios,
            status="ready",
            source_file=source_file,
        )

    def _load_editor_section(self, source: EventSource, script_id: str) -> ConferenceSection:
        path = source.path / f"{script_id}.yaml"
        if not path.exists():
            return ConferenceSection(
                id=script_id,
                section=script_id,
                description=None,
                scenarios=[],
            )

        raw = path.read_text(encoding="utf-8").strip()
        if not raw:
            return ConferenceSection(
                id=script_id,
                section=script_id,
                description=None,
                scenarios=[],
            )

        data = yaml.safe_load(raw) or {}
        return self._parse_section_data(script_id, data)

    def _parse_section_data(self, section_id: str, data: dict[str, Any]) -> ConferenceSection:
        return ConferenceSection(
            id=section_id,
            section=str(data.get("section", "")).strip() or section_id,
            description=str(data.get("description", "")).strip() or None,
            scenarios=self._load_scenarios(data.get("scenarios")),
        )

    def _load_scenarios(self, raw_scenarios: Any) -> list[ConferenceScenario]:
        if isinstance(raw_scenarios, dict):
            items: Iterable[tuple[str, Any]] = raw_scenarios.items()
        elif isinstance(raw_scenarios, list):
            items = [
                (
                    str((raw_scenario or {}).get("id", "")).strip() or f"scenario_{index + 1}",
                    raw_scenario,
                )
                for index, raw_scenario in enumerate(raw_scenarios)
                if isinstance(raw_scenario, dict)
            ]
        else:
            return []

        scenarios: list[ConferenceScenario] = []
        for scenario_id, scenario_data in items:
            scenario_data = scenario_data or {}
            scenarios.append(
                ConferenceScenario(
                    id=scenario_id,
                    label=str(scenario_data.get("label", "")).strip() or scenario_id,
                    summary_text=str(scenario_data.get("summary_text", "")).strip() or None,
                    single_action_only=bool(scenario_data.get("single_action_only", False)),
                    action_label=str(scenario_data.get("action_label", "")).strip() or None,
                    steps=self._load_steps(
                        scenario_data.get("steps", scenario_data.get("lines", []))
                    ),
                )
            )
        return scenarios

    def _load_steps(self, raw_steps: Any) -> list[ConferenceStep]:
        if not isinstance(raw_steps, list):
            return []

        steps: list[ConferenceStep] = []
        for raw_step in raw_steps:
            if isinstance(raw_step, str):
                text = raw_step.strip()
                if text:
                    steps.append(ConferenceStep(text=text))
                continue

            if not isinstance(raw_step, dict):
                continue

            text = str(raw_step.get("text", "")).strip()
            gesture = str(raw_step.get("gesture", "")).strip() or None
            display_text = str(raw_step.get("display_text", "")).strip() or None
            note = str(raw_step.get("note", "")).strip() or None
            pause_after_ms = self._parse_pause_after_ms(raw_step.get("pause_after_ms"))
            if not text and not gesture and pause_after_ms is None:
                continue

            steps.append(
                ConferenceStep(
                    text=text,
                    display_text=display_text,
                    gesture=gesture,
                    pause_after_ms=pause_after_ms,
                    note=note,
                )
            )

        return steps

    def _normalize_document(
        self,
        document: ConferenceEventDocument,
        *,
        allowed_gestures: set[str] | None = None,
    ) -> ConferenceEventDocument:
        key = self._validate_path_name(document.key, label="Event key")
        event_id = str(document.event_id).strip() or key
        name = str(document.name).strip() or event_id
        location = str(document.location or "").strip() or None
        language = str(document.language or "").strip() or None
        robot_name = str(document.robot_name or "").strip() or None
        robot_role = str(document.robot_role or "").strip() or None

        sections: list[ConferenceSection] = []
        seen_section_ids: set[str] = set()

        for raw_section in document.sections:
            section_id = self._validate_path_name(raw_section.id, label="Section id")
            lower_section_id = section_id.lower()
            if lower_section_id in seen_section_ids:
                raise ValueError(f"Duplicate section id '{section_id}'")
            seen_section_ids.add(lower_section_id)

            section_title = str(raw_section.section).strip() or section_id
            description = str(raw_section.description or "").strip() or None

            scenarios: list[ConferenceScenario] = []
            seen_scenario_ids: set[str] = set()
            for raw_scenario in raw_section.scenarios:
                scenario_id = self._validate_scenario_id(raw_scenario.id)
                if scenario_id in seen_scenario_ids:
                    raise ValueError(
                        f"Duplicate scenario id '{scenario_id}' in section '{section_id}'"
                    )
                seen_scenario_ids.add(scenario_id)

                label = str(raw_scenario.label).strip() or scenario_id
                summary_text = str(raw_scenario.summary_text or "").strip() or None
                single_action_only = bool(raw_scenario.single_action_only)
                action_label = str(raw_scenario.action_label or "").strip() or None

                steps: list[ConferenceStep] = []
                for index, raw_step in enumerate(raw_scenario.steps, start=1):
                    text = str(raw_step.text or "").strip()
                    display_text = str(raw_step.display_text or "").strip() or None
                    note = str(raw_step.note or "").strip() or None
                    pause_after_ms = self._validate_pause_after_ms(
                        raw_step.pause_after_ms,
                        section_id=section_id,
                        scenario_id=scenario_id,
                        step_index=index,
                    )

                    gesture = str(raw_step.gesture or "").strip() or None
                    if gesture:
                        normalized_gesture = normalize_gesture(gesture)
                        if normalized_gesture is None:
                            raise ValueError(
                                f"Unknown gesture '{gesture}' in section '{section_id}', scenario '{scenario_id}'"
                            )
                        if allowed_gestures is not None and normalized_gesture not in allowed_gestures:
                            raise ValueError(
                                f"Gesture '{normalized_gesture}' is not available in the current gesture pool"
                            )
                        gesture = normalized_gesture

                    if not text and not gesture and pause_after_ms is None:
                        raise ValueError(
                            f"Step {index} in section '{section_id}', scenario '{scenario_id}' must include speech, gesture, or wait"
                        )

                    steps.append(
                        ConferenceStep(
                            text=text,
                            display_text=display_text,
                            gesture=gesture,
                            pause_after_ms=pause_after_ms,
                            note=note,
                        )
                    )

                scenarios.append(
                    ConferenceScenario(
                        id=scenario_id,
                        label=label,
                        summary_text=summary_text,
                        single_action_only=single_action_only,
                        action_label=action_label,
                        steps=steps,
                    )
                )

            sections.append(
                ConferenceSection(
                    id=section_id,
                    section=section_title,
                    description=description,
                    scenarios=scenarios,
                )
            )

        return ConferenceEventDocument(
            key=key,
            event_id=event_id,
            name=name,
            location=location,
            language=language,
            robot_name=robot_name,
            robot_role=robot_role,
            sections=sections,
        )

    def _write_event_directory(self, directory: Path, document: ConferenceEventDocument) -> None:
        directory.mkdir(parents=True, exist_ok=True)

        manifest = {
            "event": {
                "id": document.event_id,
                "name": document.name,
                "location": document.location,
                "language": document.language,
            },
            "robot": {
                "name": document.robot_name,
                "role": document.robot_role,
            },
            "scripts": [section.id for section in document.sections],
        }
        (directory / EVENT_MANIFEST_NAME).write_text(
            self._dump_yaml(self._remove_none_values(manifest)),
            encoding="utf-8",
        )

        existing_section_paths = {
            path.name: path
            for path in directory.glob("*.yaml")
            if path.is_file() and path.name != EVENT_MANIFEST_NAME
        }
        desired_section_file_names = {f"{section.id}.yaml" for section in document.sections}

        for file_name, path in existing_section_paths.items():
            if file_name not in desired_section_file_names:
                path.unlink()

        for section in document.sections:
            payload = {
                "section": section.section,
                "description": section.description,
                "scenarios": [self._scenario_to_yaml_dict(scenario) for scenario in section.scenarios],
            }
            (directory / f"{section.id}.yaml").write_text(
                self._dump_yaml(self._remove_none_values(payload)),
                encoding="utf-8",
            )

    def _scenario_to_yaml_dict(self, scenario: ConferenceScenario) -> dict[str, Any]:
        return self._remove_none_values(
            {
                "id": scenario.id,
                "label": scenario.label,
                "summary_text": scenario.summary_text,
                "single_action_only": scenario.single_action_only,
                "action_label": scenario.action_label,
                "steps": [self._step_to_yaml_dict(step) for step in scenario.steps],
            }
        )

    def _step_to_yaml_dict(self, step: ConferenceStep) -> dict[str, Any]:
        return self._remove_none_values(
            {
                "text": step.text or None,
                "display_text": step.display_text,
                "gesture": step.gesture,
                "pause_after_ms": step.pause_after_ms,
                "note": step.note,
            }
        )

    @staticmethod
    def _normalize_script_ids(raw_scripts: Any) -> list[str]:
        if not isinstance(raw_scripts, list):
            return []
        return [str(script_id).strip() for script_id in raw_scripts if str(script_id).strip()]

    @staticmethod
    def _validate_path_name(value: str, *, label: str) -> str:
        normalized = str(value).strip()
        if not normalized:
            raise ValueError(f"{label} is required")
        if normalized in {".", ".."}:
            raise ValueError(f"{label} cannot be '.' or '..'")
        if "/" in normalized or "\\" in normalized:
            raise ValueError(f"{label} cannot contain path separators")
        if normalized.startswith("."):
            raise ValueError(f"{label} cannot start with '.'")
        if not _SAFE_PATH_NAME_RE.fullmatch(normalized):
            raise ValueError(
                f"{label} may only contain letters, numbers, spaces, dots, dashes, and underscores"
            )
        return normalized

    @staticmethod
    def _validate_scenario_id(value: str) -> str:
        normalized = str(value).strip()
        if not normalized:
            raise ValueError("Scenario id is required")
        if not _SAFE_SCENARIO_ID_RE.fullmatch(normalized):
            raise ValueError(
                "Scenario id may only contain letters, numbers, dots, dashes, and underscores"
            )
        return normalized

    @staticmethod
    def _validate_pause_after_ms(
        value: Any,
        *,
        section_id: str,
        scenario_id: str,
        step_index: int,
    ) -> int | None:
        if value is None:
            return None
        try:
            pause_after_ms = int(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"Wait after step must be an integer in section '{section_id}', scenario '{scenario_id}', step {step_index}"
            ) from exc
        if pause_after_ms <= 0:
            raise ValueError(
                f"Wait after step must be greater than zero in section '{section_id}', scenario '{scenario_id}', step {step_index}"
            )
        return pause_after_ms

    @staticmethod
    def _parse_pause_after_ms(value: Any) -> int | None:
        if value is None:
            return None

        try:
            pause_after_ms = int(value)
        except (TypeError, ValueError):
            return None

        return pause_after_ms if pause_after_ms > 0 else None

    @staticmethod
    def _format_source_file(source_dir: str, file_name: str) -> str:
        if source_dir in {"", "."}:
            return file_name
        return f"{source_dir}/{file_name}"

    @staticmethod
    def _remove_none_values(value: Any) -> Any:
        if isinstance(value, dict):
            return {
                key: ConferenceScriptService._remove_none_values(child)
                for key, child in value.items()
                if child is not None
            }
        if isinstance(value, list):
            return [ConferenceScriptService._remove_none_values(child) for child in value]
        return value

    @staticmethod
    def _dump_yaml(value: Any) -> str:
        return yaml.safe_dump(
            value,
            allow_unicode=True,
            sort_keys=False,
            width=4096,
        )
