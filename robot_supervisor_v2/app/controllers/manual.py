"""
Manual conversation controller.
Manages conversation dispatch/wrap - separate from service lifecycle.
"""

import asyncio
import time
import os
from typing import Optional, Dict, Any
from enum import Enum

from livekit import api

from ..models.conversation import AgentCommandRequest
from ..utils.agent_commands import send_agent_command


GRACEFUL_WRAP_COMMAND = "__WRAP_UP__"


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


GRACEFUL_WRAP_WAIT_SECONDS = _env_float("GRACEFUL_WRAP_WAIT_SECONDS", 6.0)


class EngagementState(str, Enum):
    """Engagement states for conversation lifecycle."""
    IDLE = "idle"
    DISPATCHING = "dispatching"
    ENGAGED = "engaged"
    WRAPPING = "wrapping"
    ERROR = "error"


class ManualController:
    """
    Manual conversation control.

    Responsibilities:
    1. Dispatch agent jobs via LiveKit Agent Service API
    2. Cancel agent jobs (force or graceful)
    3. Track engagement state

    Design Philosophy:
    - Service lifecycle is SEPARATE from conversation lifecycle
    - Services start once and stay running (handled by ServiceManager)
    - This controller only dispatches/cancels jobs (fast operations)
    - Reduces conversation reset time from seconds to milliseconds
    - No queueing: concurrent calls return immediately with status
    """

    def __init__(self, service_manager):
        """
        Initialize manual controller.

        Args:
            service_manager: ServiceManager instance (for status checks)
        """
        self._service_manager = service_manager
        self._engagement_state = EngagementState.IDLE
        self._current_job_id: Optional[str] = None
        self._current_room: Optional[str] = None
        self._dispatch_time: Optional[float] = None
        self._last_error: Optional[str] = None
        self._lock = asyncio.Lock()

        # LiveKit API client (lazy init)
        self._livekit_api: Optional[api.LiveKitAPI] = None

    def _get_livekit_api(self) -> api.LiveKitAPI:
        """Get or create LiveKit API client."""
        if self._livekit_api is None:
            # Get credentials from environment
            url = os.getenv("LIVEKIT_URL", "http://localhost:7880")
            api_key = os.getenv("LIVEKIT_API_KEY", "devkey")
            api_secret = os.getenv("LIVEKIT_API_SECRET", "secret")

            self._livekit_api = api.LiveKitAPI(url, api_key, api_secret)

        return self._livekit_api

    async def dispatch_conversation(
        self,
        room: Optional[str] = None,
        agent_implementation: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Dispatch agent to start a conversation.

        This is a FAST operation - just creates a job dispatch request.
        Services should already be running before calling this.

        Args:
            room: LiveKit room name (default: from LIVEKIT_ROOM env var or "main-room")
            agent_implementation: Local agent implementation to select before dispatch

        Returns:
            dict with status, job_id, and details

        Raises:
            RuntimeError: If services not running or dispatch fails

        Note: No queueing - returns immediately if operation already in progress.
        """
        # Use environment variables for defaults
        if room is None:
            room = os.getenv("LIVEKIT_ROOM", "g1-lab")
        # Check if lock is held (operation in progress)
        if self._lock.locked():
            return {
                "status": "operation_in_progress",
                "state": self._engagement_state,
                "message": "Another operation is already in progress"
            }

        async with self._lock:
            # Check if already engaged
            if self._engagement_state == EngagementState.ENGAGED:
                return {
                    "status": "already_engaged",
                    "state": self._engagement_state,
                    "room": self._current_room,
                    "job_id": self._current_job_id,
                    "uptime_seconds": time.time() - self._dispatch_time if self._dispatch_time else 0
                }

            self._engagement_state = EngagementState.DISPATCHING
            self._last_error = None

            try:
                # 1. Verify required services are running
                await self._verify_services_running()
                await self._release_audio_bridge_remote_playback("before dispatch")
                existing_dispatch = await self._find_existing_agent_dispatch(room)
                if existing_dispatch is not None:
                    existing_job_id = getattr(existing_dispatch, "id", None)
                    self._current_job_id = existing_job_id
                    self._current_room = room
                    self._dispatch_time = self._dispatch_time or time.time()
                    self._engagement_state = EngagementState.ENGAGED
                    return {
                        "status": "already_engaged",
                        "state": self._engagement_state,
                        "room": self._current_room,
                        "job_id": self._current_job_id,
                        "source": "livekit_dispatch",
                        "uptime_seconds": time.time() - self._dispatch_time if self._dispatch_time else 0,
                    }

                # 2. Select local agent implementation if specified
                if agent_implementation:
                    voice_agent = self._service_manager.get("voice-agent")
                    if voice_agent:
                        print(f"Selecting agent implementation: {agent_implementation}")
                        await voice_agent.set_agent(agent_implementation)
                        await asyncio.sleep(3)

                # 3. Dispatch agent job (FAST - just API call)
                print(f"Dispatching agent to room: {room}")
                job_id = await self._dispatch_agent_job(room)

                # 4. Update state
                self._current_job_id = job_id
                self._current_room = room
                self._dispatch_time = time.time()
                self._engagement_state = EngagementState.ENGAGED

                print(f"✓ Conversation dispatched (job: {job_id})")

                return {
                    "status": "success",
                    "state": self._engagement_state,
                    "room": room,
                    "job_id": job_id,
                    "dispatch_time": self._dispatch_time
                }

            except Exception as e:
                self._engagement_state = EngagementState.ERROR
                self._last_error = str(e)
                print(f"✗ Failed to dispatch conversation: {e}")
                raise

    async def wrap_conversation(self, graceful: bool = False) -> Dict[str, Any]:
        """
        Wrap (end) current conversation.

        This is a FAST operation - just cancels the job.
        Services remain running for quick re-dispatch.

        Args:
            graceful: If True, ask the agent to say goodbye and shut itself down
                     If False, force cancel the job immediately

        Returns:
            dict with status and details

        Note: No queueing - returns immediately if operation already in progress.
        Graceful shutdown uses the direct agent command byte stream and keeps
        the controller in wrapping state for GRACEFUL_WRAP_WAIT_SECONDS.
        """
        # Check if lock is held (operation in progress)
        if self._lock.locked():
            return {
                "status": "operation_in_progress",
                "state": self._engagement_state,
                "message": "Another operation is already in progress"
            }

        async with self._lock:
            # Check if already idle
            if self._engagement_state == EngagementState.IDLE:
                return {
                    "status": "already_idle",
                    "state": self._engagement_state
                }

            self._engagement_state = EngagementState.WRAPPING

            try:
                graceful_fallback = False
                if graceful:
                    try:
                        await self._send_graceful_wrap_command()
                        if GRACEFUL_WRAP_WAIT_SECONDS > 0:
                            await asyncio.sleep(GRACEFUL_WRAP_WAIT_SECONDS)
                        if self._current_job_id:
                            await self._delete_agent_job_best_effort(self._current_job_id, "after graceful wrap")
                    except Exception as graceful_exc:
                        graceful_fallback = True
                        print(f"Graceful wrap failed: {graceful_exc}. Falling back to force cancel.")
                        if self._current_job_id:
                            print(f"Canceling job: {self._current_job_id}")
                            await self._cancel_agent_job(self._current_job_id)
                        else:
                            print("No active job to cancel")
                elif self._current_job_id:
                    print(f"Canceling job: {self._current_job_id}")
                    await self._cancel_agent_job(self._current_job_id)
                else:
                    print("No active job to cancel")

                await self._release_audio_bridge_remote_playback("after wrap")

                # Update state
                uptime = time.time() - self._dispatch_time if self._dispatch_time else 0
                self._engagement_state = EngagementState.IDLE
                job_id = self._current_job_id
                room = self._current_room

                self._current_job_id = None
                self._current_room = None
                self._dispatch_time = None

                print(f"✓ Conversation wrapped (duration: {uptime:.1f}s)")

                return {
                    "status": "success",
                    "state": self._engagement_state,
                    "previous_job_id": job_id,
                    "previous_room": room,
                    "duration_seconds": uptime,
                    "graceful": graceful,
                    "graceful_fallback": graceful_fallback,
                }

            except Exception as e:
                # Reset state on error
                self._engagement_state = EngagementState.IDLE
                self._last_error = str(e)
                print(f"✗ Error wrapping conversation: {e}")
                raise

    def get_status(self) -> Dict[str, Any]:
        """
        Get current engagement status.

        Returns:
            dict with state, room, job_id, and other details
        """
        status = {
            "state": self._engagement_state,
            "room": self._current_room,
            "job_id": self._current_job_id,
            "last_error": self._last_error
        }

        if self._dispatch_time:
            status["uptime_seconds"] = time.time() - self._dispatch_time
            status["dispatch_time"] = self._dispatch_time

        return status

    async def _verify_services_running(self):
        """
        Verify required services are running before dispatch.
        Optional services (like gesture-bridge) are checked but not required.

        Raises:
            RuntimeError: If required services are not running
        """
        required_services = ["livekit", "voice-agent", "audio-bridge"]

        for service_name in required_services:
            service = self._service_manager.get(service_name)
            if not service:
                raise RuntimeError(f"Required service '{service_name}' not found")

            status = service.get_status()
            if status.state != "running":
                raise RuntimeError(
                    f"Required service '{service_name}' is not running (state: {status.state}). "
                    f"Start services first before dispatching conversation."
                )

        # Check optional services but don't fail if they're not running
        optional_services = ["gesture-bridge"]
        for service_name in optional_services:
            service = self._service_manager.get(service_name)
            if service:
                status = service.get_status()
                if status.state != "running":
                    print(f"Note: Optional service '{service_name}' is not running (state: {status.state})")

    async def _release_audio_bridge_remote_playback(self, timing: str) -> None:
        """Best-effort reset of bridge-side remote audio selection."""
        audio_bridge = self._service_manager.get("audio-bridge")
        if not audio_bridge or not hasattr(audio_bridge, "release_remote_playback"):
            return

        try:
            await audio_bridge.release_remote_playback()
            print(f"Released audio bridge remote playback {timing}")
        except Exception as exc:
            print(f"Warning: failed to release audio bridge remote playback {timing}: {exc}")

    async def _dispatch_agent_job(self, room: str) -> str:
        """
        Dispatch agent job via LiveKit Agent Service API.

        Args:
            room: Room name to dispatch agent to

        Returns:
            Job ID

        Raises:
            RuntimeError: If dispatch fails
        """
        try:
            lk_api = self._get_livekit_api()

            dispatch_agent_name = os.environ["LIVEKIT_AGENT_NAME"]

            print(f"DEBUG: Dispatching agent '{dispatch_agent_name}' to room '{room}'")

            # Create an explicit room-scoped agent dispatch. The dispatch name
            # must match the agent_name registered by the running AgentServer.
            request = api.CreateAgentDispatchRequest(
                room=room,
                agent_name=dispatch_agent_name
            )

            dispatch_started_at = time.perf_counter()

            # Dispatch the agent job
            response = await lk_api.agent_dispatch.create_dispatch(request)
            print(
                "INFO: create_dispatch returned in "
                f"{time.perf_counter() - dispatch_started_at:.3f}s"
            )

            # Response is an AgentDispatch object with id, room, agent_name, state, metadata
            job_id = response.id
            print(f"DEBUG: Dispatched job {job_id} to room {response.room} with agent {response.agent_name}")

            return job_id

        except Exception as e:
            import traceback
            error_details = traceback.format_exc()
            print(f"ERROR: Failed to dispatch agent job: {e}")
            print(f"ERROR: Traceback:\n{error_details}")
            raise RuntimeError(f"Failed to dispatch agent job: {type(e).__name__}: {e}")

    async def _find_existing_agent_dispatch(self, room: str):
        """Return an existing room dispatch for this agent, if LiveKit still has one."""
        try:
            lk_api = self._get_livekit_api()
            dispatch_agent_name = os.environ["LIVEKIT_AGENT_NAME"]
            dispatches = await lk_api.agent_dispatch.list_dispatch(room)
            for dispatch in dispatches:
                if getattr(dispatch, "agent_name", None) == dispatch_agent_name:
                    print(
                        "INFO: Existing LiveKit dispatch found "
                        f"job={getattr(dispatch, 'id', None)} room={room} agent={dispatch_agent_name}"
                    )
                    return dispatch
        except Exception as exc:
            print(f"Warning: failed to list LiveKit dispatches for room {room}: {exc}")
        return None

    async def _send_graceful_wrap_command(self) -> None:
        """Ask the active agent to say goodbye and shut down itself."""
        if not self._current_room:
            raise RuntimeError("Cannot gracefully wrap conversation: no room information available")

        print(f"Sending graceful wrap command to room: {self._current_room}")
        await send_agent_command(
            AgentCommandRequest(
                text=GRACEFUL_WRAP_COMMAND,
                plain_text=True,
                room=self._current_room,
                identity="supervisor-graceful-wrap",
                name="Supervisor Graceful Wrap",
            ),
            room=self._current_room,
        )

    async def _cancel_agent_job(self, job_id: str):
        """
        Force cancel an agent job.

        Args:
            job_id: Job ID to cancel

        Raises:
            RuntimeError: If cancellation fails
        """
        try:
            lk_api = self._get_livekit_api()

            # Delete the agent dispatch (force termination)
            # Requires both dispatch_id and room_name
            if not self._current_room:
                raise RuntimeError("Cannot cancel job: no room information available")

            await lk_api.agent_dispatch.delete_dispatch(job_id, self._current_room)
            print(f"DEBUG: Deleted dispatch {job_id} from room {self._current_room}")

        except Exception as e:
            import traceback
            error_details = traceback.format_exc()
            print(f"ERROR: Failed to cancel agent job: {e}")
            print(f"ERROR: Traceback:\n{error_details}")
            raise RuntimeError(f"Failed to cancel agent job: {type(e).__name__}: {e}")

    async def _delete_agent_job_best_effort(self, job_id: str, timing: str) -> None:
        try:
            await self._cancel_agent_job(job_id)
        except Exception as exc:
            print(f"Warning: failed to delete dispatch {timing}: {exc}")
