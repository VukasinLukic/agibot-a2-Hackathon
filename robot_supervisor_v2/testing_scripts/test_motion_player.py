import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch


REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from robot_services.gestures.motion_player import (
    AgibotMotionPlayerConfig,
    AgibotMotionPlayerController,
)


_PRESETS = [
    {
        "motion_id": 62,
        "motion_name": "挥手挥手",
        "motion_path": "/agibot/data/resources/default/motion/wave/wave.mcap",
        "display_name_en": "Wave hand_right hand",
        "duration": 4.0,
    },
    {
        "motion_id": 27,
        "motion_name": "比也",
        "motion_path": "/agibot/data/resources/default/motion/clap/clap.mcap",
        "display_name_en": "Yeah_right hand",
        "duration": 3.0,
    },
]

_LONG_EXPLANATION_PRESET = {
    "motion_id": 43,
    "motion_name": "general explanation",
    "motion_path": "/motions/general_explanation_22s.mcap",
    "display_name_en": "General explanation actions_22s",
    "duration": 22.208,
}


def _controller(**overrides) -> AgibotMotionPlayerController:
    cfg = AgibotMotionPlayerConfig(
        action_by_gesture={"wave": 1, "clap": 2, "missing": 3},
        motion_hints={1: "wave", 2: "clap", 3: "nonexistent"},
        **overrides,
    )
    return AgibotMotionPlayerController(cfg)


def _mock_client(responses: dict) -> MagicMock:
    client = MagicMock()
    client.__enter__.return_value = client

    def _post(url, json):
        for key, payload in responses.items():
            if key in url:
                response = MagicMock()
                response.json.return_value = payload
                response.raise_for_status.return_value = None
                return response
        raise AssertionError(f"Unexpected URL: {url}")

    client.post.side_effect = _post
    return client


class AgibotMotionPlayerTests(unittest.TestCase):
    def test_execute_gesture_resolves_and_plays_motion(self) -> None:
        controller = _controller()
        client = _mock_client(
            {
                "GetMotion": {"motions": _PRESETS},
                "SendMotionCommand": {"state": "CommonState_SUCCESS"},
            }
        )

        with patch("robot_services.gestures.motion_player.httpx.Client", return_value=client):
            result = controller.execute_gesture("wave")

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["motion_id"], "/agibot/data/resources/default/motion/wave/wave.mcap")
        self.assertEqual(result["duration_ms"], 4000)

    def test_unresolved_hint_is_skipped_not_fatal(self) -> None:
        controller = _controller()
        client = _mock_client({"GetMotion": {"motions": _PRESETS}})

        with patch("robot_services.gestures.motion_player.httpx.Client", return_value=client):
            result = controller.execute_gesture("missing")

        self.assertEqual(result["status"], "skipped")
        self.assertEqual(result["reason"], "motion_not_resolved")

    def test_unknown_gesture_is_skipped(self) -> None:
        controller = _controller()

        result = controller.execute_gesture("does-not-exist")

        self.assertEqual(result["status"], "skipped")
        self.assertEqual(result["reason"], "unknown_action_mapping")

    def test_robot_disabled_skips_without_network_call(self) -> None:
        controller = _controller()

        with patch("robot_services.gestures.motion_player.robot_enabled", return_value=False):
            result = controller.execute_gesture("wave")

        self.assertEqual(result["status"], "skipped")
        self.assertEqual(result["reason"], "robot_disabled")

    def test_only_explicitly_approved_motion_can_exceed_default_duration_cap(self) -> None:
        responses = {
            "GetMotion": {"motions": [_LONG_EXPLANATION_PRESET]},
            "SendMotionCommand": {"state": "CommonState_SUCCESS"},
        }
        default_controller = AgibotMotionPlayerController(
            AgibotMotionPlayerConfig(
                action_by_gesture={"panel explanation": 18},
                motion_hints={18: "General explanation actions_22s"},
            )
        )
        approved_controller = AgibotMotionPlayerController(
            AgibotMotionPlayerConfig(
                action_by_gesture={"panel explanation": 18},
                motion_hints={18: "General explanation actions_22s"},
                motion_duration_caps_ms={18: 23_000},
            )
        )

        with patch(
            "robot_services.gestures.motion_player.httpx.Client",
            return_value=_mock_client(responses),
        ):
            default_result = default_controller.execute_gesture("panel explanation")
        with patch(
            "robot_services.gestures.motion_player.httpx.Client",
            return_value=_mock_client(responses),
        ):
            approved_result = approved_controller.execute_gesture("panel explanation")

        self.assertEqual(default_result["duration_ms"], 6000)
        self.assertEqual(approved_result["duration_ms"], 22208)

    def test_discovery_failure_disables_gestures_without_raising(self) -> None:
        controller = _controller()
        client = MagicMock()
        client.__enter__.return_value = client
        client.post.side_effect = RuntimeError("connection refused")

        with patch("robot_services.gestures.motion_player.httpx.Client", return_value=client):
            result = controller.execute_gesture("wave")

        self.assertEqual(result["status"], "skipped")
        self.assertEqual(result["reason"], "robot_unavailable")


if __name__ == "__main__":
    unittest.main()
