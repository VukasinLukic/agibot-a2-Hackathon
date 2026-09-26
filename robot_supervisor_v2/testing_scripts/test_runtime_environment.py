import tempfile
import unittest
from pathlib import Path

from robot_supervisor_v2.app import runtime_environment as runtime_env


class RuntimeEnvironmentTests(unittest.TestCase):
    def setUp(self):
        self._old_envs_dir = runtime_env._ENVS_DIR
        self._old_state_file = runtime_env._STATE_FILE
        self._old_legacy_env_files = runtime_env._LEGACY_ENV_FILES
        self._old_process_values = {
            key: runtime_env.os.environ.get(key)
            for key in runtime_env.REQUIRED_ENV_KEYS
            + runtime_env.AZURE_ENV_KEYS
            + runtime_env.PROCESS_METADATA_KEYS
        }
        for key in self._old_process_values:
            runtime_env.os.environ.pop(key, None)

        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        runtime_env._ENVS_DIR = root / ".envs"
        runtime_env._STATE_FILE = root / "state" / "environment.json"
        runtime_env._LEGACY_ENV_FILES = ()
        runtime_env._ENVS_DIR.mkdir()

    def tearDown(self):
        runtime_env._ENVS_DIR = self._old_envs_dir
        runtime_env._STATE_FILE = self._old_state_file
        runtime_env._LEGACY_ENV_FILES = self._old_legacy_env_files
        for key, value in self._old_process_values.items():
            if value is None:
                runtime_env.os.environ.pop(key, None)
            else:
                runtime_env.os.environ[key] = value
        self._tmp.cleanup()

    def _write_env(self, name: str, values: dict[str, str]) -> None:
        lines = [f"{key}={value}" for key, value in values.items()]
        (runtime_env._ENVS_DIR / name).write_text("\n".join(lines) + "\n", encoding="utf-8")

    def _complete_values(self) -> dict[str, str]:
        return {
            "AZURE_OPENAI_BASE": "https://dev-openai.example.com/",
            "AZURE_OPENAI_API_KEY": "abcdef1234567890",
            "OPENAI_API_VERSION": "2025-01-01-preview",
            "AI_SEARCH_ENDPOINT": "https://dev-search.example.com/",
            "AI_SEARCH_ADMIN_KEY": "search1234567890",
            "INDEX_NAME": "dev-index",
            "TRUEBAR_USERNAME": "dev-user",
            "TRUEBAR_PASSWORD": "truebar1234567890",
            "TRUEBAR_CLIENT_ID": "truebar-client",
            "TRUEBAR_AUTH_URL": "https://dev-auth.truebar.example.com/token",
            "TRUEBAR_API_BASE_URL": "https://dev-api.truebar.example.com",
            "TRUEBAR_STT_WS_URL": "wss://dev-api.truebar.example.com/stt",
            "TRUEBAR_TTS_WS_URL": "wss://dev-api.truebar.example.com/tts",
        }

    def test_defaults_to_dev(self):
        self.assertEqual(runtime_env.read_selected_environment(), "DEV")

    def test_loads_common_and_selected_overlay(self):
        self._write_env("common.env", {"CHOSEN_COMPLETION_MODEL": "gpt-common"})
        self._write_env(
            "dev.env",
            {**self._complete_values(), "CHOSEN_COMPLETION_MODEL": "gpt-dev"},
        )

        loaded = runtime_env.validate_runtime_environment("DEV")

        self.assertTrue(loaded.valid)
        self.assertEqual(loaded.values["CHOSEN_COMPLETION_MODEL"], "gpt-dev")
        self.assertEqual(loaded.values["INDEX_NAME"], "dev-index")

    def test_status_masks_secrets(self):
        self._write_env("dev.env", self._complete_values())

        status = runtime_env.runtime_environment_status()

        self.assertEqual(status["azure"]["openai_key"], "abcd...7890")
        self.assertEqual(status["azure"]["search_key"], "sear...7890")
        self.assertEqual(status["azure"]["openai_host"], "dev-openai.example.com")
        self.assertEqual(status["truebar"]["password"], "true...7890")
        self.assertEqual(status["truebar"]["auth_host"], "dev-auth.truebar.example.com")

    def test_rejects_invalid_environment(self):
        with self.assertRaises(runtime_env.EnvironmentConfigError):
            runtime_env.normalize_environment("stage")

    def test_reports_missing_required_values(self):
        self._write_env("dev.env", {"AZURE_OPENAI_BASE": "https://dev-openai.example.com/"})

        loaded = runtime_env.load_runtime_environment("DEV")

        self.assertIn("AZURE_OPENAI_API_KEY", loaded.missing_required)
        self.assertNotIn("INDEX_NAME", loaded.missing_required)
        self.assertNotIn("TRUEBAR_USERNAME", loaded.missing_required)

    def test_builds_child_environment_updates(self):
        self._write_env("dev.env", self._complete_values())

        updates = runtime_env.build_child_environment_updates("DEV")

        self.assertEqual(updates["ROBOT_SUPERVISOR_ENVIRONMENT"], "DEV")
        self.assertEqual(updates["INDEX_NAME"], "dev-index")
        self.assertEqual(updates["TRUEBAR_USERNAME"], "dev-user")
        self.assertTrue(updates["ROBOT_ENV_FILE"].endswith("dev.env"))


if __name__ == "__main__":
    unittest.main()
