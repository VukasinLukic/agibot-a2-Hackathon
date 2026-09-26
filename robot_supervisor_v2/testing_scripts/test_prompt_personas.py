import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from content_service.content_store.file_store import load_yaml, write_yaml_atomic
from content_service.content_store.prompts import service as prompt_service_module
from content_service.content_store.prompts.service import PromptService


class PromptPersonaModelTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        self.paths = {
            "modes": (root / "base.yaml", root / "base.example.yaml"),
            "speaking_styles": (root / "personas.yaml", root / "personas.example.yaml"),
            "event_settings": (root / "contexts.yaml", root / "contexts.example.yaml"),
            "event_moments": (root / "event_parts.yaml", root / "event_parts.example.yaml"),
        }
        self.config_path = root / "prompt_config.yaml"

        write_yaml_atomic(self.paths["modes"][0], {"main": {"title": "Main", "prompt_text": "MAIN RULES"}})
        write_yaml_atomic(
            self.paths["speaking_styles"][0],
            {
                "host": {"title": "Host", "prompt_text": "HOST PERSONA"},
                "guide": {"title": "Guide", "prompt_text": "GUIDE PERSONA"},
            },
        )
        write_yaml_atomic(self.paths["event_settings"][0], {"office": {"title": "Office", "prompt_text": ""}})
        write_yaml_atomic(self.paths["event_moments"][0], {"contexts": {"office": {}}})
        write_yaml_atomic(
            self.config_path,
            {"active": {"core_mode": "main", "persona": "host", "context": "office", "phase": "no_event_moment"}},
        )

        self.patches = [
            patch.object(prompt_service_module, "PROMPT_FILES", self.paths),
            patch.object(prompt_service_module, "PROMPT_CONFIG_PATH", self.config_path),
        ]
        for active_patch in self.patches:
            active_patch.start()
        self.service = PromptService(include_examples=False)

    def tearDown(self) -> None:
        for active_patch in reversed(self.patches):
            active_patch.stop()
        self.temp_dir.cleanup()

    def test_main_is_protected_and_keeps_canonical_slug(self) -> None:
        main = self.service.get_main_prompt()
        self.assertEqual(main["slug"], "main")
        self.assertTrue(main["protected"])

        with self.assertRaisesRegex(ValueError, "cannot be deleted"):
            self.service.delete_mode("main")

        updated = self.service.update_main_prompt(
            title="Main rules",
            prompt_text="UPDATED MAIN",
            initial_greeting="Hello",
            goodbye_text="Bye",
            is_archived=False,
        )
        self.assertEqual(updated["slug"], "main")
        self.assertEqual(self.service.get_main_prompt()["prompt_text"], "UPDATED MAIN")

    def test_personas_are_crud_layers_after_main(self) -> None:
        created = self.service.create_speaking_style(
            slug="sports",
            title="Sports",
            prompt_text="SPORTS PERSONA",
            initial_greeting="Sports hello",
            goodbye_text="",
        )
        self.service.set_active_persona(created["id"])

        parts = self.service.get_prompt_parts()
        self.assertEqual(parts["mode"], "MAIN RULES")
        self.assertEqual(parts["speaking_style"], "SPORTS PERSONA")
        self.assertEqual(self.service.get_initial_greeting(), "Sports hello")

        self.service.delete_speaking_style(created["id"])
        self.assertNotEqual(self.service.get_active_selection()["persona"], "sports")
        stored = load_yaml(self.paths["speaking_styles"][0], None, {})
        self.assertNotIn("sports", stored)


if __name__ == "__main__":
    unittest.main()
