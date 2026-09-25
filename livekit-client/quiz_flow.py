import json
import logging
import sys
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Optional

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from livekit.agents import AgentTask, function_tool
from livekit.agents.beta.workflows import TaskGroup
from livekit.agents.llm.tool_context import ToolError

from livekit_config.quiz_builder import get_quiz_builder
from livekit_config.voice_canary import english_voice_enabled


logger = logging.getLogger("quiz-flow")


def _localized(sr: str, en: str) -> str:
    return en if english_voice_enabled() else sr


class QuizPhase(str, Enum):
    IDLE = "idle"
    WAITING_FOR_ANSWER = "waiting_for_answer"
    FINISHED = "finished"


@dataclass
class QuizQuestion:
    id: str
    question: str
    options: list[str]
    correct_answer: str
    hint: str = ""
    explanation: str = ""

    def __post_init__(self) -> None:
        self.question = self.question.strip()
        self.options = [option.strip() for option in self.options if option.strip()]
        self.correct_answer = self.correct_answer.strip()
        self.hint = self.hint.strip()
        self.explanation = self.explanation.strip()

        if not self.question:
            raise ValueError("Question text is required")
        if len(self.options) < 2:
            raise ValueError("At least two options are required")
        if self.correct_answer not in self.options:
            raise ValueError("correct_answer must match one of the options")

    @property
    def spoken_question(self) -> str:
        if len(self.options) == 2:
            options_text = _localized(
                f"{self.options[0]} ili {self.options[1]}",
                f"{self.options[0]} or {self.options[1]}",
            )
        else:
            options_text = _localized(
                f"{', '.join(self.options[:-1])} i {self.options[-1]}",
                f"{', '.join(self.options[:-1])}, or {self.options[-1]}",
            )
        return _localized(
            f"{self.question} Opcije su: {options_text}.",
            f"{self.question} The options are: {options_text}.",
        )


@dataclass
class QuizResponse:
    question_id: str
    raw_input: str = ""
    is_correct: bool = False
    attempts_used: int = 0
    correct_answer_revealed: bool = False


@dataclass
class QuizState:
    quiz_id: str
    questions: list[QuizQuestion] = field(default_factory=list)
    responses: dict[str, QuizResponse] = field(default_factory=dict)
    current_index: int = 0
    attempts_for_current: int = 0
    max_attempts: int = 2
    score: int = 0
    active: bool = False
    phase: QuizPhase = QuizPhase.IDLE

    @property
    def total_questions(self) -> int:
        return len(self.questions)

    @property
    def current_question(self) -> Optional[QuizQuestion]:
        if 0 <= self.current_index < len(self.questions):
            return self.questions[self.current_index]
        return None


def build_default_quiz() -> QuizState:
    if english_voice_enabled():
        return QuizState(
            quiz_id="comtrade-english-quiz",
            max_attempts=2,
            questions=[
                QuizQuestion(
                    id="en_q1",
                    question="What does the name TITAN stand for?",
                    options=[
                        "Task Intelligence and Technology Automation Node",
                        "Technical Integration and Testing Assistant Network",
                        "Technology Innovation Team and Navigation",
                    ],
                    correct_answer="Task Intelligence and Technology Automation Node",
                    hint="It describes task intelligence, technology, and automation.",
                    explanation="TITAN stands for Task Intelligence and Technology Automation Node.",
                ),
                QuizQuestion(
                    id="en_q2",
                    question="Which humanoid platform is TITAN built on?",
                    options=["AgiBot A2 Ultra", "Unitree G1", "Boston Dynamics Atlas"],
                    correct_answer="AgiBot A2 Ultra",
                    hint="It is an AgiBot service robot platform.",
                    explanation="TITAN is built on the AgiBot A2 Ultra platform.",
                ),
                QuizQuestion(
                    id="en_q3",
                    question="What is TITAN's main purpose at reception?",
                    options=["Replace employees", "Welcome and assist visitors", "Provide security patrols"],
                    correct_answer="Welcome and assist visitors",
                    hint="TITAN supports people at the office entrance.",
                    explanation="TITAN welcomes and assists visitors while supporting employees.",
                ),
            ],
        )
    return QuizState(
        quiz_id="petrol-station-quiz",
        max_attempts=2,
        questions=[
            QuizQuestion(
                id="q8",
                question="Gde se pravilno sipa gorivo u automobil?",
                options=[
                    "motor",
                    "gepek",
                    "rezervoar za gorivo",
                    "gume",
                ],
                correct_answer="rezervoar za gorivo",
                hint="To je deo vozila namenjen skladištenju goriva.",
                explanation="Gorivo se sipa u rezervoar za gorivo.",
            ),
            QuizQuestion(
                id="q9",
                question="Zašto je pušenje na benzinskim pumpama zabranjeno?",
                options=[
                    "Jer neprijatno miriše",
                    "Jer ometa vozače",
                    "Jer može izazvati požar",
                    "Jer je skupo",
                ],
                correct_answer="Jer može izazvati požar",
                hint="Razlog je povezan sa bezbednošću i zapaljivim isparenjima goriva.",
                explanation="Pušenje je zabranjeno jer su isparenja goriva zapaljiva i mogu izazvati požar.",
            ),
            QuizQuestion(
                id="q10",
                question="Koja je česta dodatna usluga na benzinskim pumpama?",
                options=[
                    "Frizerske usluge",
                    "Punjenje guma vazduhom",
                    "Bioskopske karte",
                    "Rezervacija hotela",
                ],
                correct_answer="Punjenje guma vazduhom",
                hint="Ova usluga pomaže održavanju pravilnog pritiska u gumama.",
                explanation="Mnoge benzinske pumpe nude punjenje guma vazduhom kako bi vozači mogli proveriti i dopuniti pritisak.",
            ),
        ],
    )


def build_quiz_from_payload(payload: dict) -> QuizState:
    return QuizState(
        quiz_id=str(payload["quiz_id"]),
        max_attempts=int(payload.get("max_attempts", 2)),
        questions=[
            QuizQuestion(
                id=str(item["id"]),
                question=item["question"],
                options=list(item["options"]),
                correct_answer=item["correct_answer"],
                hint=item.get("hint", ""),
                explanation=item.get("explanation", ""),
            )
            for item in payload.get("questions", [])
        ],
    )


def load_quiz_state() -> QuizState:
    if english_voice_enabled():
        return build_default_quiz()
    try:
        payload = get_quiz_builder().build()
        return build_quiz_from_payload(payload)
    except Exception as exc:
        logger.warning("Unable to load quiz from local files, using default quiz: %s", exc, exc_info=True)
        return build_default_quiz()


class QuizStopped(RuntimeError):
    pass


class QuizQuestionTask(AgentTask[QuizResponse]):
    def __init__(
        self,
        *,
        quiz_state: QuizState,
        question: QuizQuestion,
        question_index: int,
    ) -> None:
        self._quiz_state = quiz_state
        self._question = question
        self._question_index = question_index

        super().__init__(
            instructions=self._build_instructions(question),
            allow_interruptions=True,
        )

    @staticmethod
    def _build_instructions(question: QuizQuestion) -> str:
        language_rules = (
            [
                "Respond in the same language the user is currently speaking, Serbian or English.",
                "The quiz questions and options are written in English. Read them out in the user's language, but keep proper nouns such as Comtrade, TITAN, AgiBot and CERN in their original form.",
            ]
            if english_voice_enabled()
            else [
                "Respond in the same language the user is currently speaking, Serbian or English.",
                "Default to Serbian if the user's language is unclear or mixed.",
            ]
        )
        return "\n".join(
            [
                "You are handling exactly one quiz question.",
                *language_rules,
                "You are only responsible for the current question.",
                "Do not mention previous questions.",
                "Do not mention next questions unless the current answer is finished.",
                "When the user gives a clear answer, call `submit_answer` immediately.",
                "If the user explicitly asks to repeat the question, call `repeat_question`.",
                "If the user explicitly asks for a hint, call `give_hint`.",
                "If the user's answer is unclear, ambiguous, missing, or you are not confident, call `ask_clarification`.",
                "If the user wants to stop the quiz, call `stop_quiz`.",
                "Judge correctness by meaning, not exact wording.",
                "Do not require the user to repeat an option verbatim.",
                "Accept clearly equivalent spoken answers, including shorter phrasings, paraphrases, inflected forms, reordered words, and minor STT errors.",
                "If the user's answer clearly points to the same concept as the correct option, mark it correct.",
                "If the user's answer clearly points to a different option, mark it incorrect.",
                "If the answer is partial but still unambiguously refers to the correct option, mark it correct.",
                "If the answer is too vague, could match multiple options, or you are not confident, call `ask_clarification` instead of marking it wrong.",
                "Accept answers that refer to an option by position when the mapping is clear, for example: first, second, third, last.",
                "Prefer a slightly forgiving interpretation for obviously correct spoken answers.",
                f"Current question text: {question.question}",
                f"Allowed options: {json.dumps(question.options, ensure_ascii=False)}",
                f"Correct answer: {question.correct_answer}",
                f"Hint: {question.hint}",
                f"Explanation: {question.explanation}",
                "For `submit_answer`, pass:",
                "- `answer` as the user's actual answer text.",
                "- `is_correct=true` if the answer clearly means the same thing as the correct option, even if phrased differently.",
                "- `is_correct=false` only if the answer clearly means a different option or is clearly wrong.",
                "- `confidence` as your confidence from 0.0 to 1.0.",
                "If confidence is not high enough, do not guess. Use `ask_clarification`.",
            ]
        )

    async def on_enter(self) -> None:
        self._quiz_state.current_index = self._question_index

        question_number = self._question_index + 1
        prefix = _localized(f"Pitanje broj {question_number}.", f"Question {question_number}.")
        await self.session.say(
            f"{prefix} {self._question.spoken_question}",
            allow_interruptions=False,
            add_to_chat_ctx=True,
        )

    @function_tool(
        name="submit_answer",
        description="Evaluate and record the user's answer for the current quiz question, update attempts and score, and resolve the question.",
    )
    async def submit_answer(
        self,
        answer: str,
        is_correct: bool,
        confidence: float | None = None,
    ) -> str | None:
        normalized_answer = (answer or "").strip()
        judged_confidence = self._coerce_confidence(confidence)
        
        logger.info("submit_answer called with answer=%r, is_correct=%r, confidence=%r (normalized_answer=%r, judged_confidence=%r)", answer, is_correct, confidence, normalized_answer, judged_confidence)

        if not normalized_answer:
            raise ToolError("Nedostaje odgovor korisnika.")

        if is_correct is False and judged_confidence < 0.35:
            raise ToolError("Odgovor nije dovoljno jasan za pouzdanu procenu.")

        if is_correct:
            self._quiz_state.score += 1
            attempts_used = self._quiz_state.attempts_for_current + 1

            response = QuizResponse(
                question_id=self._question.id,
                raw_input=normalized_answer,
                is_correct=True,
                attempts_used=attempts_used,
                correct_answer_revealed=False,
            )
            self._quiz_state.responses[self._question.id] = response
            self._quiz_state.attempts_for_current = 0
            self._quiz_state.current_index = self._question_index + 1

            feedback = _localized("Tačno.", "Correct.")
            if self._question.explanation:
                feedback = f"{feedback} {self._question.explanation}"

            #await self.session.say(
            #    feedback,
            #    allow_interruptions=False,
            #    add_to_chat_ctx=True,
            #)
            
            await self.session.generate_reply(instructions=feedback, allow_interruptions=False)

            if not self.done():
                self.complete(response)
            return None

        self._quiz_state.attempts_for_current += 1
        attempts_used = self._quiz_state.attempts_for_current

        if attempts_used < self._quiz_state.max_attempts:
            hint_text = self._question.hint or _localized("Razmislite ponovo.", "Think about it again.")
            return _localized(
                f"Netačno. Nagoveštaj: {hint_text} {self._question.spoken_question}",
                f"Not quite. Hint: {hint_text} {self._question.spoken_question}",
            )

        response = QuizResponse(
            question_id=self._question.id,
            raw_input=normalized_answer,
            is_correct=False,
            attempts_used=attempts_used,
            correct_answer_revealed=True,
        )
        self._quiz_state.responses[self._question.id] = response
        self._quiz_state.attempts_for_current = 0
        self._quiz_state.current_index = self._question_index + 1

        feedback = _localized(
            f"Netačno. Tačan odgovor je {self._question.correct_answer}.",
            f"Incorrect. The correct answer is {self._question.correct_answer}.",
        )
        if self._question.explanation:
            feedback = f"{feedback} {self._question.explanation}"

        #await self.session.say(
        #    feedback,
        #    allow_interruptions=False,
        #    add_to_chat_ctx=True,
        #)
        
        await self.session.generate_reply(instructions=feedback, allow_interruptions=False)
        
        
        if not self.done():
            self.complete(response)
        return None

    @function_tool(
        name="repeat_question",
        description="Repeat the current quiz question.",
    )
    async def repeat_question(self) -> str:
        return _localized(
            f"Naravno. {self._question.spoken_question}",
            f"Of course. {self._question.spoken_question}",
        )

    @function_tool(
        name="give_hint",
        description="Give a hint for the current quiz question without revealing the correct answer.",
    )
    async def give_hint(self) -> str:
        hint_text = self._question.hint or _localized(
            "Nagoveštaj trenutno nije dostupan.", "A hint is not available right now."
        )
        return _localized(
            f"Nagoveštaj: {hint_text} {self._question.spoken_question}",
            f"Hint: {hint_text} {self._question.spoken_question}",
        )

    @function_tool(
        name="ask_clarification",
        description="Ask the user to answer the current quiz question again when the answer is unclear.",
    )
    async def ask_clarification(self, reason: str = "") -> str:
        reason = reason or _localized("Molim vas, odgovorite ponovo.", "Please answer again.")
        return f"{reason} {self._question.spoken_question}"

    @function_tool(
        name="stop_quiz",
        description="Stop the entire quiz.",
    )
    async def stop_quiz(self, reason: str = "user_requested_stop") -> None:
        self._quiz_state.active = False
        self._quiz_state.phase = QuizPhase.FINISHED

        if not self.done():
            self.complete(QuizStopped(reason))

    @staticmethod
    def _coerce_confidence(value: object) -> float:
        try:
            confidence = float(value)
        except (TypeError, ValueError):
            return 0.0
        return max(0.0, min(1.0, confidence))


class QuizFlowTask(AgentTask[QuizState]):
    def __init__(
        self,
        *,
        quiz_state: QuizState | None = None,
    ) -> None:
        self._quiz_state = quiz_state or load_quiz_state()
        super().__init__(
            instructions=_localized(
                "You are running a Serbian/English bilingual spoken quiz flow, defaulting to Serbian. The detailed handling of each quiz question is delegated to child tasks.",
                "You are running a Serbian/English bilingual spoken quiz whose question bank is written in English. Always answer in the language the user is speaking. The detailed handling of each quiz question is delegated to child tasks.",
            ),
            allow_interruptions=True,
        )

    async def on_enter(self) -> None:
        quiz = self._quiz_state

        if not quiz.questions:
            await self.session.say(
                _localized("Trenutno nemam podešena pitanja za kviz.", "I do not have any quiz questions configured right now."),
                allow_interruptions=False,
                add_to_chat_ctx=True,
            )
            quiz.active = False
            quiz.phase = QuizPhase.FINISHED
            if not self.done():
                self.complete(quiz)
            return

        quiz.active = True
        quiz.phase = QuizPhase.WAITING_FOR_ANSWER
        quiz.current_index = 0
        quiz.attempts_for_current = 0
        quiz.score = 0
        quiz.responses = {}

        await self.session.say(
            _localized(
                f"Sledi kratak kviz sa {quiz.total_questions} pitanja.",
                f"Here is a short quiz with {quiz.total_questions} questions.",
            ),
            allow_interruptions=False,
            add_to_chat_ctx=True,
        )

        group = TaskGroup(summarize_chat_ctx=False)
        for index, question in enumerate(quiz.questions):
            group.add(
                lambda q=question, i=index: QuizQuestionTask(
                    quiz_state=quiz,
                    question=q,
                    question_index=i,
                ),
                id=question.id,
                description=question.question,
            )

        try:
            result = await group
            logger.info("Quiz task group finished with %d question results", len(result.task_results))
        except QuizStopped as exc:
            logger.info("Quiz stopped early: %s", exc)
            quiz.active = False
            quiz.phase = QuizPhase.FINISHED
            await self.session.say(
                _localized(
                    f"U redu. Prekidam kviz. Trenutni rezultat je {quiz.score} od {quiz.total_questions}.",
                    f"All right. I am stopping the quiz. Your score is {quiz.score} out of {quiz.total_questions}.",
                ),
                allow_interruptions=False,
                add_to_chat_ctx=True,
            )
            if not self.done():
                self.complete(quiz)
            return

        quiz.active = False
        quiz.phase = QuizPhase.FINISHED
        quiz.current_index = quiz.total_questions

        await self.session.say(
            _localized(
                f"Kviz je završen. Vaš rezultat je {quiz.score} od {quiz.total_questions}.",
                f"The quiz is complete. Your score is {quiz.score} out of {quiz.total_questions}.",
            ),
            allow_interruptions=False,
            add_to_chat_ctx=True,
        )

        if not self.done():
            self.complete(quiz)


__all__ = [
    "QuizFlowTask",
    "QuizPhase",
    "QuizQuestion",
    "QuizQuestionTask",
    "QuizResponse",
    "QuizState",
    "QuizStopped",
    "build_default_quiz",
    "build_quiz_from_payload",
    "load_quiz_state",
]
