import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch


REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from livekit_config.prompt_builder import PromptBuilder


class PromptBuilderRobotNameTests(unittest.TestCase):
    def _builder_with_env(self, values: dict[str, str]) -> PromptBuilder:
        env = {
            key: value
            for key, value in os.environ.items()
            if key not in {"HUMANOID_ROBOT_NAME", "ROBOT_NAME"}
        }
        env.update(values)
        with patch.dict(os.environ, env, clear=True):
            return PromptBuilder(include_examples=False)

    def test_humanoid_robot_name_overrides_legacy_robot_name(self) -> None:
        builder = self._builder_with_env(
            {
                "HUMANOID_ROBOT_NAME": "Agibot X2 Ultra",
                "ROBOT_NAME": "Primus",
            }
        )

        self.assertEqual(builder.default_variables["robot_name"], "Agibot X2 Ultra")

    def test_robot_name_remains_standalone_fallback(self) -> None:
        builder = self._builder_with_env({"ROBOT_NAME": "Primus"})

        self.assertEqual(builder.default_variables["robot_name"], "Primus")

    def test_no_robot_name_env_keeps_legacy_default(self) -> None:
        builder = self._builder_with_env({})

        self.assertEqual(builder.default_variables["robot_name"], "Primus")

    def test_blank_humanoid_robot_name_falls_back_to_robot_name(self) -> None:
        builder = self._builder_with_env(
            {
                "HUMANOID_ROBOT_NAME": "  ",
                "ROBOT_NAME": "Primus",
            }
        )

        self.assertEqual(builder.default_variables["robot_name"], "Primus")


if __name__ == "__main__":
    unittest.main()
