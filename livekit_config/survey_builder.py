import logging
from typing import Optional

from content_service.content_store.surveys.service import SurveyService

logger = logging.getLogger(__name__)

class SurveyBuilder: 
    def __init__(self):
        self.survey_service = SurveyService(include_examples=True)
    
    def build(self, survey_slug: Optional[str] = None) -> dict:
        active = self.survey_service.get_active_selection()
        effective_slug = survey_slug or active.get("survey_slug")

        if not effective_slug:
            raise ValueError("No active survey state found. Provide survey_slug explicitly.")

        return self.survey_service.get_runtime_survey(effective_slug)


_builder: Optional[SurveyBuilder] = None

def get_survey_builder() -> SurveyBuilder:
    global _builder
    if _builder is None:
        _builder = SurveyBuilder()
    return _builder
