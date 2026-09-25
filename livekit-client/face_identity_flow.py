"""
Face-identity conversation flow: greet enrolled people by name, and run a short
consent-gated enrollment for people the vision service does not recognize.

Split of responsibilities (mirrors the card-capture/ID-scanning split already in
this repo):

  - robot_services/vision/detection  owns the camera, resolves identity at lock
    time, computes embeddings, and stores them in a local SQLite face store.
    Raw embeddings never leave that process.
  - robot_supervisor_v2               relays state over HTTP: it exposes the
    resolved identity on /api/vision and brokers face-capture / face-forget
    requests, but never sees a biometric vector.
  - this module (the LiveKit agent)   drives the actual spoken conversation by
    calling the supervisor. It only ever sees names and opaque face ids.

LiveKit AgentTask constraint that shapes this file: an AgentTask may only be
awaited from inside an Agent's on_enter/on_exit or a tool function's own call
stack - never from a detached background task. FaceEnrollmentTask is therefore
awaited directly in HumanoidAgent.on_enter() (and in the retry tool), not via
asyncio.create_task().
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Optional

from livekit.agents import AgentTask, function_tool
from livekit.agents.llm.tool_context import ToolError
from livekit_config.voice_locale_resources import localized_mapping

logger = logging.getLogger("face-identity-flow")

SUPERVISOR_API_URL = os.getenv("SUPERVISOR_API_URL", "http://127.0.0.1:8080").rstrip("/")
FACE_HTTP_TIMEOUT_S = float(os.getenv("FACE_HTTP_TIMEOUT_S", "3.0"))
FACE_CAPTURE_TIMEOUT_S = float(os.getenv("FACE_CAPTURE_TIMEOUT_S", "20.0"))
FACE_CAPTURE_POLL_INTERVAL_S = float(os.getenv("FACE_CAPTURE_POLL_INTERVAL_S", "0.5"))
FACE_FORGET_TIMEOUT_S = float(os.getenv("FACE_FORGET_TIMEOUT_S", "8.0"))

# A conversation dispatch can lag well behind the detection that triggered it
# (e.g. the agent process was restarting when someone walked up). Acting on an
# identity older than this risks greeting an empty room by name.
FACE_DETECTION_MAX_AGE_S = float(os.getenv("FACE_DETECTION_MAX_AGE_S", "10.0"))

# Spoken lines. Serbian remains the production control; the English canary is
# sourced from one structured locale resource.
_SERBIAN_FACE_TEXT = {
    "known_greeting": "Zdravo ponovo, {name}. Lepo je videti vas. Kako vam mogu pomoći?",
    "unknown_greeting": "Zdravo. Drago mi je da vas vidim.",
    "ask_name": "Mislim da se još nismo upoznali. Kako se zovete?",
    "consent_question": "Drago mi je, {name}. Da li želite da zapamtim vaše lice, kako bih vas prepoznao i pozdravio po imenu kada se sledeći put vidimo? Recite da ili ne.",
    "consent_declined": "Nema problema, neću ništa sačuvati. Kako vam mogu pomoći?",
    "capture_close": "Hvala. Priđite mi malo bliže i pogledajte u kameru, mirno, samo trenutak.",
    "capture_far": "Odlično. Sada se vratite na normalnu razdaljinu za razgovor i ponovo me pogledajte.",
    "enroll_success": "Zapamtio sam vas, {name}. Kako vam mogu pomoći?",
    "enroll_failed": "Izvinite, nisam uspeo dobro da vidim vaše lice. Nećemo to sada, možemo probati kasnije. Kako vam mogu pomoći?",
    "enroll_unavailable": "Pamćenje lica trenutno nije dostupno. Kako vam mogu pomoći?",
    "name_skipped": "U redu, nema problema. Kako vam mogu pomoći?",
    "forget_done": "U redu, zaboravio sam vaše lice.",
    "forget_not_found": "Nemam sačuvano vaše lice, pa nema šta da zaboravim.",
    "forget_failed": "Izvinite, nisam uspeo da izbrišem podatke. Prosim, probajte kasnije.",
}
_FACE_TEXT = localized_mapping("face_identity", _SERBIAN_FACE_TEXT)

SAY_KNOWN_GREETING = os.getenv(
    "FACE_SAY_KNOWN_GREETING",
    _FACE_TEXT["known_greeting"],
)
# Lead-in used instead of the standard greeting when enrollment is about to
# run. The standard greeting ends by asking how it can help, which reads badly
# immediately before "what is your name?"; the enrollment flow asks that itself
# once the exchange is finished.
SAY_UNKNOWN_GREETING = os.getenv(
    "FACE_SAY_UNKNOWN_GREETING",
    _FACE_TEXT["unknown_greeting"],
)
SAY_ASK_NAME = os.getenv(
    "FACE_SAY_ASK_NAME",
    _FACE_TEXT["ask_name"],
)
SAY_CONSENT_QUESTION = os.getenv(
    "FACE_SAY_CONSENT_QUESTION",
    _FACE_TEXT["consent_question"],
)
SAY_CONSENT_DECLINED = os.getenv(
    "FACE_SAY_CONSENT_DECLINED",
    _FACE_TEXT["consent_declined"],
)
SAY_CAPTURE_CLOSE = os.getenv(
    "FACE_SAY_CAPTURE_CLOSE",
    _FACE_TEXT["capture_close"],
)
SAY_CAPTURE_FAR = os.getenv(
    "FACE_SAY_CAPTURE_FAR",
    _FACE_TEXT["capture_far"],
)
SAY_ENROLL_SUCCESS = os.getenv(
    "FACE_SAY_ENROLL_SUCCESS",
    _FACE_TEXT["enroll_success"],
)
SAY_ENROLL_FAILED = os.getenv(
    "FACE_SAY_ENROLL_FAILED",
    _FACE_TEXT["enroll_failed"],
)
SAY_ENROLL_UNAVAILABLE = os.getenv(
    "FACE_SAY_ENROLL_UNAVAILABLE",
    _FACE_TEXT["enroll_unavailable"],
)
SAY_NAME_SKIPPED = os.getenv(
    "FACE_SAY_NAME_SKIPPED",
    _FACE_TEXT["name_skipped"],
)
SAY_FORGET_DONE = os.getenv(
    "FACE_SAY_FORGET_DONE",
    _FACE_TEXT["forget_done"],
)
SAY_FORGET_NOT_FOUND = os.getenv(
    "FACE_SAY_FORGET_NOT_FOUND",
    _FACE_TEXT["forget_not_found"],
)
SAY_FORGET_FAILED = os.getenv(
    "FACE_SAY_FORGET_FAILED",
    _FACE_TEXT["forget_failed"],
)

CAPTURE_TERMINAL_STATES = {"captured", "failed", "cancelled", "expired"}
FORGET_TERMINAL_STATES = {"deleted", "not_found", "failed"}


class FaceApiError(RuntimeError):
    """A supervisor face endpoint was unreachable, disabled, or errored."""

    def __init__(
        self,
        message: str,
        *,
        status: int | None = None,
        payload: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.status = status
        self.payload = payload or {}


@dataclass
class VisionIdentitySnapshot:
    """What /api/vision reports about who is currently in front of the robot."""

    feature_enabled: bool = False
    vision_active: bool = False
    person_present: bool = False
    status: Optional[str] = None
    name: Optional[str] = None
    face_id: Optional[str] = None
    canonical_name: Optional[str] = None
    aliases: list[str] = field(default_factory=list)
    detected_at: Optional[float] = None
    age_s: Optional[float] = None

    @property
    def is_fresh(self) -> bool:
        """True when the detection is recent enough to act on.

        Guards the case where conversation dispatch was delayed: a stale
        detection must not make the robot greet a room that is now empty.
        """
        return self.age_s is not None and self.age_s <= FACE_DETECTION_MAX_AGE_S

    @property
    def should_greet_by_name(self) -> bool:
        return bool(
            self.feature_enabled
            and self.vision_active
            and self.person_present
            and self.is_fresh
            and self.status == "known"
            and self.name
        )

    @property
    def should_enroll(self) -> bool:
        return bool(
            self.feature_enabled
            and self.vision_active
            and self.person_present
            and self.is_fresh
            and self.status == "unknown"
        )


@dataclass
class FaceEnrollmentResult:
    """Outcome of one enrollment attempt, for logging/instruction refresh."""

    outcome: str = "skipped"  # enrolled | declined | failed | unavailable | skipped
    name: Optional[str] = None
    face_id: Optional[str] = None
    detail: Optional[str] = None
    captured_distances: list[str] = field(default_factory=list)


async def face_api_json(
    method: str,
    path: str,
    payload: dict[str, Any] | None = None,
    *,
    timeout_s: float = FACE_HTTP_TIMEOUT_S,
) -> dict[str, Any]:
    """Call a supervisor JSON endpoint off the event loop."""
    url = f"{SUPERVISOR_API_URL}{path}"

    def _do_request() -> dict[str, Any]:
        data = None
        headers = {}
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"

        req = urllib.request.Request(url, data=data, headers=headers, method=method.upper())
        try:
            with urllib.request.urlopen(req, timeout=timeout_s) as resp:
                body = resp.read().decode("utf-8", "replace")
                return json.loads(body) if body else {}
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", "replace")
            try:
                parsed = json.loads(body) if body else {}
            except json.JSONDecodeError:
                parsed = {"detail": body}
            raise FaceApiError(
                f"Face API returned HTTP {exc.code}",
                status=exc.code,
                payload=parsed,
            ) from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise FaceApiError(f"Face API request failed: {exc}") from exc
        except json.JSONDecodeError as exc:
            raise FaceApiError("Face API returned invalid JSON") from exc

    return await asyncio.to_thread(_do_request)


async def fetch_vision_identity() -> VisionIdentitySnapshot:
    """Read the current identity/presence snapshot from the supervisor.

    Never raises: an unreachable supervisor yields a snapshot with the feature
    reported off, so callers fall back to normal un-personalized behavior.
    """
    try:
        status = await face_api_json("GET", "/api/vision")
    except FaceApiError as exc:
        logger.warning("Unable to read vision identity status: %s", exc)
        return VisionIdentitySnapshot()

    features = status.get("features") or {}
    face_recognition = features.get("face_recognition") or {}
    identity = status.get("identity") or {}
    detected_at = status.get("identity_at") or status.get("last_detected_at")

    age_s = None
    if isinstance(detected_at, (int, float)):
        age_s = max(0.0, time.time() - float(detected_at))

    snapshot = VisionIdentitySnapshot(
        feature_enabled=bool(face_recognition.get("enabled", False)),
        vision_active=bool(status.get("active", False)),
        person_present=bool(status.get("person_present", False)),
        status=identity.get("status") if isinstance(identity, dict) else None,
        name=identity.get("name") if isinstance(identity, dict) else None,
        face_id=identity.get("face_id") if isinstance(identity, dict) else None,
        canonical_name=identity.get("canonical_name") if isinstance(identity, dict) else None,
        aliases=list(identity.get("aliases") or []) if isinstance(identity, dict) else [],
        detected_at=float(detected_at) if isinstance(detected_at, (int, float)) else None,
        age_s=age_s,
    )
    logger.info(
        "Vision identity snapshot: feature=%s active=%s present=%s status=%s name=%s "
        "age_s=%s fresh=%s",
        snapshot.feature_enabled,
        snapshot.vision_active,
        snapshot.person_present,
        snapshot.status,
        snapshot.name,
        f"{snapshot.age_s:.1f}" if snapshot.age_s is not None else "n/a",
        snapshot.is_fresh,
    )
    return snapshot


async def capture_face_reference(name: str, distance: str) -> dict[str, Any]:
    """Request one distance-gated reference capture and wait for its result.

    The vision side gates each shot on face size, sharpness (Laplacian
    variance) and several consecutive stationary frames, so this can legitimately
    take a few seconds.
    """
    request = await face_api_json(
        "POST",
        "/api/vision/face-capture/request",
        {"name": name, "distance": distance, "timeout_s": FACE_CAPTURE_TIMEOUT_S},
    )
    request_id = request.get("request_id")
    if not request_id:
        raise FaceApiError("Face capture start response did not include request_id", payload=request)

    quoted = urllib.parse.quote(str(request_id), safe="")
    deadline = time.monotonic() + FACE_CAPTURE_TIMEOUT_S + 2.0
    while True:
        result = await face_api_json("GET", f"/api/vision/face-capture/{quoted}")
        state = str(result.get("state") or "").lower()
        if state in CAPTURE_TERMINAL_STATES:
            return result

        if time.monotonic() >= deadline:
            try:
                await face_api_json("POST", f"/api/vision/face-capture/{quoted}/cancel")
            except FaceApiError as exc:
                logger.warning("Best-effort face capture cancel failed for %s: %s", request_id, exc)
            raise FaceApiError("Face capture timed out while polling", payload=result)

        await asyncio.sleep(FACE_CAPTURE_POLL_INTERVAL_S)


async def forget_face(*, face_id: Optional[str] = None, name: Optional[str] = None) -> str:
    """Delete a stored face and wait for the vision service to confirm.

    Deletion is always addressed by a specific face_id when one is known, since
    `name` is not unique - two different real people may share a name.
    Returns one of: deleted | not_found | failed.
    """
    payload: dict[str, Any] = {}
    if face_id:
        payload["face_id"] = face_id
    elif name:
        payload["name"] = name
    else:
        raise FaceApiError("Provide face_id or name to forget a face")

    request = await face_api_json("POST", "/api/vision/face/forget", payload)
    request_id = request.get("request_id")
    if not request_id:
        raise FaceApiError("Face forget response did not include request_id", payload=request)

    deadline = time.monotonic() + FACE_FORGET_TIMEOUT_S
    while True:
        status = await face_api_json("GET", "/api/vision")
        forget_state = ((status.get("features") or {}).get("face_forget") or {})
        if forget_state.get("request_id") == request_id:
            state = str(forget_state.get("state") or "").lower()
            if state in FORGET_TERMINAL_STATES:
                return state

        if time.monotonic() >= deadline:
            logger.warning("Face forget timed out waiting for confirmation request_id=%s", request_id)
            return "failed"

        await asyncio.sleep(FACE_CAPTURE_POLL_INTERVAL_S)


class FaceEnrollmentTask(AgentTask[FaceEnrollmentResult]):
    """Ask for a name, ask for explicit verbal consent, then capture two shots.

    Every scripted line is spoken via session.say() rather than being left to the
    LLM's discretion. That matters most for the consent question: a model turn
    that contains only a tool call and no text produces no speech at all, which
    would silently skip the consent step while the flow carried on.
    """

    def __init__(self) -> None:
        self._result = FaceEnrollmentResult()
        super().__init__(
            instructions="\n".join(
                [
                    "You are collecting a visitor's name and their consent to remember their face.",
                    "Respond in the same language the user speaks, Serbian or English.",
                    "Default to Serbian if the language is unclear.",
                    "Keep every reply to one short sentence.",
                    "When the user tells you their name, call `submit_name` with just the name.",
                    "If the user refuses to give a name, or asks what this is about and then declines,"
                    " call `skip_enrollment`.",
                    "After the consent question has been asked, if the user agrees"
                    " (da, naravno, u redu, yes, sure), call `consent_granted`.",
                    "If the user refuses (ne, nemoj, no, don't), call `consent_denied`.",
                    "Never assume consent. Only call `consent_granted` on a clear yes.",
                    "Do not ask for the name or the consent question yourself - those are spoken for you.",
                    "Do not discuss anything else until this is resolved.",
                ]
            ),
            allow_interruptions=True,
        )

    async def on_enter(self) -> None:
        # Scripted question, spoken deterministically rather than via the LLM.
        await self.session.say(SAY_ASK_NAME, allow_interruptions=True, add_to_chat_ctx=True)

    @function_tool(
        name="submit_name",
        description="Record the visitor's name once they state it, then ask for face-memory consent.",
    )
    async def submit_name(self, name: str) -> None:
        normalized = (name or "").strip()
        if not normalized:
            raise ToolError("Nedostaje ime korisnika.")
        if len(normalized) > 80:
            normalized = normalized[:80].strip()

        self._result.name = normalized
        logger.info("Face enrollment collected name=%r", normalized)

        # Speak the consent question here rather than returning an instruction
        # for the LLM: a tool-call-only turn would produce no audio and the
        # mandatory consent question would never actually be asked.
        await self.session.say(
            SAY_CONSENT_QUESTION.format(name=normalized),
            allow_interruptions=True,
            add_to_chat_ctx=True,
        )

    @function_tool(
        name="consent_granted",
        description=(
            "Call only after the visitor clearly agrees to have their face remembered. "
            "Captures two reference photos and stores them."
        ),
    )
    async def consent_granted(self) -> None:
        name = self._result.name
        if not name:
            raise ToolError("Ime još nije zabeleženo.")

        logger.info("Face enrollment consent granted for name=%r", name)
        try:
            await self._run_captures(name)
        except FaceApiError as exc:
            logger.warning("Face enrollment unavailable for %r: %s", name, exc)
            self._result.outcome = "unavailable"
            self._result.detail = str(exc)
            await self.session.say(
                SAY_ENROLL_UNAVAILABLE, allow_interruptions=True, add_to_chat_ctx=True
            )
        except Exception as exc:
            logger.exception("Unexpected face enrollment failure for %r", name)
            self._result.outcome = "failed"
            self._result.detail = str(exc)
            await self.session.say(
                SAY_ENROLL_FAILED, allow_interruptions=True, add_to_chat_ctx=True
            )

        if not self.done():
            self.complete(self._result)

    @function_tool(
        name="consent_denied",
        description=(
            "Call when the visitor declines to have their face remembered. "
            "Nothing is stored and the conversation continues normally."
        ),
    )
    async def consent_denied(self) -> None:
        logger.info("Face enrollment consent denied for name=%r", self._result.name)
        self._result.outcome = "declined"
        await self.session.say(
            SAY_CONSENT_DECLINED, allow_interruptions=True, add_to_chat_ctx=True
        )
        if not self.done():
            self.complete(self._result)

    @function_tool(
        name="skip_enrollment",
        description="Call when the visitor will not give a name or wants to move on. Stores nothing.",
    )
    async def skip_enrollment(self) -> None:
        logger.info("Face enrollment skipped before consent")
        self._result.outcome = "skipped"
        await self.session.say(
            SAY_NAME_SKIPPED, allow_interruptions=True, add_to_chat_ctx=True
        )
        if not self.done():
            self.complete(self._result)

    async def _run_captures(self, name: str) -> None:
        """Take the close then far reference shot and report the outcome."""
        for distance, prompt in (
            ("close", SAY_CAPTURE_CLOSE),
            ("far", SAY_CAPTURE_FAR),
        ):
            await self.session.say(prompt, allow_interruptions=False, add_to_chat_ctx=True)
            result = await capture_face_reference(name, distance)
            state = str(result.get("state") or "").lower()
            if state != "captured":
                logger.info("Face enrollment %s capture ended in state=%s", distance, state)
                self._result.outcome = "failed"
                self._result.detail = f"{distance}_capture_{state}"
                await self.session.say(
                    SAY_ENROLL_FAILED, allow_interruptions=True, add_to_chat_ctx=True
                )
                return

            self._result.captured_distances.append(distance)
            metadata = result.get("metadata") or {}
            if metadata.get("face_id"):
                self._result.face_id = str(metadata["face_id"])

        self._result.outcome = "enrolled"
        logger.info(
            "Face enrollment complete name=%r face_id=%s distances=%s",
            name,
            self._result.face_id,
            self._result.captured_distances,
        )
        await self.session.say(
            SAY_ENROLL_SUCCESS.format(name=name), allow_interruptions=True, add_to_chat_ctx=True
        )


def build_known_person_instructions(name: str, canonical_name: str | None = None, aliases: list[str] | None = None) -> str:
    """Extra system-prompt lines so the LLM keeps using the greeted person's name."""
    canonical_name = (canonical_name or name).strip()
    lines = [
            "Recognized visitor context:",
            f"- The person you are talking to is {name}; the vision system recognized their face.",
            f"- You have already greeted {name} by name. Do not greet them again.",
            f"- Address them as {name} naturally during the conversation, without overusing it.",
            "- Do not ask for their name, and do not offer to remember their face; both are already done.",
            "- If they ask you to forget them or delete their data, call `forget_me`.",
            f"- For RAG questions about this person, use the canonical identity: {canonical_name}.",
        ]
    if aliases:
        lines.append(f"- Their known search aliases are: {', '.join(aliases)}.")
    return "\n".join(lines)


def build_enrolled_person_instructions(name: str) -> str:
    """Extra system-prompt lines after a successful fresh enrollment."""
    return "\n".join(
        [
            "Newly enrolled visitor context:",
            f"- The person you are talking to is {name}; they just consented to being remembered.",
            f"- Address them as {name} naturally during the conversation, without overusing it.",
            "- Do not ask for their name again, and do not ask about remembering their face again.",
            "- If they ask you to forget them or delete their data, call `forget_me`.",
        ]
    )


__all__ = [
    "FACE_DETECTION_MAX_AGE_S",
    "FaceApiError",
    "FaceEnrollmentResult",
    "FaceEnrollmentTask",
    "SAY_FORGET_DONE",
    "SAY_FORGET_FAILED",
    "SAY_FORGET_NOT_FOUND",
    "SAY_KNOWN_GREETING",
    "SAY_UNKNOWN_GREETING",
    "VisionIdentitySnapshot",
    "build_enrolled_person_instructions",
    "build_known_person_instructions",
    "capture_face_reference",
    "face_api_json",
    "fetch_vision_identity",
    "forget_face",
]
