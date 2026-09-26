import logging
from typing import Optional

from content_service.content_store.quizzes.service import QuizService

logger = logging.getLogger(__name__)


class QuizBuilder:
    

    def __init__(self):
        self.quiz_service = QuizService(include_examples=True)

    def build(self, quiz_slug: Optional[str] = None) -> dict:
        active = self.quiz_service.get_active_selection()
        effective_slug = quiz_slug or active.get("quiz_slug")

        if not effective_slug:
            raise ValueError("No active quiz state found. Provide quiz_slug explicitly.")

        return self.quiz_service.get_runtime_quiz(effective_slug)

    def update_active(self, quiz_slug: Optional[str] = None, save: bool = True) -> None:
        active = self.quiz_service.get_active_selection()
        effective_slug = quiz_slug or active.get("quiz_slug")

        if not effective_slug:
            raise ValueError("No active quiz state found. Provide quiz_slug explicitly.")

        self.quiz_service.set_active_selection(quiz_slug=effective_slug)

    def get_available_options(self) -> dict:
        return self.quiz_service.get_available_options()

    def get_active(self) -> dict:
        return self.quiz_service.get_active_selection()

    def update_and_build(self, quiz_slug: Optional[str] = None) -> dict:
        self.update_active(quiz_slug=quiz_slug)
        return self.build()


_builder: Optional[QuizBuilder] = None


def get_quiz_builder() -> QuizBuilder:
    global _builder
    if _builder is None:
        _builder = QuizBuilder()
    return _builder


def build_quiz(**kwargs) -> dict:
    return get_quiz_builder().build(**kwargs)
