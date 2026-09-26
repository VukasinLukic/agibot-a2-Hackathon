from pathlib import Path
from unittest.mock import patch

import yaml

from content_service.content_store.prompts.service import PromptService


def test_english_persona_edit_updates_only_localized_file(tmp_path: Path):
    english_path = tmp_path / "personas.en.yaml"
    english_path.write_text(
        yaml.safe_dump(
            {
                "hall_of_fame_tour": {
                    "title": "Hall of Fame Tour",
                    "prompt_text": "Old English instructions",
                    "initial_greeting": "Old greeting",
                    "goodbye_text": "Old goodbye",
                }
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    with patch(
        "content_service.content_store.prompts.service.ENGLISH_PERSONAS_PATH",
        english_path,
    ):
        service = PromptService(include_examples=False)
        item = service.list_english_personas(include_archived=True)[0]
        updated = service.update_english_persona(
            item["id"],
            slug=item["slug"],
            title="Guided Tour",
            prompt_text="New English instructions",
            initial_greeting="Welcome to the hall.",
            goodbye_text="Thank you for visiting.",
            is_archived=False,
        )

    saved = yaml.safe_load(english_path.read_text(encoding="utf-8"))
    assert updated["slug"] == "hall_of_fame_tour"
    assert saved["hall_of_fame_tour"]["prompt_text"] == "New English instructions"
    assert saved["hall_of_fame_tour"]["initial_greeting"] == "Welcome to the hall."
