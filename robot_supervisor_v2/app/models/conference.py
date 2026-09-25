from typing import Literal

from pydantic import BaseModel, Field


class ConferenceStep(BaseModel):
    text: str = ""
    display_text: str | None = None
    gesture: str | None = None
    pause_after_ms: int | None = None
    note: str | None = None


class ConferenceScenario(BaseModel):
    id: str
    label: str
    summary_text: str | None = None
    single_action_only: bool = False
    action_label: str | None = None
    steps: list[ConferenceStep] = Field(default_factory=list)


class ConferenceSection(BaseModel):
    id: str
    section: str
    description: str | None = None
    scenarios: list[ConferenceScenario] = Field(default_factory=list)


class ConferenceRuntimeSection(ConferenceSection):
    status: Literal["ready", "missing", "empty"]
    source_file: str


class ConferenceEventSummary(BaseModel):
    key: str
    event_id: str
    name: str
    location: str | None = None
    language: str | None = None
    robot_name: str | None = None
    source_dir: str


class ConferenceEventListResponse(BaseModel):
    active_event_key: str | None = None
    events: list[ConferenceEventSummary] = Field(default_factory=list)


class ConferenceActiveEventResponse(BaseModel):
    active_event_key: str
    active_event: ConferenceEventSummary


class ConferenceProgram(BaseModel):
    event_key: str
    source_dir: str
    event: dict[str, str] = Field(default_factory=dict)
    robot: dict[str, str] = Field(default_factory=dict)
    scripts: list[str] = Field(default_factory=list)
    sections: list[ConferenceRuntimeSection] = Field(default_factory=list)


class ConferenceEventDocument(BaseModel):
    key: str
    event_id: str
    name: str
    location: str | None = None
    language: str | None = None
    robot_name: str | None = None
    robot_role: str | None = None
    sections: list[ConferenceSection] = Field(default_factory=list)


class ConferenceEventDeleteResponse(BaseModel):
    deleted_key: str
    active_event_key: str | None = None
