from __future__ import annotations

from typing import Any

from ..file_store import REPO_ROOT, load_yaml, stable_id, write_yaml_atomic


QUIZZES_PATH = REPO_ROOT / "livekit_config" / "quizzes.yaml"
QUIZZES_EXAMPLE_PATH = REPO_ROOT / "livekit_config" / "quizzes.example.yaml"


class QuizService:
    def __init__(self, *, include_examples: bool = True):
        self.include_examples = include_examples

    def _load_catalog(self) -> dict[str, Any]:
        fallback_path = QUIZZES_EXAMPLE_PATH if self.include_examples else None
        payload = load_yaml(QUIZZES_PATH, fallback_path, {}) or {}
        if not isinstance(payload, dict):
            return {"active": "", "quizzes": []}
        payload.setdefault("active", "")
        payload.setdefault("quizzes", [])
        if not isinstance(payload["quizzes"], list):
            payload["quizzes"] = []
        return payload

    def _write_catalog(self, payload: dict[str, Any]) -> None:
        write_yaml_atomic(QUIZZES_PATH, payload)

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

    def _normalize_options(self, options: list[str]) -> list[str]:
        normalized = [(option or "").strip() for option in options]
        normalized = [option for option in normalized if option]
        if len(normalized) < 2:
            raise ValueError("At least two options are required.")
        if len(set(normalized)) != len(normalized):
            raise ValueError("Options must be unique.")
        return normalized

    def _normalize_correct_answer(self, correct_answer: str, options: list[str]) -> str:
        normalized = self._normalize_required_text(correct_answer, "Correct answer")
        if normalized not in options:
            raise ValueError("Correct answer must match one of the options exactly.")
        return normalized

    def _normalize_max_attempts(self, value: int) -> int:
        try:
            attempts = int(value)
        except (TypeError, ValueError):
            raise ValueError("max_attempts must be an integer.")
        if attempts < 1:
            raise ValueError("max_attempts must be at least 1.")
        return attempts

    def _quiz_id(self, slug: str) -> int:
        return stable_id(f"quiz:{slug}")

    def _question_id(self, quiz_slug: str, question_key: str) -> int:
        return stable_id(f"quiz:{quiz_slug}:question:{question_key}")

    def _serialize_quiz(self, quiz: dict) -> dict:
        slug = str(quiz.get("slug") or "")
        questions = quiz.get("questions") if isinstance(quiz.get("questions"), list) else []
        return {
            "id": self._quiz_id(slug),
            "slug": slug,
            "title": str(quiz.get("title") or slug),
            "description": str(quiz.get("description") or ""),
            "max_attempts": int(quiz.get("max_attempts") or 2),
            "question_count": len(questions),
            "created_at": None,
            "updated_at": None,
        }

    def _serialize_question(self, quiz_slug: str, question: dict) -> dict:
        key = str(question.get("question_key") or question.get("id") or "")
        return {
            "id": self._question_id(quiz_slug, key),
            "quiz_id": self._quiz_id(quiz_slug),
            "quiz_slug": quiz_slug,
            "question_key": key,
            "question": str(question.get("question") or ""),
            "options": list(question.get("options") or []),
            "correct_answer": str(question.get("correct_answer") or ""),
            "hint": str(question.get("hint") or ""),
            "explanation": str(question.get("explanation") or ""),
            "order_index": int(question.get("order_index") or 0),
            "created_at": None,
            "updated_at": None,
        }

    def _find_quiz(self, catalog: dict, ref: int | str) -> tuple[int, dict]:
        ref_text = str(ref)
        for index, quiz in enumerate(catalog.get("quizzes", [])):
            slug = str(quiz.get("slug") or "")
            if slug == ref_text or str(self._quiz_id(slug)) == ref_text:
                return index, quiz
        raise LookupError("Quiz not found.")

    def _find_question(self, catalog: dict, ref: int | str) -> tuple[int, dict, int, dict]:
        ref_text = str(ref)
        for quiz_index, quiz in enumerate(catalog.get("quizzes", [])):
            quiz_slug = str(quiz.get("slug") or "")
            for question_index, question in enumerate(quiz.get("questions") or []):
                key = str(question.get("question_key") or question.get("id") or "")
                if key == ref_text or str(self._question_id(quiz_slug, key)) == ref_text:
                    return quiz_index, quiz, question_index, question
        raise LookupError("Question not found.")

    def list_quizzes(self) -> list[dict]:
        return [self._serialize_quiz(item) for item in self._load_catalog().get("quizzes", [])]

    def create_quiz(self, *, slug: str, title: str, description: str = "", max_attempts: int = 2) -> dict:
        catalog = self._load_catalog()
        slug = self._normalize_slug(slug)
        if any(str(item.get("slug") or "") == slug for item in catalog["quizzes"]):
            raise ValueError(f"Quiz slug '{slug}' already exists.")
        quiz = {
            "slug": slug,
            "title": self._normalize_title(title),
            "description": self._normalize_optional_text(description),
            "max_attempts": self._normalize_max_attempts(max_attempts),
            "questions": [],
        }
        catalog["quizzes"].append(quiz)
        catalog["active"] = catalog.get("active") or slug
        self._write_catalog(catalog)
        return self._serialize_quiz(quiz)

    def update_quiz(self, quiz_id: int | str, *, slug: str, title: str, description: str = "", max_attempts: int = 2) -> dict:
        catalog = self._load_catalog()
        index, quiz = self._find_quiz(catalog, quiz_id)
        old_slug = str(quiz.get("slug") or "")
        slug = self._normalize_slug(slug)
        if slug != old_slug and any(str(item.get("slug") or "") == slug for item in catalog["quizzes"]):
            raise ValueError(f"Quiz slug '{slug}' already exists.")
        quiz.update(
            {
                "slug": slug,
                "title": self._normalize_title(title),
                "description": self._normalize_optional_text(description),
                "max_attempts": self._normalize_max_attempts(max_attempts),
                "questions": quiz.get("questions") or [],
            }
        )
        catalog["quizzes"][index] = quiz
        if catalog.get("active") == old_slug:
            catalog["active"] = slug
        self._write_catalog(catalog)
        return self._serialize_quiz(quiz)

    def delete_quiz(self, quiz_id: int | str) -> None:
        catalog = self._load_catalog()
        index, quiz = self._find_quiz(catalog, quiz_id)
        if catalog.get("active") == quiz.get("slug"):
            raise ValueError("Cannot delete the active quiz.")
        catalog["quizzes"].pop(index)
        self._write_catalog(catalog)

    def list_questions(self, quiz_id: int | str) -> list[dict]:
        _, quiz = self._find_quiz(self._load_catalog(), quiz_id)
        quiz_slug = str(quiz.get("slug") or "")
        return [
            self._serialize_question(quiz_slug, item)
            for item in sorted(quiz.get("questions") or [], key=lambda q: int(q.get("order_index") or 0))
        ]

    def create_question(
        self,
        *,
        quiz_id: int | str,
        question_key: str,
        question: str,
        options: list[str],
        correct_answer: str,
        hint: str = "",
        explanation: str = "",
        order_index: int = 0,
    ) -> dict:
        catalog = self._load_catalog()
        quiz_index, quiz = self._find_quiz(catalog, quiz_id)
        question_key = self._normalize_question_key(question_key)
        questions = quiz.setdefault("questions", [])
        if any(str(item.get("question_key") or item.get("id") or "") == question_key for item in questions):
            raise ValueError(f"Question key '{question_key}' already exists for this quiz.")
        item = self._question_payload(
            question_key=question_key,
            question=question,
            options=options,
            correct_answer=correct_answer,
            hint=hint,
            explanation=explanation,
            order_index=order_index,
        )
        questions.append(item)
        catalog["quizzes"][quiz_index] = quiz
        self._write_catalog(catalog)
        return self._serialize_question(str(quiz.get("slug") or ""), item)

    def update_question(
        self,
        question_id: int | str,
        *,
        question_key: str,
        question: str,
        options: list[str],
        correct_answer: str,
        hint: str = "",
        explanation: str = "",
        order_index: int = 0,
    ) -> dict:
        catalog = self._load_catalog()
        quiz_index, quiz, question_index, existing = self._find_question(catalog, question_id)
        question_key = self._normalize_question_key(question_key)
        questions = quiz.setdefault("questions", [])
        for idx, item in enumerate(questions):
            key = str(item.get("question_key") or item.get("id") or "")
            if idx != question_index and key == question_key:
                raise ValueError(f"Question key '{question_key}' already exists for this quiz.")
        item = self._question_payload(
            question_key=question_key,
            question=question,
            options=options,
            correct_answer=correct_answer,
            hint=hint,
            explanation=explanation,
            order_index=order_index,
        )
        questions[question_index] = item
        catalog["quizzes"][quiz_index] = quiz
        self._write_catalog(catalog)
        return self._serialize_question(str(quiz.get("slug") or ""), item)

    def delete_question(self, question_id: int | str) -> None:
        catalog = self._load_catalog()
        quiz_index, quiz, question_index, _question = self._find_question(catalog, question_id)
        quiz.setdefault("questions", []).pop(question_index)
        catalog["quizzes"][quiz_index] = quiz
        self._write_catalog(catalog)

    def get_runtime_quiz(self, slug: str) -> dict:
        _, quiz = self._find_quiz(self._load_catalog(), slug)
        return {
            "quiz_id": quiz["slug"],
            "max_attempts": int(quiz.get("max_attempts") or 2),
            "questions": [
                {
                    "id": str(q.get("question_key") or q.get("id") or ""),
                    "question": str(q.get("question") or ""),
                    "options": list(q.get("options") or []),
                    "correct_answer": str(q.get("correct_answer") or ""),
                    "hint": str(q.get("hint") or ""),
                    "explanation": str(q.get("explanation") or ""),
                }
                for q in sorted(quiz.get("questions") or [], key=lambda item: int(item.get("order_index") or 0))
            ],
        }

    def get_active_selection(self) -> dict[str, str]:
        catalog = self._load_catalog()
        active_slug = str(catalog.get("active") or "")
        if not active_slug and catalog["quizzes"]:
            active_slug = str(catalog["quizzes"][0].get("slug") or "")
        if not active_slug:
            return {}
        try:
            _, quiz = self._find_quiz(catalog, active_slug)
        except LookupError:
            return {}
        return {"quiz_slug": quiz["slug"], "quiz_title": quiz.get("title") or quiz["slug"]}

    def set_active_selection(self, *, quiz_slug: str) -> dict[str, str]:
        catalog = self._load_catalog()
        quiz_slug = self._normalize_slug(quiz_slug)
        _, quiz = self._find_quiz(catalog, quiz_slug)
        catalog["active"] = quiz["slug"]
        self._write_catalog(catalog)
        return {"quiz_slug": quiz["slug"], "quiz_title": quiz.get("title") or quiz["slug"]}

    def get_available_options(self) -> dict:
        return {
            "quizzes": [
                {
                    **self._serialize_quiz(quiz),
                    "question_count": len(quiz.get("questions") or []),
                }
                for quiz in self._load_catalog().get("quizzes", [])
            ]
        }

    def _question_payload(
        self,
        *,
        question_key: str,
        question: str,
        options: list[str],
        correct_answer: str,
        hint: str = "",
        explanation: str = "",
        order_index: int = 0,
    ) -> dict:
        options = self._normalize_options(options)
        return {
            "question_key": question_key,
            "question": self._normalize_required_text(question, "Question"),
            "options": options,
            "correct_answer": self._normalize_correct_answer(correct_answer, options),
            "hint": self._normalize_optional_text(hint),
            "explanation": self._normalize_optional_text(explanation),
            "order_index": int(order_index),
        }
