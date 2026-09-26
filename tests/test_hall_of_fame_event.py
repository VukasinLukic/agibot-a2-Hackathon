import json
from pathlib import Path

import yaml


REPO_ROOT = Path(__file__).resolve().parents[1]


def test_hall_of_fame_event_has_one_direct_action_per_panel():
    records = [
        json.loads(line)
        for line in (REPO_ROOT / "hall_of_fame_kb.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    panels = sorted(
        (record for record in records if record.get("type") == "panel"),
        key=lambda record: int(record["sequence"]),
    )
    event_dir = REPO_ROOT / "conference_scripts" / "hall_of_fame"
    manifest = yaml.safe_load((event_dir / "event.yaml").read_text(encoding="utf-8"))
    section = yaml.safe_load((event_dir / "panels.yaml").read_text(encoding="utf-8"))
    scenarios = section["scenarios"]

    assert manifest["event"]["name"] == "Comtrade Hall of Fame"
    assert manifest["event"]["language"] == "en"
    assert manifest["robot"]["name"] == "TITAN"
    assert manifest["scripts"] == ["panels"]
    assert len(panels) == len(scenarios) == 40
    assert [scenario["id"] for scenario in scenarios] == [panel["id"] for panel in panels]
    assert all(scenario["single_action_only"] for scenario in scenarios)
    assert all(len(scenario["steps"]) == 1 for scenario in scenarios)
    assert [scenario["steps"][0]["text"] for scenario in scenarios] == [
        panel["spoken"] for panel in panels
    ]


def test_only_hall_of_fame_event_is_present_and_active():
    scripts_dir = REPO_ROOT / "conference_scripts"
    event_directories = sorted(
        path.name for path in scripts_dir.iterdir() if path.is_dir()
    )
    assert event_directories == ["hall_of_fame"]
    assert (scripts_dir / ".active_event").read_text(encoding="utf-8").strip() == "hall_of_fame"
