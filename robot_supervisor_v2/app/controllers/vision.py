"""
Vision-driven conversation controller.
Wraps ManualController with person-presence events from the vision service.
"""

import asyncio
import logging
import os
import time
import uuid
from typing import Any, Optional

from .manual import ManualController

CARD_CAPTURE_IDLE = "idle"
CARD_CAPTURE_ACTIVE_STATES = {"pending", "running"}
DEFAULT_CARD_CAPTURE_TIMEOUT_S = 15.0
CARD_CAPTURE_REACQUIRE_TIMEOUT_REASON = "card_capture_reacquire_timeout"

FACE_CAPTURE_IDLE = "idle"
FACE_CAPTURE_ACTIVE_STATES = {"pending", "running"}
DEFAULT_FACE_CAPTURE_TIMEOUT_S = 20.0

FACE_FORGET_IDLE = "idle"
logger = logging.getLogger(__name__)


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


VISION_PERSON_LEFT_CONFIRM_S = max(
    0.0,
    _env_float("VISION_PERSON_LEFT_CONFIRM_S", 2.0),
)


class VisionController:
    """
    Convert vision presence events into manual conversation lifecycle actions.

    The vision service sends person_detected/person_left events. This controller
    keeps those events idempotent so repeated detections do not dispatch multiple
    conversations and repeated leaves do not repeatedly wrap.
    """

    def __init__(
        self,
        manual_controller: ManualController,
        service_manager,
        service_name: str = "vision-controller",
    ):
        self._manual_controller = manual_controller
        self._service_manager = service_manager
        self._service_name = service_name
        self._enabled_override: Optional[bool] = None
        self._person_present = False
        self._active_track_id: Optional[str] = None
        self._last_detected_at: Optional[float] = None
        self._last_left_at: Optional[float] = None
        self._last_identity: Optional[dict[str, Any]] = None
        self._last_identity_at: Optional[float] = None
        self._event_sequence = 0
        self._pending_events: dict[int, dict[str, Any]] = {}
        self._card_capture: dict[str, Any] = self._new_idle_card_capture_state()
        self._suppress_person_left_until_card_reacquire = False
        self._card_capture_hold_request_id: Optional[str] = None
        self._face_capture: dict[str, Any] = self._new_idle_face_capture_state()
        self._face_forget: dict[str, Any] = self._new_idle_face_forget_state()
        self._lock = asyncio.Lock()

    def _new_idle_card_capture_state(self) -> dict[str, Any]:
        return {
            "state": CARD_CAPTURE_IDLE,
            "active": False,
            "request_id": None,
            "target_type": None,
            "source": None,
            "requested_at": None,
            "expires_at": None,
            "completed_at": None,
            "result": None,
            "metadata": {},
        }

    def _new_idle_face_capture_state(self) -> dict[str, Any]:
        return {
            "state": FACE_CAPTURE_IDLE,
            "active": False,
            "request_id": None,
            "name": None,
            "distance": None,
            "requested_at": None,
            "expires_at": None,
            "completed_at": None,
            "result": None,
            "metadata": {},
        }

    def _new_idle_face_forget_state(self) -> dict[str, Any]:
        return {
            "state": FACE_FORGET_IDLE,
            "request_id": None,
            "face_id": None,
            "name": None,
            "completed_at": None,
            "result": None,
        }

    def _service_status(self) -> dict[str, Any]:
        service = self._service_manager.get(self._service_name)
        if not service:
            return {
                "service_name": self._service_name,
                "configured": False,
                "running": False,
                "state": "unregistered",
                "pid": None,
            }

        status = service.get_status()
        state = status.state.value if hasattr(status.state, "value") else str(status.state)
        return {
            "service_name": self._service_name,
            "configured": True,
            "running": state == "running",
            "state": state,
            "pid": status.pid,
        }

    def _resolve_enabled(self, service_running: bool) -> bool:
        if self._enabled_override is not None:
            return self._enabled_override
        return service_running

    def _id_scanning_enabled(self) -> bool:
        service = self._service_manager.get(self._service_name)
        get_config = getattr(service, "get_config", None)
        if not callable(get_config):
            return False
        return bool(get_config().get("enable_id_scanning", False))

    def _face_recognition_enabled(self) -> bool:
        """Operator toggle for the face-recognition feature, separate from the
        general vision on/off switch and live-pollable without a restart (see
        VisionControllerService.get_config_parameters / DETECTOR_RESTART_CONFIG_KEYS)."""
        service = self._service_manager.get(self._service_name)
        get_config = getattr(service, "get_config", None)
        if not callable(get_config):
            return False
        return bool(get_config().get("enable_face_recognition", False))

    def set_enabled(self, enabled: bool) -> dict[str, Any]:
        """Set the operator vision toggle."""
        self._enabled_override = enabled
        if not enabled:
            self._person_present = False
            self._active_track_id = None
            self._pending_events.clear()
            self._cancel_card_capture_state(reason="vision_disabled")
            self._cancel_face_capture_state(reason="vision_disabled")
        return self.get_status()

    def is_active(self) -> bool:
        status = self.get_status()
        return bool(status["active"])

    def _expire_card_capture_if_due(self, now: Optional[float] = None) -> None:
        if self._card_capture.get("state") not in CARD_CAPTURE_ACTIVE_STATES:
            return

        expires_at = self._card_capture.get("expires_at")
        if expires_at is None:
            return

        now = time.time() if now is None else now
        if now < float(expires_at):
            return

        request_id = self._card_capture.get("request_id")
        self._card_capture.update(
            {
                "state": "expired",
                "active": False,
                "completed_at": now,
                "result": {
                    "status": "expired",
                    "timestamp": now,
                    "metadata": {"reason": "timeout"},
                },
                "metadata": {"reason": "timeout"},
            }
        )
        self._clear_card_capture_reacquire_hold(
            reason="card_capture_expired",
            request_id=request_id,
        )

    def _cancel_card_capture_state(self, *, reason: str) -> None:
        if self._card_capture.get("state") not in CARD_CAPTURE_ACTIVE_STATES:
            return

        now = time.time()
        request_id = self._card_capture.get("request_id")
        logger.info(
            "Cancelling card capture request_id=%s reason=%s",
            request_id,
            reason,
        )
        self._card_capture.update(
            {
                "state": "cancelled",
                "active": False,
                "completed_at": now,
                "result": {
                    "status": "cancelled",
                    "timestamp": now,
                    "metadata": {"reason": reason},
                },
                "metadata": {"reason": reason},
            }
        )
        self._clear_card_capture_reacquire_hold(
            reason=f"card_capture_{reason}",
            request_id=request_id,
        )

    def _card_capture_leave_suppression_reason(self) -> Optional[str]:
        self._expire_card_capture_if_due()
        if not self._person_present:
            return None
        if self._card_capture.get("state") in CARD_CAPTURE_ACTIVE_STATES:
            return "suppressed_card_capture_active"
        if self._suppress_person_left_until_card_reacquire:
            return "suppressed_card_capture_reacquire_hold"
        return None

    def _card_capture_dispatch_paused(self) -> bool:
        self._expire_card_capture_if_due()
        return self._card_capture.get("state") in CARD_CAPTURE_ACTIVE_STATES

    def _clear_card_capture_reacquire_hold(
        self,
        *,
        reason: str,
        request_id: Optional[str] = None,
    ) -> None:
        if not self._suppress_person_left_until_card_reacquire:
            return
        logger.info(
            "Clearing card capture person_left suppression reason=%s request_id=%s",
            reason,
            request_id or self._card_capture_hold_request_id,
        )
        self._suppress_person_left_until_card_reacquire = False
        self._card_capture_hold_request_id = None

    def _card_capture_public_state(self, *, include_result: bool = True) -> dict[str, Any]:
        self._expire_card_capture_if_due()
        state = dict(self._card_capture)
        if not include_result:
            result = state.get("result")
            if isinstance(result, dict):
                state["result"] = {
                    key: value
                    for key, value in result.items()
                    if key != "metadata"
                }
            metadata = state.get("metadata")
            if isinstance(metadata, dict) and "image_jpeg_base64" in metadata:
                state["metadata"] = {
                    key: value
                    for key, value in metadata.items()
                    if key != "image_jpeg_base64"
                }
        return state

    def _expire_face_capture_if_due(self, now: Optional[float] = None) -> None:
        if self._face_capture.get("state") not in FACE_CAPTURE_ACTIVE_STATES:
            return

        expires_at = self._face_capture.get("expires_at")
        if expires_at is None:
            return

        now = time.time() if now is None else now
        if now < float(expires_at):
            return

        self._face_capture.update(
            {
                "state": "expired",
                "active": False,
                "completed_at": now,
                "result": {
                    "status": "expired",
                    "timestamp": now,
                    "metadata": {"reason": "timeout"},
                },
                "metadata": {"reason": "timeout"},
            }
        )

    def _cancel_face_capture_state(self, *, reason: str) -> None:
        if self._face_capture.get("state") not in FACE_CAPTURE_ACTIVE_STATES:
            return

        now = time.time()
        request_id = self._face_capture.get("request_id")
        logger.info("Cancelling face capture request_id=%s reason=%s", request_id, reason)
        self._face_capture.update(
            {
                "state": "cancelled",
                "active": False,
                "completed_at": now,
                "result": {
                    "status": "cancelled",
                    "timestamp": now,
                    "metadata": {"reason": reason},
                },
                "metadata": {"reason": reason},
            }
        )

    def _face_capture_dispatch_paused(self) -> bool:
        self._expire_face_capture_if_due()
        return self._face_capture.get("state") in FACE_CAPTURE_ACTIVE_STATES

    def _face_capture_leave_suppression_reason(self) -> Optional[str]:
        self._expire_face_capture_if_due()
        if not self._person_present:
            return None
        if self._face_capture.get("state") in FACE_CAPTURE_ACTIVE_STATES:
            return "suppressed_face_capture_active"
        return None

    def _face_capture_public_state(self, *, include_result: bool = True) -> dict[str, Any]:
        self._expire_face_capture_if_due()
        state = dict(self._face_capture)
        if not include_result:
            result = state.get("result")
            if isinstance(result, dict):
                state["result"] = {
                    key: value
                    for key, value in result.items()
                    if key != "metadata"
                }
            metadata = state.get("metadata")
            if isinstance(metadata, dict) and "face_jpeg_base64" in metadata:
                state["metadata"] = {
                    key: value
                    for key, value in metadata.items()
                    if key != "face_jpeg_base64"
                }
        return state

    def _new_face_forget_completed_state(
        self,
        *,
        request_id: str,
        status: str,
        timestamp: Optional[float],
        face_id: Optional[str],
        name: Optional[str],
    ) -> dict[str, Any]:
        completed_at = timestamp if timestamp is not None else time.time()
        return {
            "state": status,
            "request_id": request_id,
            "face_id": face_id,
            "name": name,
            "completed_at": completed_at,
            "result": {"status": status, "timestamp": completed_at},
        }

    async def request_card_capture(
        self,
        *,
        timeout_s: Optional[float] = None,
        target_type: Optional[str] = None,
        source: Optional[str] = None,
    ) -> dict[str, Any]:
        """Start a request-gated card capture job for the vision worker."""
        async with self._lock:
            self._expire_card_capture_if_due()
            if self._card_capture.get("state") in CARD_CAPTURE_ACTIVE_STATES:
                return {
                    "status": "already_active",
                    "card_capture": self._card_capture_public_state(),
                }

            now = time.time()
            effective_timeout_s = (
                DEFAULT_CARD_CAPTURE_TIMEOUT_S
                if timeout_s is None
                else max(float(timeout_s), 0.1)
            )
            request_id = uuid.uuid4().hex
            self._card_capture = {
                "state": "pending",
                "active": True,
                "request_id": request_id,
                "target_type": target_type,
                "source": source,
                "requested_at": now,
                "expires_at": now + effective_timeout_s,
                "completed_at": None,
                "result": None,
                "metadata": {},
            }
            dropped_left_count = self._drop_pending_left_events()
            if dropped_left_count:
                logger.info(
                    "Cleared %s pending person_left event(s) when card capture started request_id=%s",
                    dropped_left_count,
                    request_id,
                )
            if self._person_present:
                self._suppress_person_left_until_card_reacquire = True
                self._card_capture_hold_request_id = request_id
            return self._card_capture_public_state()

    async def get_card_capture(self, request_id: str) -> dict[str, Any]:
        """Return a card capture request by id."""
        async with self._lock:
            state = self._card_capture_public_state()
            if state.get("request_id") != request_id:
                return {"status": "not_found", "request_id": request_id}
            return state

    async def cancel_card_capture(self, request_id: str) -> dict[str, Any]:
        """Cancel a pending/running card capture request."""
        async with self._lock:
            self._expire_card_capture_if_due()
            if self._card_capture.get("request_id") != request_id:
                return {"status": "not_found", "request_id": request_id}
            if self._card_capture.get("state") not in CARD_CAPTURE_ACTIVE_STATES:
                return self._card_capture_public_state()

            now = time.time()
            self._card_capture.update(
                {
                    "state": "cancelled",
                    "active": False,
                    "completed_at": now,
                    "result": {
                        "status": "cancelled",
                        "timestamp": now,
                        "metadata": {"reason": "requested"},
                    },
                    "metadata": {"reason": "requested"},
                }
            )
            self._clear_card_capture_reacquire_hold(
                reason="card_capture_cancelled",
                request_id=request_id,
            )
            return self._card_capture_public_state()

    async def complete_card_capture(
        self,
        *,
        request_id: str,
        status: str,
        timestamp: Optional[float] = None,
        metadata: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        """Accept a card capture result from the vision worker."""
        async with self._lock:
            self._expire_card_capture_if_due()
            if self._card_capture.get("request_id") != request_id:
                return {"status": "not_found", "request_id": request_id}
            if self._card_capture.get("state") not in CARD_CAPTURE_ACTIVE_STATES:
                return {
                    "status": "not_active",
                    "card_capture": self._card_capture_public_state(),
                }

            normalized_status = status.strip().lower()
            if normalized_status == "success":
                normalized_status = "captured"
            if normalized_status not in {"captured", "failed", "cancelled", "expired"}:
                normalized_status = "failed"

            completed_at = timestamp if timestamp is not None else time.time()
            result = {
                "status": normalized_status,
                "timestamp": completed_at,
                "metadata": metadata or {},
            }
            self._card_capture.update(
                {
                    "state": normalized_status,
                    "active": False,
                    "completed_at": completed_at,
                    "result": result,
                    "metadata": metadata or {},
                }
            )
            self._clear_card_capture_reacquire_hold(
                reason=f"card_capture_{normalized_status}",
                request_id=request_id,
            )
            return self._card_capture_public_state()

    async def request_face_capture(
        self,
        *,
        name: str,
        distance: str,
        timeout_s: Optional[float] = None,
    ) -> dict[str, Any]:
        """Start one distance-gated enrollment capture job for the vision worker."""
        async with self._lock:
            self._expire_face_capture_if_due()
            if self._face_capture.get("state") in FACE_CAPTURE_ACTIVE_STATES:
                return {
                    "status": "already_active",
                    "face_capture": self._face_capture_public_state(),
                }

            now = time.time()
            effective_timeout_s = (
                DEFAULT_FACE_CAPTURE_TIMEOUT_S
                if timeout_s is None
                else max(float(timeout_s), 0.1)
            )
            request_id = uuid.uuid4().hex
            self._face_capture = {
                "state": "pending",
                "active": True,
                "request_id": request_id,
                "name": name,
                "distance": distance,
                "requested_at": now,
                "expires_at": now + effective_timeout_s,
                "completed_at": None,
                "result": None,
                "metadata": {},
            }
            return self._face_capture_public_state()

    async def get_face_capture(self, request_id: str) -> dict[str, Any]:
        """Return a face capture request by id."""
        async with self._lock:
            state = self._face_capture_public_state()
            if state.get("request_id") != request_id:
                return {"status": "not_found", "request_id": request_id}
            return state

    async def cancel_face_capture(self, request_id: str) -> dict[str, Any]:
        """Cancel a pending/running face capture request."""
        async with self._lock:
            self._expire_face_capture_if_due()
            if self._face_capture.get("request_id") != request_id:
                return {"status": "not_found", "request_id": request_id}
            if self._face_capture.get("state") not in FACE_CAPTURE_ACTIVE_STATES:
                return self._face_capture_public_state()

            now = time.time()
            self._face_capture.update(
                {
                    "state": "cancelled",
                    "active": False,
                    "completed_at": now,
                    "result": {
                        "status": "cancelled",
                        "timestamp": now,
                        "metadata": {"reason": "requested"},
                    },
                    "metadata": {"reason": "requested"},
                }
            )
            return self._face_capture_public_state()

    async def complete_face_capture(
        self,
        *,
        request_id: str,
        status: str,
        timestamp: Optional[float] = None,
        metadata: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        """Accept a face capture result from the vision worker.

        metadata never contains a raw embedding - the vision worker pops it
        before posting this result, using it locally against its own store.
        """
        async with self._lock:
            self._expire_face_capture_if_due()
            if self._face_capture.get("request_id") != request_id:
                return {"status": "not_found", "request_id": request_id}
            if self._face_capture.get("state") not in FACE_CAPTURE_ACTIVE_STATES:
                return {
                    "status": "not_active",
                    "face_capture": self._face_capture_public_state(),
                }

            normalized_status = status.strip().lower()
            if normalized_status == "success":
                normalized_status = "captured"
            if normalized_status not in {"captured", "failed", "cancelled", "expired"}:
                normalized_status = "failed"

            completed_at = timestamp if timestamp is not None else time.time()
            result = {
                "status": normalized_status,
                "timestamp": completed_at,
                "metadata": metadata or {},
            }
            self._face_capture.update(
                {
                    "state": normalized_status,
                    "active": False,
                    "completed_at": completed_at,
                    "result": result,
                    "metadata": metadata or {},
                }
            )
            return self._face_capture_public_state()

    async def request_face_forget(
        self,
        *,
        face_id: Optional[str] = None,
        name: Optional[str] = None,
    ) -> dict[str, Any]:
        """Ask the vision worker to delete a stored face by id or name."""
        if not face_id and not name:
            raise ValueError("Provide face_id or name to forget a face")

        async with self._lock:
            request_id = uuid.uuid4().hex
            self._face_forget = {
                "state": "pending",
                "request_id": request_id,
                "face_id": face_id,
                "name": name,
                "completed_at": None,
                "result": None,
            }
            return dict(self._face_forget)

    async def complete_face_forget(
        self,
        *,
        request_id: str,
        status: str,
        timestamp: Optional[float] = None,
    ) -> dict[str, Any]:
        """Accept a face forget result from the vision worker."""
        async with self._lock:
            if self._face_forget.get("request_id") != request_id:
                return {"status": "not_found", "request_id": request_id}
            if self._face_forget.get("state") != "pending":
                return {"status": "not_active", "face_forget": dict(self._face_forget)}

            self._face_forget = self._new_face_forget_completed_state(
                request_id=request_id,
                status=status,
                timestamp=timestamp,
                face_id=self._face_forget.get("face_id"),
                name=self._face_forget.get("name"),
            )
            return dict(self._face_forget)

    def _next_event_sequence(self) -> int:
        self._event_sequence += 1
        return self._event_sequence

    def _person_left_process_delay_s(self, now: float, *, reason: Optional[str] = None) -> float:
        if reason == CARD_CAPTURE_REACQUIRE_TIMEOUT_REASON:
            return 0.0
        return VISION_PERSON_LEFT_CONFIRM_S

    def _update_identity(self, identity: Optional[dict[str, Any]], timestamp: Optional[float]) -> None:
        if identity is None:
            return
        self._last_identity = identity
        self._last_identity_at = timestamp if timestamp is not None else time.time()

    async def queue_person_detected(
        self,
        *,
        track_id: Optional[int | str] = None,
        timestamp: Optional[float] = None,
        identity: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        """Accept a person-detected event for background processing."""
        async with self._lock:
            status = self.get_status()
            if not status["active"]:
                return {
                    "status": "vision_inactive",
                    **status,
                }

            if self._person_present:
                # Same person, but the vision worker may have just resolved (or
                # re-resolved) identity for the already-dispatched conversation -
                # update it without dispatching a second conversation.
                self._update_identity(identity, timestamp)
                dropped_left_count = self._drop_pending_left_events()
                if dropped_left_count:
                    logger.info(
                        "Cleared %s pending person_left event(s) after person reacquisition track_id=%s",
                        dropped_left_count,
                        self._active_track_id,
                    )
                return {
                    "status": "already_present",
                    "person_present": True,
                    "track_id": self._active_track_id,
                    "last_detected_at": self._last_detected_at,
                    "conversation": self._manual_controller.get_status(),
                }

            self._drop_pending_left_events()
            sequence = self._next_event_sequence()
            self._pending_events[sequence] = {
                "type": "person_detected",
                "track_id": track_id,
                "timestamp": timestamp,
                "identity": identity,
            }
            return {
                "status": "accepted",
                "event_sequence": sequence,
                "track_id": str(track_id) if track_id is not None else None,
                "timestamp": timestamp,
            }

    async def complete_person_detected(self, event_sequence: int) -> dict[str, Any]:
        """Process an accepted person-detected event."""
        event = self._pending_events.pop(event_sequence, None)
        if not event or event.get("type") != "person_detected":
            return {
                "status": "event_not_found",
                "event_sequence": event_sequence,
                "conversation": self._manual_controller.get_status(),
            }

        result = await self.person_detected(
            track_id=event.get("track_id"),
            timestamp=event.get("timestamp"),
            identity=event.get("identity"),
        )
        result["event_sequence"] = event_sequence
        return result

    async def queue_person_left(
        self,
        *,
        timestamp: Optional[float] = None,
        reason: Optional[str] = None,
    ) -> dict[str, Any]:
        """Accept a person-left event for background processing."""
        async with self._lock:
            allow_card_capture_timeout = reason == CARD_CAPTURE_REACQUIRE_TIMEOUT_REASON
            suppression_reason = (
                self._card_capture_leave_suppression_reason()
                or self._face_capture_leave_suppression_reason()
            )
            if suppression_reason and not allow_card_capture_timeout:
                logger.info(
                    "Suppressing person_left due to card capture hold request_id=%s state=%s reason=%s",
                    self._card_capture.get("request_id") or self._card_capture_hold_request_id,
                    self._card_capture.get("state"),
                    suppression_reason,
                )
                return {
                    "status": suppression_reason,
                    "person_present": True,
                    "track_id": self._active_track_id,
                    "card_capture": self._card_capture_public_state(),
                    "conversation": self._manual_controller.get_status(),
                }
            if suppression_reason and allow_card_capture_timeout:
                self._clear_card_capture_reacquire_hold(
                    reason=reason,
                    request_id=self._card_capture.get("request_id")
                    or self._card_capture_hold_request_id,
                )

            status = self.get_status()
            if not status["active"]:
                return {
                    "status": "vision_inactive",
                    **status,
                }

            if not self._person_present:
                self._drop_pending_detect_events()
                return {
                    "status": "already_absent",
                    "person_present": False,
                    "last_left_at": self._last_left_at,
                    "conversation": self._manual_controller.get_status(),
                }

            sequence = self._next_event_sequence()
            previous_track_id = self._active_track_id
            now = time.time()
            process_delay_s = self._person_left_process_delay_s(now, reason=reason)
            self._pending_events[sequence] = {
                "type": "person_left",
                "timestamp": timestamp,
                "previous_track_id": previous_track_id,
                "queued_at": now,
                "process_after": now + process_delay_s,
                "reason": reason,
            }
            if process_delay_s > 0:
                logger.info(
                    "Delaying person_left processing by %.2fs previous_track_id=%s",
                    process_delay_s,
                    previous_track_id,
                )
            return {
                "status": "accepted",
                "event_sequence": sequence,
                "previous_track_id": previous_track_id,
                "timestamp": timestamp,
                "process_delay_s": process_delay_s,
            }

    async def complete_person_left(
        self,
        event_sequence: int,
        *,
        previous_track_id: Optional[str] = None,
    ) -> dict[str, Any]:
        """Process an accepted person-left event."""
        event = self._pending_events.pop(event_sequence, None)
        if not event or event.get("type") != "person_left":
            return {
                "status": "event_not_found",
                "event_sequence": event_sequence,
                "previous_track_id": previous_track_id,
                "conversation": self._manual_controller.get_status(),
            }

        result = await self.person_left(
            timestamp=event.get("timestamp"),
            reason=event.get("reason"),
        )
        result["event_sequence"] = event_sequence
        result.setdefault("previous_track_id", previous_track_id)
        return result

    def _drop_pending_detect_events(self) -> None:
        stale_sequences = [
            sequence
            for sequence, event in self._pending_events.items()
            if event.get("type") == "person_detected"
        ]
        for sequence in stale_sequences:
            self._pending_events.pop(sequence, None)

    def _drop_pending_left_events(self) -> int:
        stale_sequences = [
            sequence
            for sequence, event in self._pending_events.items()
            if event.get("type") == "person_left"
        ]
        for sequence in stale_sequences:
            self._pending_events.pop(sequence, None)
        return len(stale_sequences)

    async def person_detected(
        self,
        *,
        track_id: Optional[int | str] = None,
        timestamp: Optional[float] = None,
        identity: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        """Handle a person-detected event by dispatching a conversation."""
        async with self._lock:
            status = self.get_status()
            if not status["active"]:
                return {
                    "status": "vision_inactive",
                    **status,
                }

            now = timestamp if timestamp is not None else time.time()
            normalized_track_id = str(track_id) if track_id is not None else None

            if self._person_present:
                self._update_identity(identity, timestamp)
                return {
                    "status": "already_present",
                    "person_present": True,
                    "track_id": self._active_track_id,
                    "last_detected_at": self._last_detected_at,
                    "conversation": self._manual_controller.get_status(),
                }

            self._person_present = True
            self._active_track_id = normalized_track_id
            self._last_detected_at = now
            self._last_identity = identity
            self._last_identity_at = now

            try:
                result = await self._manual_controller.dispatch_conversation()
            except Exception:
                self._person_present = False
                self._active_track_id = None
                raise

            if result.get("status") == "operation_in_progress":
                self._person_present = False
                self._active_track_id = None

            return {
                "status": "person_detected",
                "person_present": self._person_present,
                "track_id": self._active_track_id,
                "detected_at": now,
                "conversation": result,
            }

    async def person_left(
        self,
        *,
        timestamp: Optional[float] = None,
        reason: Optional[str] = None,
    ) -> dict[str, Any]:
        """Handle a person-left event by wrapping the active conversation."""
        async with self._lock:
            now = timestamp if timestamp is not None else time.time()
            previous_track_id = self._active_track_id

            allow_card_capture_timeout = reason == CARD_CAPTURE_REACQUIRE_TIMEOUT_REASON
            suppression_reason = (
                self._card_capture_leave_suppression_reason()
                or self._face_capture_leave_suppression_reason()
            )
            if suppression_reason and not allow_card_capture_timeout:
                logger.info(
                    "Suppressing direct person_left due to card capture hold request_id=%s state=%s reason=%s",
                    self._card_capture.get("request_id") or self._card_capture_hold_request_id,
                    self._card_capture.get("state"),
                    suppression_reason,
                )
                return {
                    "status": suppression_reason,
                    "person_present": True,
                    "track_id": self._active_track_id,
                    "card_capture": self._card_capture_public_state(),
                    "conversation": self._manual_controller.get_status(),
                }
            if suppression_reason and allow_card_capture_timeout:
                self._clear_card_capture_reacquire_hold(
                    reason=reason,
                    request_id=self._card_capture.get("request_id")
                    or self._card_capture_hold_request_id,
                )

            status = self.get_status()
            if not status["active"]:
                return {
                    "status": "vision_inactive",
                    **status,
                }

            if not self._person_present:
                return {
                    "status": "already_absent",
                    "person_present": False,
                    "last_left_at": self._last_left_at,
                    "conversation": self._manual_controller.get_status(),
                }

            self._person_present = False
            self._active_track_id = None
            self._last_left_at = now
            self._last_identity = None
            self._last_identity_at = None

            result = await self._manual_controller.wrap_conversation(graceful=True)
            if result.get("status") == "operation_in_progress":
                self._person_present = True
                self._active_track_id = previous_track_id

            return {
                "status": "person_left",
                "person_present": self._person_present,
                "previous_track_id": previous_track_id,
                "left_at": now,
                "conversation": result,
            }

    def get_status(self) -> dict[str, Any]:
        """Return current vision presence and wrapped conversation status."""
        service = self._service_status()
        service_running = bool(service["running"])
        enabled = self._resolve_enabled(service_running)
        vision_active = enabled and service_running
        card_capture_config_enabled = self._id_scanning_enabled()
        face_recognition_config_enabled = self._face_recognition_enabled()
        if not vision_active:
            self._cancel_card_capture_state(reason="vision_inactive")
            self._cancel_face_capture_state(reason="vision_inactive")
        elif not card_capture_config_enabled:
            self._cancel_card_capture_state(reason="id_scanning_disabled")
        if not vision_active or not face_recognition_config_enabled:
            self._cancel_face_capture_state(
                reason="vision_inactive" if not vision_active else "face_recognition_disabled"
            )
        capture_dispatch_paused = (
            vision_active
            and card_capture_config_enabled
            and self._card_capture_dispatch_paused()
        ) or (
            vision_active
            and face_recognition_config_enabled
            and self._face_capture_dispatch_paused()
        )
        dispatch_active = vision_active and not capture_dispatch_paused
        card_capture_state = self._card_capture_public_state(include_result=False)
        card_capture_enabled = vision_active and card_capture_config_enabled
        face_capture_state = self._face_capture_public_state(include_result=False)
        face_capture_enabled = vision_active and face_recognition_config_enabled

        return {
            "enabled": enabled,
            "active": vision_active,
            "service": service,
            "enabled_override": self._enabled_override,
            "person_present": self._person_present,
            "track_id": self._active_track_id,
            "last_detected_at": self._last_detected_at,
            "last_left_at": self._last_left_at,
            "identity": self._last_identity,
            "identity_at": self._last_identity_at,
            "card_capture_person_left_hold": self._suppress_person_left_until_card_reacquire,
            "card_capture_dispatch_paused": capture_dispatch_paused,
            "features": {
                "dispatch": {
                    "enabled": vision_active,
                    "active": dispatch_active,
                    "paused": capture_dispatch_paused,
                },
                "card_capture": {
                    **card_capture_state,
                    "enabled": card_capture_enabled,
                },
                "face_capture": {
                    **face_capture_state,
                    "enabled": face_capture_enabled,
                },
                "face_forget": dict(self._face_forget),
                "face_recognition": {
                    "enabled": face_recognition_config_enabled,
                },
            },
            "conversation": self._manual_controller.get_status(),
        }
