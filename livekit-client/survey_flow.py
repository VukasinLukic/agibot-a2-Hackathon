import asyncio
import json
import logging
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Optional

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from livekit.agents import AgentTask, function_tool
from livekit.agents.beta.workflows import TaskGroup
from livekit.agents.llm.tool_context import ToolError

from content_service.content_store.surveys.service import SurveyService
from livekit_config.survey_builder import get_survey_builder
from livekit_config.voice_canary import english_voice_enabled


logger = logging.getLogger("survey-flow")
DEBUG_SURVEY_RESPONSES_PATH = Path(__file__).with_name("survey_responses_debug.json")
_LOCAL_ARCHIVE_TASKS: set[asyncio.Task] = set()


def _localized(sr: str, en: str) -> str:
    return en if english_voice_enabled() else sr



class SurveyPhase(str, Enum):
    IDLE = "idle"
    WAITING_FOR_RESPONSE = "waiting_for_response"
    FINISHED = "finished"


class ResponseKind(str, Enum):
    SINGLE_CHOICE = "single_choice"
    MULTIPLE_CHOICE = "multiple_choice"
    FREE_TEXT = "free_text"
    RANKING = "ranking"


@dataclass
class SurveyQuestion:
    id: str
    question: str
    response_kind: ResponseKind
    options: list[str] = field(default_factory=list)
    allow_skip: bool = True

    def __post_init__(self) -> None:
        self.question = self.question.strip()
        self.options = [option.strip() for option in self.options if option.strip()]

        if not self.question:
            raise ValueError("Question text is required")

        if self.response_kind in {
            ResponseKind.SINGLE_CHOICE,
            ResponseKind.MULTIPLE_CHOICE,
            ResponseKind.RANKING,
        } and len(self.options) < 2:
            raise ValueError(f"{self.response_kind} requires at least two options")

    @property
    def spoken_question(self) -> str:
        if not self.options:
            return self.question

        if len(self.options) == 2:
            options_text = _localized(
                f"{self.options[0]} ili {self.options[1]}",
                f"{self.options[0]} or {self.options[1]}",
            )
        else:
            options_text = _localized(
                f"{', '.join(self.options[:-1])} i {self.options[-1]}",
                f"{', '.join(self.options[:-1])}, and {self.options[-1]}",
            )

        if self.response_kind == ResponseKind.SINGLE_CHOICE:
            return _localized(
                f"{self.question} Izaberite jednu opciju. Opcije su: {options_text}.",
                f"{self.question} Choose one option. The options are: {options_text}.",
            )
        if self.response_kind == ResponseKind.MULTIPLE_CHOICE:
            return _localized(
                f"{self.question} Možete izabrati više opcija. Opcije su: {options_text}.",
                f"{self.question} You may choose more than one. The options are: {options_text}.",
            )
        if self.response_kind == ResponseKind.RANKING:
            return _localized(
                f"{self.question} Rangirajte sve opcije. Opcije su: {options_text}.",
                f"{self.question} Rank all options. The options are: {options_text}.",
            )
        return self.question


@dataclass
class SurveyResponse:
    question_id: str
    raw_input: str = ""
    selected_options: list[str] = field(default_factory=list)
    free_text: str = ""
    ranking: list[str] = field(default_factory=list)


@dataclass
class SurveyState:
    survey_id: str
    questions: list[SurveyQuestion] = field(default_factory=list)
    responses: dict[str, SurveyResponse] = field(default_factory=dict)
    contact_info: str = ""
    current_index: int = 0
    active: bool = False
    phase: SurveyPhase = SurveyPhase.IDLE
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def total_questions(self) -> int:
        return len(self.questions)

    @property
    def current_question(self) -> Optional[SurveyQuestion]:
        if 0 <= self.current_index < len(self.questions):
            return self.questions[self.current_index]
        return None


def build_default_survey() -> SurveyState:
    if english_voice_enabled():
        return SurveyState(
            survey_id="comtrade-visitor-survey-en",
            questions=[
                SurveyQuestion(
                    id="en_s1",
                    question="How clear and helpful was your interaction with TITAN?",
                    response_kind=ResponseKind.SINGLE_CHOICE,
                    options=["Very helpful", "Helpful", "Neutral", "Not helpful"],
                ),
                SurveyQuestion(
                    id="en_s2",
                    question="Which parts of the experience did you value most?",
                    response_kind=ResponseKind.MULTIPLE_CHOICE,
                    options=["Clarity", "Speed", "Voice", "Gestures", "Information"],
                ),
                SurveyQuestion(
                    id="en_s3",
                    question="What could we improve about the robot experience?",
                    response_kind=ResponseKind.FREE_TEXT,
                ),
            ],
        )
    return SurveyState(
        survey_id="petrol-survey",
        questions=[
            SurveyQuestion(
                id="s_free_1",
                question="Šta vam se najviše svidelo tokom današnje posete?",
                response_kind=ResponseKind.FREE_TEXT,
            ),
            SurveyQuestion(
                id="s_rank_1",
                question="Rangirajte ove faktore od najvažnijeg do najmanje važnog prilikom posete servisu.",
                response_kind=ResponseKind.RANKING,
                options=["Cena", "Brzina", "Lokacija", "Ljubaznost osoblja"],
            ),
            SurveyQuestion(
                id="s_free_2",
                question="Šta biste želeli da poboljšamo u usluzi?",
                response_kind=ResponseKind.FREE_TEXT,
            ),
            SurveyQuestion(
                id="s_rank_2",
                question="Rangirajte ove usluge po tome koliko često ih koristite.",
                response_kind=ResponseKind.RANKING,
                options=["Sipanje goriva", "Prodavnica", "Kafa", "Pranje automobila"],
            ),
            SurveyQuestion(
                id="s_free_3",
                question="Opišite svoje iskustvo sa osobljem.",
                response_kind=ResponseKind.FREE_TEXT,
            ),
            SurveyQuestion(
                id="s6",
                question="Šta vam je važno prilikom posete servisu?",
                response_kind=ResponseKind.MULTIPLE_CHOICE,
                options=["Brzina", "Cena", "Lokacija", "Ljubaznost osoblja", "Čistoća"],
            ),
            SurveyQuestion(
                id="s7",
                question="Koje dodatne proizvode najčešće kupujete?",
                response_kind=ResponseKind.MULTIPLE_CHOICE,
                options=["Piće", "Grickalice", "Autokozmetika", "Duvanski proizvodi"],
            ),
            SurveyQuestion(
                id="s8",
                question="Koje načine plaćanja obično koristite?",
                response_kind=ResponseKind.MULTIPLE_CHOICE,
                options=["Gotovina", "Bankovna kartica", "Mobilno plaćanje", "Kartica lojalnosti"],
            ),
            SurveyQuestion(
                id="s_rank_3",
                question="Rangirajte ove osobine po važnosti za dobro korisničko iskustvo.",
                response_kind=ResponseKind.RANKING,
                options=["Čistoća", "Brzina", "Ljubaznost", "Dostupnost"],
            ),
            SurveyQuestion(
                id="s_free_4",
                question="Šta vam je najviše smetalo tokom posete, ako uopšte nešto jeste?",
                response_kind=ResponseKind.FREE_TEXT,
            ),
            SurveyQuestion(
                id="s_rank_4",
                question="Rangirajte ove načine plaćanja od najpogodnijeg do najmanje pogodnog za vas.",
                response_kind=ResponseKind.RANKING,
                options=["Gotovina", "Bankovna kartica", "Mobilno plaćanje"],
            ),
            SurveyQuestion(
                id="s_free_5",
                question="Imate li još neki komentar ili predlog?",
                response_kind=ResponseKind.FREE_TEXT,
            ),
            SurveyQuestion(
                id="s_rank_5",
                question="Rangirajte ove razloge za posetu servisu od najčešćeg do najređeg.",
                response_kind=ResponseKind.RANKING,
                options=["Sipanje goriva", "Kupovina u prodavnici", "Kafa", "Pranje automobila"],
            ),
            SurveyQuestion(
                id="s1",
                question="Koliko često posećujete benzinsku pumpu?",
                response_kind=ResponseKind.SINGLE_CHOICE,
                options=["Svaki dan", "Više puta nedeljno", "Jednom nedeljno", "Retko"],
            ),
            SurveyQuestion(
                id="s2",
                question="Koje gorivo najčešće koristite?",
                response_kind=ResponseKind.SINGLE_CHOICE,
                options=["Benzin", "Dizel", "Struja", "Ne koristim automobil"],
            ),
            SurveyQuestion(
                id="s3",
                question="Koliko ste zadovoljni brzinom usluge?",
                response_kind=ResponseKind.SINGLE_CHOICE,
                options=["Veoma zadovoljan", "Zadovoljan", "Neutralan", "Nezadovoljan"],
            ),
            SurveyQuestion(
                id="s4",
                question="Kako ocenjujete ljubaznost osoblja?",
                response_kind=ResponseKind.SINGLE_CHOICE,
                options=["Odlično", "Dobro", "Prosečno", "Loše"],
            ),
            SurveyQuestion(
                id="s5",
                question="Koje usluge obično koristite?",
                response_kind=ResponseKind.MULTIPLE_CHOICE,
                options=["Sipanje goriva", "Prodavnica", "Kafa", "Pranje automobila"],
            ),
            SurveyQuestion(
                id="s9",
                question="Šta biste najviše poboljšali u vezi posete servisu?",
                response_kind=ResponseKind.FREE_TEXT,
            ),
            SurveyQuestion(
                id="s10",
                question="Rangirajte po važnosti.",
                response_kind=ResponseKind.RANKING,
                options=["Cena", "Brzina", "Lokacija", "Ljubaznost osoblja"],
            ),
        ],
    )



class SurveyStopped(RuntimeError):
    pass


def validate_response(question: SurveyQuestion, response: SurveyResponse) -> None:
    if question.response_kind == ResponseKind.SINGLE_CHOICE:
        if len(response.selected_options) != 1:
            raise ToolError("Molim izaberite jednu opciju.")
        if response.selected_options[0] not in question.options:
            raise ToolError("Ta opcija nije među ponuđenim odgovorima.")

    elif question.response_kind == ResponseKind.MULTIPLE_CHOICE:
        if not response.selected_options:
            raise ToolError("Molim navedite bar jednu opciju.")
        if len(set(response.selected_options)) != len(response.selected_options):
            raise ToolError("Opcije ne smeju biti duplirane.")
        invalid = [item for item in response.selected_options if item not in question.options]
        if invalid:
            raise ToolError("Jedan od odgovora nije među ponuđenim opcijama.")

    elif question.response_kind == ResponseKind.FREE_TEXT:
        if not response.free_text:
            raise ToolError("Molim odgovorite kratkim opisom.")

    elif question.response_kind == ResponseKind.RANKING:
        if len(response.ranking) != len(question.options):
            raise ToolError("Molim rangirajte sve ponuđene opcije.")
        if len(set(response.ranking)) != len(response.ranking):
            raise ToolError("Rangiranje ne sme sadržati duplikate.")
        if set(response.ranking) != set(question.options):
            raise ToolError("Rangiranje mora sadržati sve ponuđene opcije sa istim oznakama.")


def _serialize_response(response: SurveyResponse | None) -> dict | None:
    if response is None:
        return None

    return {
        "question_id": response.question_id,
        "raw_input": response.raw_input,
        "selected_options": response.selected_options,
        "free_text": response.free_text,
        "ranking": response.ranking,
    }


def build_survey_snapshot(survey: SurveyState) -> dict:
    return {
        "survey_id": survey.survey_id,
        "started_at": survey.started_at.isoformat(),
        "active": survey.active,
        "phase": survey.phase.value,
        "current_index": survey.current_index,
        "total_questions": survey.total_questions,
        "responses_recorded": len(survey.responses),
        "contact_info": survey.contact_info,
        "questions": [
            {
                "id": question.id,
                "question": question.question,
                "response_kind": question.response_kind.value,
                "options": question.options,
                "allow_skip": question.allow_skip,
                "response": _serialize_response(survey.responses.get(question.id)),
            }
            for question in survey.questions
        ],
    }

def persist_survey_snapshot(
    survey: SurveyState,
    *,
    snapshot_path: Path | None = DEBUG_SURVEY_RESPONSES_PATH,
    callback: Callable[[SurveyState], None] | None = None,
) -> None:
    snapshot_json = json.dumps(build_survey_snapshot(survey), ensure_ascii=False, indent=2)

    if snapshot_path is not None:
        snapshot_path.write_text(snapshot_json, encoding="utf-8")
        logger.info("Stored survey snapshot to %s", snapshot_path)

    if callback is not None:
        callback(survey)


def archive_survey_snapshot_to_local_store(snapshot: dict) -> dict | None:
    if snapshot.get("phase") != SurveyPhase.FINISHED.value:
        return None

    try:
        archive = SurveyService().archive_snapshot(snapshot)
        logger.info(
            "Archived survey snapshot to local store: run_id=%s archived=%s",
            archive.get("id"),
            archive.get("archived"),
        )
        return archive
    except Exception as exc:
        logger.warning("Unable to archive survey snapshot to local store: %s", exc, exc_info=True)
        return None


def schedule_survey_snapshot_archive(survey: SurveyState) -> None:
    snapshot = build_survey_snapshot(survey)
    if snapshot.get("phase") != SurveyPhase.FINISHED.value:
        return

    async def _archive() -> None:
        await asyncio.to_thread(archive_survey_snapshot_to_local_store, snapshot)

    task = asyncio.create_task(_archive(), name="survey-snapshot-local-archive")
    _LOCAL_ARCHIVE_TASKS.add(task)
    task.add_done_callback(_LOCAL_ARCHIVE_TASKS.discard)

def build_survey_from_payload(payload: dict) -> SurveyState:
    return SurveyState(
        survey_id=str(payload["survey_id"]),
        questions=[
            SurveyQuestion(
                id=str(item["id"]),
                question=item["question"],
                response_kind=ResponseKind(item["response_kind"]),
                options=list(item.get("options", [])),
                allow_skip=bool(item.get("allow_skip", True)),
            )
            for item in payload.get("questions", [])
        ],
    )


def load_survey_state() -> SurveyState:
    if english_voice_enabled():
        return build_default_survey()
    try:
        payload = get_survey_builder().build()
        return build_survey_from_payload(payload)
    except Exception as exc:
        logger.warning("Unable to load survey from local files, using default survey: %s", exc, exc_info=True)
        return build_default_survey()


class SurveyQuestionTask(AgentTask[SurveyResponse]):
    def __init__(
        self,
        *,
        survey_state: SurveyState,
        question: SurveyQuestion,
        question_index: int,
        persist_snapshot: Callable[[], None],
    ) -> None:
        self._survey_state = survey_state
        self._question = question
        self._question_index = question_index
        self._persist_snapshot = persist_snapshot
        


        super().__init__(
            instructions=self._build_instructions(question),
            allow_interruptions=True,
        )

    @staticmethod
    def _build_instructions(question: SurveyQuestion) -> str:
        language_rules = (
            [
                "Respond in the same language the user is currently speaking, Serbian or English.",
                "The survey questions and options are written in English. Read them out in the user's language, but keep proper nouns such as Comtrade, TITAN, AgiBot and CERN in their original form.",
            ]
            if english_voice_enabled()
            else [
                "Respond in the same language the user is currently speaking, Serbian or English.",
                "Default to Serbian if the user's language is unclear or mixed.",
            ]
        )
        base = [
            "You are handling exactly one survey question.",
            *language_rules,
            "You are only responsible for the current question.",
            "Do not mention previous questions.",
            "Do not mention next questions.",
            "When the user gives a valid answer, call `submit_response` immediately.",
            "If the user explicitly asks you to repeat the question, call `repeat_question`.",
            "If the answer is unclear, incomplete, ambiguous, or does not match the available options, call `ask_clarification`.",
            "If the user wants to skip the current question, call `skip_question`.",
            "If the user wants to stop the survey, call `stop_survey`.",
            f"Current question text: {question.question}",
            f"Current response kind: {question.response_kind.value}",
            f"Allowed options: {json.dumps(question.options, ensure_ascii=False)}",
        ]

        if question.response_kind == ResponseKind.SINGLE_CHOICE:
            base.extend(
                [
                    "For single choice questions, `selected_options` must contain exactly one option.",
                    "Use the exact option label from the current question.",
                ]
            )
        elif question.response_kind == ResponseKind.MULTIPLE_CHOICE:
            base.extend(
                [
                    "For multiple choice questions, `selected_options` must contain one or more options.",
                    "Use the exact option labels from the current question.",
                    "Do not invent new options.",
                ]
            )
        elif question.response_kind == ResponseKind.FREE_TEXT:
            base.append("For free text questions, fill `free_text` with the user's actual answer.")
        elif question.response_kind == ResponseKind.RANKING:
            base.extend(
                [
                    "For ranking questions, always fill `ranking`.",
                    "The `ranking` list must contain all options from the current question.",
                    "Use the exact option labels from the current question.",
                    "Preserve the order stated by the user from first to last.",
                    "If the user provides only some of the options, call `ask_clarification`.",
                ]
            )

        return "\n".join(base)

    async def on_enter(self) -> None:
        self._survey_state.current_index = self._question_index
        self._persist_snapshot()

        question_number = self._question_index + 1
        prefix = _localized(f"Pitanje broj {question_number}.", f"Question {question_number}.")
        await self.session.say(
            f"{prefix} {self._question.spoken_question}",
            allow_interruptions=False,
            add_to_chat_ctx=True,
        )

    @function_tool
    async def submit_response(
        self,
        raw_input: str | None = None,
        selected_options: list[str] | None = None,
        free_text: str | None = None,
        ranking: list[str] | None = None,
    ) -> None:
        """Store the user's answer for the current survey question and finish this task."""
        normalized_raw = (raw_input or free_text or "").strip()
        normalized_free_text = (free_text or raw_input or "").strip()

        response = SurveyResponse(
            question_id=self._question.id,
            raw_input=normalized_raw,
            selected_options=[item.strip() for item in (selected_options or []) if item.strip()],
            free_text=normalized_free_text,
            ranking=[item.strip() for item in (ranking or []) if item.strip()],
        )

        validate_response(self._question, response)

        self._survey_state.responses[self._question.id] = response
        self._survey_state.current_index = self._question_index + 1
        self._persist_snapshot()

        if not self.done():
            self.complete(response)

    @function_tool(
        name="repeat_question",
        description="Repeat the current question when the user explicitly asks for repetition.",
    )
    async def repeat_question(self) -> str:
        return _localized(
            f"Naravno. {self._question.spoken_question}",
            f"Of course. {self._question.spoken_question}",
        )

    @function_tool(
        name="ask_clarification",
        description="Use when the user's answer is unclear, incomplete, or ambiguous..",
    )
    async def ask_clarification(self, reason: str = "") -> str:
        reason = reason or _localized("Molim odgovorite ponovo.", "Please answer again.")
        return f"{reason} {self._question.spoken_question}"

    @function_tool(
        name="skip_question",
        description="Skip the current question if skipping is allowed.",
    )
    async def skip_question(self) -> None:
        if  not self._question.allow_skip:
            raise ToolError("Ovo pitanje je obavezno.")

        response = SurveyResponse(
            question_id=self._question.id,
            raw_input="",
        )
        self._survey_state.responses[self._question.id] = response
        self._survey_state.current_index = self._question_index + 1
        self._persist_snapshot()

        if not self.done():
            self.complete(response)

    @function_tool(
        name="stop_survey",
        description="Stop the entire survey.",
    )
    async def stop_survey(self, reason: str = "user_requested_stop") -> None:
        self._survey_state.active = False
        self._survey_state.phase = SurveyPhase.FINISHED
        self._persist_snapshot()

        if not self.done():
            self.complete(SurveyStopped(reason))


class SurveyContactInfoTask(AgentTask[str]):
    def __init__(
        self,
        *,
        survey_state: SurveyState,
        persist_snapshot: Callable[[], None],
    ) -> None:
        self._survey_state = survey_state
        self._persist_snapshot = persist_snapshot

        super().__init__(
            instructions=_localized(
                "You are collecting contact information at the end of a Serbian/English bilingual survey. Ask for a name, email, or phone number. When the user provides contact information, call `submit_contact_info`. If the user declines or wants to skip, call `skip_contact_info`.",
                "You are collecting optional contact information at the end of a Serbian/English bilingual survey. Ask for a name, email, or phone number in the language the user is speaking. When the user provides it, call `submit_contact_info`. If they decline, call `skip_contact_info`.",
            ),
            allow_interruptions=True,
        )

    async def on_enter(self) -> None:
        await self.session.say(
            _localized(
                "Pre nego što završimo anketu, molim vas dajte mi kontakt podatke: ime, e-poštu ili broj telefona.",
                "Before we finish, you may give me a contact name, email address, or phone number.",
            ),
            allow_interruptions=False,
            add_to_chat_ctx=True,
        )

    @function_tool
    async def submit_contact_info(self, contact_info: str | None = None) -> None:
        """Store contact information for the completed survey."""
        normalized = (contact_info or "").strip()
        if not normalized:
            raise ToolError("Molim navedite ime, e-poštu ili broj telefona.")

        self._survey_state.contact_info = normalized
        self._persist_snapshot()

        if not self.done():
            self.complete(normalized)

    @function_tool(
        name="skip_contact_info",
        description="Skip contact information if the user declines.",
    )
    async def skip_contact_info(self) -> None:
        self._survey_state.contact_info = ""
        self._persist_snapshot()

        if not self.done():
            self.complete("")


class SurveyFlowTask(AgentTask[SurveyState]):
    def __init__(
        self,
        *,
        survey_state: SurveyState | None = None,
        snapshot_path: Path | None = DEBUG_SURVEY_RESPONSES_PATH,
        snapshot_callback: Callable[[SurveyState], None] | None = None,
    ) -> None:
        self._survey_state = survey_state or load_survey_state()
        self._snapshot_path = snapshot_path
        self._snapshot_callback = snapshot_callback

        super().__init__(
            instructions=_localized(
                "You are running a Serbian/English bilingual spoken survey flow, defaulting to Serbian. The detailed handling of each question is delegated to child tasks.",
                "You are running a Serbian/English bilingual spoken survey whose question bank is written in English. Always answer in the language the user is speaking. The detailed handling of each question is delegated to child tasks.",
            ),
            allow_interruptions=True,
        )

    def _persist_snapshot(self) -> None:
        persist_survey_snapshot(
            self._survey_state,
            snapshot_path=self._snapshot_path,
            callback=self._snapshot_callback,
        )

    async def on_enter(self) -> None:
        survey = self._survey_state

        if not survey.questions:
            await self.session.say(
                _localized("Trenutno nemam podešena pitanja za anketu.", "I do not have any survey questions configured right now."),
                allow_interruptions=False,
                add_to_chat_ctx=True,
            )
            survey.active = False
            survey.phase = SurveyPhase.FINISHED
            self._persist_snapshot()
            self.complete(survey)
            return

        survey.active = True
        survey.phase = SurveyPhase.WAITING_FOR_RESPONSE
        survey.current_index = 0
        survey.responses = {}
        self._persist_snapshot()

        await self.session.say(
            _localized(
                f"Sledi kratka anketa sa {survey.total_questions} pitanja.",
                f"Here is a short survey with {survey.total_questions} questions.",
            ),
            allow_interruptions=False,
            add_to_chat_ctx=True,
        )

        group = TaskGroup(summarize_chat_ctx=False)
        for index, question in enumerate(survey.questions):
            group.add(
                lambda q=question, i=index: SurveyQuestionTask(
                    survey_state=survey,
                    question=q,
                    question_index=i,
                    persist_snapshot=self._persist_snapshot,
                ),
                id=question.id,
                description=question.question,
            )

        try:
            result = await group # 
            logger.info("Survey task group finished with %d question results", len(result.task_results))
        except SurveyStopped as exc:
            logger.info("Survey stopped early: %s", exc)
            survey.active = False
            survey.phase = SurveyPhase.FINISHED
            self._persist_snapshot()
            await self.session.say(
                _localized("U redu. Prekidam anketu.", "All right. I am stopping the survey."),
                allow_interruptions=False,
                add_to_chat_ctx=True,
            )
            if not self.done():
                self.complete(survey)
            return

        await SurveyContactInfoTask(
            survey_state=survey,
            persist_snapshot=self._persist_snapshot,
        )

        survey.active = False
        survey.phase = SurveyPhase.FINISHED
        survey.current_index = survey.total_questions
        self._persist_snapshot()

        await self.session.say(
            _localized("Hvala. Anketa je završena.", "Thank you. The survey is complete."),
            allow_interruptions=False,
            add_to_chat_ctx=True,
        )
        schedule_survey_snapshot_archive(survey)
        if not self.done():
            self.complete(survey)


__all__ = [
    "DEBUG_SURVEY_RESPONSES_PATH",
    "ResponseKind",
    "SurveyFlowTask",
    "SurveyPhase",
    "SurveyContactInfoTask",
    "SurveyQuestion",
    "SurveyQuestionTask",
    "SurveyResponse",
    "SurveyState",
    "SurveyStopped",
    "archive_survey_snapshot_to_local_store",
    "build_default_survey",
    "build_survey_snapshot",
    "persist_survey_snapshot",
    "schedule_survey_snapshot_archive",
    "validate_response",
]
