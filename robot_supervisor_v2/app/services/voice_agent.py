"""
Voice Agent service implementation.
Manages the LiveKit voice agent with directory-based agent discovery.
"""

import subprocess
import asyncio
import os
import sys
import glob
import logging
from typing import Dict, Any, Optional, List
from pathlib import Path
from .base import BaseService, ServiceState, ConfigParameter
from ..runtime_environment import (
    EnvironmentConfigError,
    build_child_environment_updates,
)
from ..speech_config import get_active_tts_tag, get_active_voice
from ..supervisor_config import SERVICE_ROBOT_CONTEXT_KEY
from robot_services.gestures import (
    DEFAULT_GESTURE_SAFETY_POOL,
    normalize_gesture_safety_pool,
    resolve_gesture_catalog_id_for_robot_context,
)

logger = logging.getLogger(__name__)


def _env_enabled(name: str) -> bool:
    return (os.getenv(name) or "").strip().lower() in {"1", "true", "yes", "on"}


def resolve_voice_agent_python() -> str:
    """Select the isolated interpreter only for an explicitly enabled canary."""
    if not _env_enabled("VOICE_CANARY_ENABLED"):
        return sys.executable

    repo_root = Path(__file__).resolve().parents[3]
    configured = (os.getenv("VOICE_AGENT_PYTHON") or "").strip()
    candidate = Path(configured) if configured else repo_root / ".venv-voice-canary" / "bin" / "python"
    if not candidate.is_absolute():
        candidate = repo_root / candidate
    # Keep the venv entry point itself.  Resolving its symlink selects the base
    # CPython binary and makes Python lose the venv's site-packages at startup.
    candidate = Path(os.path.abspath(candidate))
    if not candidate.is_file() or not os.access(candidate, os.X_OK):
        raise RuntimeError(
            "VOICE_CANARY_ENABLED requires an executable VOICE_AGENT_PYTHON; "
            f"resolved path: {candidate}"
        )
    return str(candidate)


class AgentInfo:
    """Information about a discovered agent."""
    def __init__(self, name: str, path: str, module_name: str):
        self.name = name
        self.path = path
        self.module_name = module_name

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "path": self.path,
            "module": self.module_name
        }


class VoiceAgentService(BaseService):
    """Voice agent service with directory-based agent discovery."""

    def __init__(self, name: str, config: Dict[str, Any]):
        service_config = dict(config)
        self._robot_context = service_config.pop(SERVICE_ROBOT_CONTEXT_KEY, None)
        super().__init__(
            name=name,
            display_name=service_config.get("display_name", "Voice Agent"),
            config=service_config
        )
        self._log_file = f"robot_supervisor_v2/logs/{name}.log"
        self._log_handle = None
        self._available_agents: List[AgentInfo] = []
        self._current_agent: Optional[AgentInfo] = None

    def _get_gesture_safety_pool(self) -> str:
        return normalize_gesture_safety_pool(
            self._config.get("gesture_safety_pool", DEFAULT_GESTURE_SAFETY_POOL)
        )

    async def discover_agents(self) -> List[AgentInfo]:
        """
        Discover available agents in the configured directory.
        Looks for Python files with "agent" in the name (e.g., *agent*.py).
        """
        agents = []
        agents_dir = self._config.get("agents_directory")

        if not agents_dir:
            logger.warning("No agents_directory configured")
            return agents

        agents_path = Path(agents_dir)
        logger.info(f"Looking for agents in: {agents_path} (absolute: {agents_path.resolve()})")

        if not agents_path.exists() or not agents_path.is_dir():
            logger.warning(f"Agents directory does not exist or is not a directory: {agents_path.resolve()}")
            return agents

        # Look for any Python file with "agent" in the name
        # Use a set to avoid duplicates
        seen = set()

        for file_path in agents_path.glob("*agent*.py"):
            if file_path.is_file() and not file_path.name.startswith("_"):
                # Extract module name (filename without .py)
                module_name = file_path.stem

                # Skip if already seen
                if module_name in seen:
                    continue
                seen.add(module_name)

                # Create friendly name (convert underscores to spaces, title case)
                friendly_name = module_name.replace("_", " ").title()

                agents.append(AgentInfo(
                    name=friendly_name,
                    path=str(file_path),
                    module_name=module_name
                ))

        self._available_agents = sorted(agents, key=lambda a: a.name)
        return self._available_agents

    def get_available_agents(self) -> List[Dict[str, Any]]:
        """Get list of available agents."""
        return [agent.to_dict() for agent in self._available_agents]

    def get_current_agent(self) -> Optional[Dict[str, Any]]:
        """Get currently selected agent."""
        return self._current_agent.to_dict() if self._current_agent else None

    async def set_agent(self, agent_implementation: str) -> None:
        """
        Select which local agent implementation to use.
        Can specify by module name or friendly name.
        """
        # Refresh available agents
        await self.discover_agents()

        # Find agent by name (check both module_name and friendly name)
        selected = None
        for agent in self._available_agents:
            if agent.module_name == agent_implementation or agent.name == agent_implementation:
                selected = agent
                break

        if not selected:
            available_names = [a.name for a in self._available_agents]
            raise ValueError(
                f"Agent implementation '{agent_implementation}' not found. Available: {available_names}"
            )

        self._current_agent = selected
        self._config["selected_agent"] = selected.module_name

        # If service is running, restart with new agent
        if self._state == ServiceState.RUNNING:
            await self.restart()

    async def start(self) -> None:
        """Start the voice agent process."""
        if self._state != ServiceState.STOPPED:
            raise ValueError(f"Service {self.name} is not stopped (current state: {self._state})")

        self._state = ServiceState.STARTING
        self._last_error = None

        try:
            # Ensure we have discovered agents
            if not self._available_agents:
                await self.discover_agents()

            # Get selected agent
            selected_agent_implementation = self._config.get("selected_agent")

            if selected_agent_implementation:
                # Normalize implementation name (remove .py extension if present)
                if selected_agent_implementation.endswith(".py"):
                    selected_agent_implementation = selected_agent_implementation[:-3]

                # Find the selected implementation
                for agent in self._available_agents:
                    if agent.module_name == selected_agent_implementation:
                        self._current_agent = agent
                        logger.info(f"Selected agent: {agent.name} ({agent.module_name})")
                        break

                # If specified agent not found, log error and list available
                if not self._current_agent:
                    available = ", ".join([f"{a.name} ({a.module_name})" for a in self._available_agents])
                    error_msg = (
                        f"Specified agent implementation '{selected_agent_implementation}' not found. "
                        f"Available agents: {available}"
                    )
                    logger.error(error_msg)
                    raise ValueError(error_msg)

            if not self._current_agent:
                # Default to first agent if available
                if self._available_agents:
                    self._current_agent = self._available_agents[0]
                    logger.info(f"Using default agent: {self._current_agent.name} ({self._current_agent.module_name})")
                else:
                    raise ValueError("No agents available in configured directory")

            # The production path stays on the supervisor interpreter. The
            # isolated plugin environment is selected only by the canary flag.
            voice_agent_python = resolve_voice_agent_python()
            cmd = [
                voice_agent_python,
                self._current_agent.path,
                "start"
            ]

            # Add environment variables for agent configuration
            # LiveKit credentials are inherited from parent environment
            env = os.environ.copy()
            azure_env = build_child_environment_updates()
            env.update(azure_env)
            if _env_enabled("VOICE_CANARY_ENABLED"):
                active_voice = get_active_voice()
                if active_voice:
                    env["TTS_PROVIDER"] = active_voice["provider"]
                    env["TTS_MODEL"] = active_voice["model"]
                    env["TTS_VOICE_ID"] = active_voice["voice_id"]
                    env["TTS_LANGUAGE"] = active_voice["language"]
            else:
                active_tts_tag = get_active_tts_tag()
                if active_tts_tag:
                    env["TRUEBAR_TTS_TAG"] = active_tts_tag
            env["GESTURE_SAFETY_POOL"] = self._get_gesture_safety_pool()
            env["GESTURE_CATALOG_ID"] = resolve_gesture_catalog_id_for_robot_context(
                self._robot_context
            )
            # Tell the agent where this supervisor actually listens. The agent
            # calls back for face-identity lookups and face capture/forget, and
            # its own fallback default cannot know a deployment's api.port (this
            # robot runs 8070, the example config uses 8080). Mirrors how
            # vision-controller is given supervisor_url.
            env["SUPERVISOR_API_URL"] = str(
                self._config.get("supervisor_url", "http://127.0.0.1:8080")
            ).rstrip("/")
            if self._robot_context:
                robot_id = self._robot_context.get("id")
                robot_name = self._robot_context.get("name")
                robot_platform = self._robot_context.get("platform")
                robot_model = self._robot_context.get("model")
                if robot_id:
                    env["HUMANOID_ROBOT_ID"] = str(robot_id)
                if robot_name:
                    env["HUMANOID_ROBOT_NAME"] = str(robot_name)
                if robot_platform:
                    env["HUMANOID_ROBOT_PLATFORM"] = str(robot_platform)
                if robot_model:
                    env["HUMANOID_ROBOT_MODEL"] = str(robot_model)

            # Ensure log directory exists
            os.makedirs(os.path.dirname(self._log_file), exist_ok=True)

            # Write start divider to log showing which agent is being started
            from datetime import datetime
            with open(self._log_file, 'a') as f:
                timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                divider = (
                    f"\n{'=' * 80}\n"
                    f"=== VOICE AGENT START at {timestamp} ===\n"
                    f"=== Agent: {self._current_agent.name} ({self._current_agent.module_name}) ===\n"
                    f"=== Script: {self._current_agent.path} ===\n"
                    f"=== Python: {voice_agent_python} ===\n"
                    f"=== Environment: {azure_env.get('ROBOT_SUPERVISOR_ENVIRONMENT')} ===\n"
                    f"=== Truebar TTS Tag: {env.get('TRUEBAR_TTS_TAG', '')} ===\n"
                    f"{'=' * 80}\n\n"
                )
                f.write(divider)

            # Start process
            self._log_handle = open(self._log_file, 'a')
            self._process = subprocess.Popen(
                cmd,
                stdout=self._log_handle,
                stderr=subprocess.STDOUT,
                env=env
            )

            # Give it a moment to fail if there are issues
            await asyncio.sleep(2)

            if self._process.poll() is not None:
                self._state = ServiceState.FAILED
                self._last_error = "Process exited immediately after start"
                raise RuntimeError(self._last_error)

            self._state = ServiceState.RUNNING
            self._mark_started()

        except EnvironmentConfigError as e:
            self._state = ServiceState.FAILED
            self._last_error = str(e)
            raise
        except Exception as e:
            self._state = ServiceState.FAILED
            self._last_error = str(e)
            raise

    async def stop(self) -> None:
        """Stop the voice agent process."""
        if self._state == ServiceState.STOPPED:
            return

        self._state = ServiceState.STOPPING

        if self._process:
            self._process.terminate()
            try:
                self._process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._process.kill()
                self._process.wait()
            self._process = None

        if self._log_handle:
            self._log_handle.close()
            self._log_handle = None

        self._state = ServiceState.STOPPED
        self._start_time = None

    async def check_health(self) -> bool:
        """Check if process is alive."""
        # For voice agent, process being alive is sufficient
        # More sophisticated health check could check LiveKit connection
        return self._process is not None and self._process.poll() is None

    def get_log_path(self) -> str:
        """Return path to log file."""
        return self._log_file

    def get_dependencies(self) -> List[str]:
        """Voice agent requires LiveKit server to be running."""
        return ["livekit"]

    def get_current_mode(self) -> Optional[str]:
        """Return current agent name as mode."""
        return self._current_agent.name if self._current_agent else None

    def get_available_modes(self) -> List[str]:
        """Return available agent names as modes."""
        return [agent.name for agent in self._available_agents]

    def get_config_parameters(self) -> List[ConfigParameter]:
        """Return configurable parameters."""
        agent_choices = [agent.name for agent in self._available_agents]

        return [
            ConfigParameter(
                key="agents_directory",
                value=self._config.get("agents_directory"),
                type="string",
                description="Directory containing agent implementations",
                required=True
            ),
            ConfigParameter(
                key="selected_agent",
                value=self._current_agent.name if self._current_agent else None,
                type="choice",
                choices=agent_choices if agent_choices else ["No agents found"],
                description="Selected agent to run",
                required=True
            )
        ]

    async def update_config(self, updates: Dict[str, Any]) -> None:
        updates = updates.copy()
        if "gesture_safety_pool" in updates:
            updates["gesture_safety_pool"] = normalize_gesture_safety_pool(
                updates["gesture_safety_pool"]
            )

        self._config.update(updates)

        if "selected_agent" in updates:
            self._current_agent = None

        if self._state == ServiceState.RUNNING:
            logger.info("Voice agent config changed, restarting service...")
            await self.restart()
