from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..file_store import (
    REPO_ROOT,
    load_yaml,
    read_json,
    stable_id,
    write_json_atomic,
    write_yaml_atomic,
)


SURVEYS_PATH = REPO_ROOT / "livekit_config" / "surveys.yaml"
SURVEYS_EXAMPLE_PATH = REPO_ROOT / "livekit_config" / "surveys.example.yaml"
SURVEY_RUNS_DIR = REPO_ROOT / "robot_supervisor_v2" / "state" / "survey_runs"

VALID_RESPONSE_KINDS = {"single_choice", "multiple_choice", "free_text", "ranking"}
OPTION_BASED_RESPONSE_KINDS = {"single_choice", "multiple_choice", "ranking"}


class SurveyService:
    def __init__(self, *, include_examples: bool = True):
        self.include_examples = include_examples

    def _load_catalog(self) -> dict[str, Any]:
        fallback_path = SURVEYS_EXAMPLE_PATH if self.include_examples else None
        payload = load_yaml(SURVEYS_PATH, fallback_path, {}) or {}
        if not isinstance(payload, dict):
            return {"active": "", "surveys": []}
        payload.setdefault("active", "")
        payload.setdefault("surveys", [])
        if not isinstance(payload["surveys"], list):
            payload["surveys"] = []
        return payload

    def _write_catalog(self, payload: dict[str, Any]) -> None:
        write_yaml_atomic(SURVEYS_PATH, payload)

    def _survey_id(self, slug: str) -> int:
        return stable_id(f"survey:{slug}")

    def _question_id(self, survey_slug: str, question_key: str) -> int:
        return stable_id(f"survey:{survey_slug}:question:{question_key}")

    def _serialize_survey(self, survey: dict) -> dict:
        slug = str(survey.get("slug") or "")
        questions = survey.get("questions") if isinstance(survey.get("questions"), list) else []
        return {
            "id": self._survey_id(slug),
            "slug": slug,
            "title": str(survey.get("title") or slug),
            "description": str(survey.get("description") or ""),
            "question_count": len(questions),
            "created_at": None,
            "updated_at": None,
        }

    def _serialize_question(self, survey_slug: str, question: dict) -> dict:
        key = str(question.get("question_key") or question.get("id") or "")
        return {
            "id": self._question_id(survey_slug, key),
            "survey_id": self._survey_id(survey_slug),
            "survey_slug": survey_slug,
            "question_key": key,
            "question": str(question.get("question") or ""),
            "response_kind": str(question.get("response_kind") or ""),
            "options": list(question.get("options") or []),
            "allow_skip": bool(question.get("allow_skip", True)),
            "order_index": int(question.get("order_index") or 0),
            "created_at": None,
            "updated_at": None,
        }

    def _normalize_required_text(self, value: str, field_name: str) -> str:
        normalized = (value or "").strip()
        if not normalized:
            raise ValueError(f"{field_name} is required.")
        return normalized

    def _normalize_optional_text(self, value: str | None) -> str:
        return (value or "").strip()

    def _normalize_slug(self, slug: str) -> str:
        return self._normalize_required_text(slug, "Slug")

    def _normalize_title(self, title: str) -> str:
        return self._normalize_required_text(title, "Title")

    def _normalize_question_key(self, question_key: str) -> str:
        return self._normalize_required_text(question_key, "Question key")

    def _normalize_response_kind(self, response_kind: str) -> str:
        normalized = self._normalize_required_text(response_kind, "Response kind")
        if normalized not in VALID_RESPONSE_KINDS:
            allowed = ", ".join(sorted(VALID_RESPONSE_KINDS))
            raise ValueError(f"Unsupported response_kind '{normalized}'. Expected one of: {allowed}.")
        return normalized

    def _normalize_options(self, response_kind: str, options: list[str] | None) -> list[str]:
        normalized = [(option or "").strip() for option in (options or [])]
        normalized = [option for option in normalized if option]
        if response_kind == "free_text":
            return []
        if len(normalized) < 2:
            raise ValueError("At least two options are required for this question type.")
        if len(set(normalized)) != len(normalized):
            raise ValueError("Options must be unique.")
        return normalized

    def _normalize_bool(self, value: bool, field_name: str) -> bool:
        if isinstance(value, bool):
            return value
        raise ValueError(f"{field_name} must be a boolean.")

    def _normalize_order_index(self, value: int) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            raise ValueError("order_index must be an integer.")

    def _find_survey(self, catalog: dict, ref: int | str) -> tuple[int, dict]:
        ref_text = str(ref)
        for index, survey in enumerate(catalog.get("surveys", [])):
            slug = str(survey.get("slug") or "")
            if slug == ref_text or str(self._survey_id(slug)) == ref_text:
                return index, survey
        raise LookupError("Survey not found.")

    def _find_question(self, catalog: dict, ref: int | str) -> tuple[int, dict, int, dict]:
        ref_text = str(ref)
        for survey_index, survey in enumerate(catalog.get("surveys", [])):
            survey_slug = str(survey.get("slug") or "")
            for question_index, question in enumerate(survey.get("questions") or []):
                key = str(question.get("question_key") or question.get("id") or "")
                if key == ref_text or str(self._question_id(survey_slug, key)) == ref_text:
                    return survey_index, survey, question_index, question
        raise LookupError("Question not found.")

    def list_surveys(self) -> list[dict]:
        return [self._serialize_survey(item) for item in self._load_catalog().get("surveys", [])]

    def create_survey(self, *, slug: str, title: str, description: str = "") -> dict:
        catalog = self._load_catalog()
        slug = self._normalize_slug(slug)
        if any(str(item.get("slug") or "") == slug for item in catalog["surveys"]):
            raise ValueError(f"Survey slug '{slug}' already exists.")
        survey = {
            "slug": slug,
            "title": self._normalize_title(title),
            "description": self._normalize_optional_text(description),
            "questions": [],
        }
        catalog["surveys"].append(survey)
        catalog["active"] = catalog.get("active") or slug
        self._write_catalog(catalog)
        return self._serialize_survey(survey)

    def update_survey(self, survey_id: int | str, *, slug: str, title: str, description: str = "") -> dict:
        catalog = self._load_catalog()
        index, survey = self._find_survey(catalog, survey_id)
        old_slug = str(survey.get("slug") or "")
        slug = self._normalize_slug(slug)
        if slug != old_slug and any(str(item.get("slug") or "") == slug for item in catalog["surveys"]):
            raise ValueError(f"Survey slug '{slug}' already exists.")
        survey.update(
            {
                "slug": slug,
                "title": self._normalize_title(title),
                "description": self._normalize_optional_text(description),
                "questions": survey.get("questions") or [],
            }
        )
        catalog["surveys"][index] = survey
        if catalog.get("active") == old_slug:
            catalog["active"] = slug
        self._write_catalog(catalog)
        return self._serialize_survey(survey)

    def delete_survey(self, survey_id: int | str) -> None:
        catalog = self._load_catalog()
        index, survey = self._find_survey(catalog, survey_id)
        if catalog.get("active") == survey.get("slug"):
            raise ValueError("Cannot delete the active survey.")
        catalog["surveys"].pop(index)
        self._write_catalog(catalog)

    def list_questions(self, survey_id: int | str) -> list[dict]:
        _, survey = self._find_survey(self._load_catalog(), survey_id)
        survey_slug = str(survey.get("slug") or "")
        return [
            self._serialize_question(survey_slug, item)
            for item in sorted(survey.get("questions") or [], key=lambda q: int(q.get("order_index") or 0))
        ]

    def create_question(
        self,
        *,
        survey_id: int | str,
        question_key: str,
        question: str,
        response_kind: str,
        options: list[str] | None = None,
        allow_skip: bool = True,
        order_index: int = 0,
    ) -> dict:
        catalog = self._load_catalog()
        survey_index, survey = self._find_survey(catalog, survey_id)
        question_key = self._normalize_question_key(question_key)
        questions = survey.setdefault("questions", [])
        if any(str(item.get("question_key") or item.get("id") or "") == question_key for item in questions):
            raise ValueError(f"Question key '{question_key}' already exists for this survey.")
        item = self._question_payload(
            question_key=question_key,
            question=question,
            response_kind=response_kind,
            options=options,
            allow_skip=allow_skip,
            order_index=order_index,
        )
        questions.append(item)
        catalog["surveys"][survey_index] = survey
        self._write_catalog(catalog)
        return self._serialize_question(str(survey.get("slug") or ""), item)

    def update_question(
        self,
        question_id: int | str,
        *,
        question_key: str,
        question: str,
        response_kind: str,
        options: list[str] | None = None,
        allow_skip: bool = True,
        order_index: int = 0,
    ) -> dict:
        catalog = self._load_catalog()
        survey_index, survey, question_index, _existing = self._find_question(catalog, question_id)
        question_key = self._normalize_question_key(question_key)
        questions = survey.setdefault("questions", [])
        for idx, item in enumerate(questions):
            key = str(item.get("question_key") or item.get("id") or "")
            if idx != question_index and key == question_key:
                raise ValueError(f"Question key '{question_key}' already exists for this survey.")
        item = self._question_payload(
            question_key=question_key,
            question=question,
            response_kind=response_kind,
            options=options,
            allow_skip=allow_skip,
            order_index=order_index,
        )
        questions[question_index] = item
        catalog["surveys"][survey_index] = survey
        self._write_catalog(catalog)
        return self._serialize_question(str(survey.get("slug") or ""), item)

    def delete_question(self, question_id: int | str) -> None:
        catalog = self._load_catalog()
        survey_index, survey, question_index, _question = self._find_question(catalog, question_id)
        survey.setdefault("questions", []).pop(question_index)
        catalog["surveys"][survey_index] = survey
        self._write_catalog(catalog)

    def get_runtime_survey(self, slug: str) -> dict:
        _, survey = self._find_survey(self._load_catalog(), slug)
        return {
            "survey_id": survey["slug"],
            "title": str(survey.get("title") or survey["slug"]),
            "description": str(survey.get("description") or ""),
            "questions": [
                {
                    "id": str(q.get("question_key") or q.get("id") or ""),
                    "question": str(q.get("question") or ""),
                    "response_kind": str(q.get("response_kind") or ""),
                    "options": list(q.get("options") or []),
                    "allow_skip": bool(q.get("allow_skip", True)),
                }
                for q in sorted(survey.get("questions") or [], key=lambda item: int(item.get("order_index") or 0))
            ],
        }

    def get_active_selection(self) -> dict[str, str]:
        catalog = self._load_catalog()
        active_slug = str(catalog.get("active") or "")
        if not active_slug and catalog["surveys"]:
            active_slug = str(catalog["surveys"][0].get("slug") or "")
        if not active_slug:
            return {}
        try:
            _, survey = self._find_survey(catalog, active_slug)
        except LookupError:
            return {}
        return {"survey_slug": survey["slug"], "survey_title": survey.get("title") or survey["slug"]}

    def set_active_selection(self, *, survey_slug: str) -> dict[str, str]:
        catalog = self._load_catalog()
        survey_slug = self._normalize_slug(survey_slug)
        _, survey = self._find_survey(catalog, survey_slug)
        catalog["active"] = survey["slug"]
        self._write_catalog(catalog)
        return {"survey_slug": survey["slug"], "survey_title": survey.get("title") or survey["slug"]}

    def get_available_options(self) -> dict:
        return {
            "surveys": [
                {
                    **self._serialize_survey(survey),
                    "question_count": len(survey.get("questions") or []),
                }
                for survey in self._load_catalog().get("surveys", [])
            ]
        }

    def archive_snapshot(self, snapshot: dict, *, source_updated_at: float | None = None) -> dict:
        if snapshot.get("phase") != "finished":
            raise ValueError("Only finished survey snapshots can be archived.")
        survey_slug = self._normalize_slug(str(snapshot.get("survey_id") or ""))
        snapshot_hash = self._snapshot_hash(snapshot)
        started_at = str(snapshot.get("started_at") or "")
        existing = self._find_run_by_started_at(survey_slug, started_at) if started_at else self._find_run_by_hash(snapshot_hash)
        if existing is not None:
            payload = self._run_summary(existing)
            payload["archived"] = False
            return payload

        survey_title = survey_slug
        try:
            _, survey = self._find_survey(self._load_catalog(), survey_slug)
            survey_title = str(survey.get("title") or survey_slug)
        except LookupError:
            pass
        run_id = self._next_run_id()
        created_at = datetime.now(timezone.utc).isoformat()
        source_updated_at_text = (
            datetime.fromtimestamp(source_updated_at, timezone.utc).isoformat()
            if source_updated_at
            else None
        )
        questions = snapshot.get("questions") or []
        run = {
            "id": run_id,
            "survey_slug": survey_slug,
            "survey_title": survey_title,
            "phase": str(snapshot.get("phase") or ""),
            "responses_recorded": int(snapshot.get("responses_recorded") or 0),
            "total_questions": int(snapshot.get("total_questions") or len(questions)),
            "snapshot_hash": snapshot_hash,
            "snapshot": snapshot,
            "answers": self._snapshot_to_answers(snapshot),
            "source_updated_at": source_updated_at_text,
            "started_at": started_at or None,
            "created_at": created_at,
        }
        write_json_atomic(self._run_path(run_id), run)
        payload = self._run_summary(run)
        payload["archived"] = True
        return payload

    def list_runs(self, *, survey_slug: str | None = None) -> list[dict]:
        return [self._run_summary(run) for run in self._load_runs(survey_slug=survey_slug)]

    def get_run_snapshot(self, run_id: int) -> dict:
        run = self._load_run(run_id)
        return run["snapshot"]

    def get_run_snapshots(self, *, survey_slug: str | None = None) -> list[dict]:
        return [{**self._run_summary(run), "snapshot": run["snapshot"]} for run in self._load_runs(survey_slug=survey_slug)]

    def get_run_detail(self, run_id: int) -> dict:
        run = self._load_run(run_id)
        return {**self._run_summary(run), "snapshot": run["snapshot"], "answers": run.get("answers", [])}

    def delete_run(self, run_id: int) -> None:
        path = self._run_path(run_id)
        if not path.exists():
            raise LookupError("Survey run not found.")
        path.unlink()

    def delete_runs(self, *, survey_slug: str | None = None) -> int:
        deleted = 0
        for run in self._load_runs(survey_slug=survey_slug):
            path = self._run_path(int(run["id"]))
            if path.exists():
                path.unlink()
                deleted += 1
        return deleted

    def _question_payload(
        self,
        *,
        question_key: str,
        question: str,
        response_kind: str,
        options: list[str] | None = None,
        allow_skip: bool = True,
        order_index: int = 0,
    ) -> dict:
        response_kind = self._normalize_response_kind(response_kind)
        return {
            "question_key": question_key,
            "question": self._normalize_required_text(question, "Question"),
            "response_kind": response_kind,
            "options": self._normalize_options(response_kind, options),
            "allow_skip": self._normalize_bool(allow_skip, "allow_skip"),
            "order_index": self._normalize_order_index(order_index),
        }

    def _snapshot_hash(self, snapshot: dict) -> str:
        canonical = json.dumps(snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def _snapshot_to_answers(self, snapshot: dict) -> list[dict]:
        answers = []
        for index, question in enumerate(snapshot.get("questions") or []):
            response = question.get("response") or {}
            answers.append(
                {
                    "question_id": str(question.get("id") or ""),
                    "question": str(question.get("question") or ""),
                    "response_kind": str(question.get("response_kind") or ""),
                    "options": list(question.get("options") or []),
                    "allow_skip": bool(question.get("allow_skip", True)),
                    "raw_input": str(response.get("raw_input") or ""),
                    "selected_options": list(response.get("selected_options") or []),
                    "free_text": str(response.get("free_text") or ""),
                    "ranking": list(response.get("ranking") or []),
                    "order_index": index,
                }
            )
        return answers

    def _run_path(self, run_id: int) -> Path:
        return SURVEY_RUNS_DIR / f"{int(run_id):06d}.json"

    def _next_run_id(self) -> int:
        runs = self._load_runs()
        return max([int(run["id"]) for run in runs], default=0) + 1

    def _load_run(self, run_id: int) -> dict:
        payload = read_json(self._run_path(run_id), None)
        if not isinstance(payload, dict):
            raise LookupError("Survey run not found.")
        return payload

    def _load_runs(self, *, survey_slug: str | None = None) -> list[dict]:
        if not SURVEY_RUNS_DIR.exists():
            return []
        runs = []
        for path in sorted(SURVEY_RUNS_DIR.glob("*.json")):
            payload = read_json(path, None)
            if isinstance(payload, dict) and (survey_slug is None or payload.get("survey_slug") == survey_slug):
                runs.append(payload)
        return sorted(runs, key=lambda run: str(run.get("created_at") or ""), reverse=True)

    def _find_run_by_hash(self, snapshot_hash: str) -> dict | None:
        for run in self._load_runs():
            if run.get("snapshot_hash") == snapshot_hash:
                return run
        return None

    def _find_run_by_started_at(self, survey_slug: str, started_at: str) -> dict | None:
        for run in self._load_runs(survey_slug=survey_slug):
            if run.get("started_at") == started_at:
                return run
        return None

    def _run_summary(self, run: dict) -> dict:
        return {
            "id": int(run.get("id") or 0),
            "survey_slug": str(run.get("survey_slug") or ""),
            "survey_title": str(run.get("survey_title") or ""),
            "phase": str(run.get("phase") or ""),
            "responses_recorded": int(run.get("responses_recorded") or 0),
            "total_questions": int(run.get("total_questions") or 0),
            "snapshot_hash": str(run.get("snapshot_hash") or ""),
            "source_updated_at": run.get("source_updated_at"),
            "started_at": run.get("started_at"),
            "created_at": run.get("created_at"),
        }
