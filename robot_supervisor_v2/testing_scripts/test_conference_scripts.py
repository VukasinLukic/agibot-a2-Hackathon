#!/usr/bin/env python3
"""Focused tests for conference script loading, saving, and migration."""

from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

import yaml

from robot_supervisor_v2.app.models.conference import (
    ConferenceEventDocument,
    ConferenceScenario,
    ConferenceSection,
    ConferenceStep,
)
from robot_supervisor_v2.app.services.conference_scripts import ConferenceScriptService


REPO_ROOT = Path(__file__).resolve().parents[2]
SOURCE_SCRIPTS_DIR = REPO_ROOT / "conference_scripts"


def make_document(
    *,
    key: str,
    section_id: str = "intro",
    scenario_id: str = "welcome",
    steps: list[ConferenceStep] | None = None,
) -> ConferenceEventDocument:
    return ConferenceEventDocument(
        key=key,
        event_id=key,
        name=f"Event {key}",
        location="Ljubljana",
        language="sl",
        robot_name="Primus",
        robot_role="event_host",
        sections=[
            ConferenceSection(
                id=section_id,
                section="Introduction",
                description="Opening section",
                scenarios=[
                    ConferenceScenario(
                        id=scenario_id,
                        label="Welcome",
                        summary_text="Opening",
                        single_action_only=False,
                        action_label=None,
                        steps=steps
                        or [
                            ConferenceStep(
                                text="Pozdravljeni",
                                gesture="face wave",
                                pause_after_ms=500,
                            )
                        ],
                    )
                ],
            )
        ],
    )


class ConferenceScriptServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temp_dir = tempfile.TemporaryDirectory()
        self.scripts_dir = Path(self._temp_dir.name) / "conference_scripts"
        shutil.copytree(SOURCE_SCRIPTS_DIR, self.scripts_dir)
        self.service = ConferenceScriptService(self.scripts_dir)

    def tearDown(self) -> None:
        self._temp_dir.cleanup()

    def test_hall_of_fame_event_loads_and_saves_canonical_format(self) -> None:
        document = self.service.get_event_document("hall_of_fame")

        self.assertEqual(document.key, "hall_of_fame")
        self.assertEqual(document.sections[0].id, "panels")
        self.assertEqual(len(document.sections[0].scenarios), 40)
        self.assertTrue(document.sections[0].scenarios[0].single_action_only)
        self.assertTrue(document.sections[0].scenarios[0].steps[0].text.startswith("Our story starts"))

        self.service.update_event("hall_of_fame", document)

        manifest = yaml.safe_load((self.scripts_dir / "hall_of_fame" / "event.yaml").read_text(encoding="utf-8"))
        section_yaml = yaml.safe_load((self.scripts_dir / "hall_of_fame" / "panels.yaml").read_text(encoding="utf-8"))

        self.assertEqual(manifest["scripts"], ["panels"])
        self.assertIsInstance(section_yaml["scenarios"], list)
        self.assertEqual(len(section_yaml["scenarios"]), 40)
        self.assertTrue(section_yaml["scenarios"][0]["single_action_only"])

    def test_rename_and_delete_update_active_event_pointer(self) -> None:
        self.service.set_active_event("hall_of_fame")
        document = self.service.get_event_document("hall_of_fame")
        document.key = "Hall of Fame canonical"
        document.event_id = "hall_of_fame_canonical"

        saved = self.service.update_event("hall_of_fame", document)

        self.assertEqual(saved.key, "Hall of Fame canonical")
        self.assertFalse((self.scripts_dir / "hall_of_fame").exists())
        self.assertTrue((self.scripts_dir / "Hall of Fame canonical").exists())
        self.assertEqual((self.scripts_dir / ".active_event").read_text(encoding="utf-8").strip(), "Hall of Fame canonical")

        deleted = self.service.delete_event("Hall of Fame canonical")

        self.assertEqual(deleted.deleted_key, "Hall of Fame canonical")
        self.assertNotEqual(deleted.active_event_key, "Hall of Fame canonical")
        if deleted.active_event_key:
            self.assertEqual(
                (self.scripts_dir / ".active_event").read_text(encoding="utf-8").strip(),
                deleted.active_event_key,
            )

    def test_validation_rejects_invalid_event_shapes(self) -> None:
        invalid_documents = [
            ("invalid event key", make_document(key="bad/name")),
            (
                "empty step",
                make_document(
                    key="empty_step",
                    steps=[ConferenceStep(text="", gesture="", pause_after_ms=None)],
                ),
            ),
            (
                "non-positive wait",
                make_document(
                    key="bad_wait",
                    steps=[ConferenceStep(text="Wait", pause_after_ms=0)],
                ),
            ),
        ]

        for label, document in invalid_documents:
            with self.subTest(label=label):
                with self.assertRaises(ValueError):
                    self.service.create_event(document, allowed_gestures={"face wave"})

        with self.assertRaises(ValueError):
            self.service.create_event(
                make_document(
                    key="bad_gesture",
                    steps=[ConferenceStep(text="Pozdrav", gesture="x-ray")],
                ),
                allowed_gestures={"face wave"},
            )

    def test_round_trip_preserves_all_supported_step_variants(self) -> None:
        document = make_document(
            key="roundtrip_steps",
            steps=[
                ConferenceStep(text="Speech only"),
                ConferenceStep(gesture="face wave"),
                ConferenceStep(text="Speech and gesture", gesture="high wave", pause_after_ms=400),
                ConferenceStep(pause_after_ms=1200),
            ],
        )

        self.service.create_event(document, allowed_gestures={"face wave", "high wave"})

        reloaded = self.service.get_event_document("roundtrip_steps")
        runtime = self.service.load_program("roundtrip_steps")
        steps = reloaded.sections[0].scenarios[0].steps

        self.assertEqual(len(steps), 4)
        self.assertEqual(steps[0].text, "Speech only")
        self.assertEqual(steps[1].gesture, "face wave")
        self.assertEqual(steps[1].text, "")
        self.assertEqual(steps[2].gesture, "high wave")
        self.assertEqual(steps[2].pause_after_ms, 400)
        self.assertEqual(steps[3].pause_after_ms, 1200)
        self.assertEqual(runtime.sections[0].scenarios[0].steps[3].pause_after_ms, 1200)

        section_yaml = yaml.safe_load((self.scripts_dir / "roundtrip_steps" / "intro.yaml").read_text(encoding="utf-8"))
        self.assertEqual(section_yaml["scenarios"][0]["steps"][1]["gesture"], "face wave")
        self.assertEqual(section_yaml["scenarios"][0]["steps"][3]["pause_after_ms"], 1200)


if __name__ == "__main__":
    unittest.main()
