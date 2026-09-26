import json
from pathlib import Path

from table_tennis import manifest
from table_tennis.sim.driver import InProcessDriver
from table_tennis.sim.fixtures import make_runtime
from table_tennis.sim.scenarios import new_match


def _make_db(tmp_path) -> Path:
    runtime, ids = make_runtime(str(tmp_path))
    try:
        new_match(InProcessDriver(runtime, ids=ids))
    finally:
        runtime.stop()
    return tmp_path / "fixture.sqlite"


def test_manifest_with_db(tmp_path):
    db = _make_db(tmp_path)
    m = manifest.build_manifest(db_path=str(db), env={"TT_AUTH_MODE": "local"}, check_generated=False)
    assert m["mode"] == "mock" and m["simulated"] is True
    assert m["auth_mode"] == "local"
    assert m["automatic_scoring_enabled"] is False
    assert m["hardware_tested"] is False
    assert set(m["adapters"]) >= {"display", "speech", "gesture", "navigator"}
    lm = m["latest_match"]
    assert lm["revision"] == 1 and lm["status"] == "setup"
    assert lm["calibration_id"] == "table-1-camera-a-v1"
    assert lm["scoring_mode"] == "assisted"
    assert m["vision"]["model_version"] is None
    assert m["known_limits"]
    assert "\u2014" not in manifest.render_text(m)


def test_missing_db_not_created(tmp_path):
    db = tmp_path / "nope.sqlite"
    m = manifest.build_manifest(db_path=str(db), env={}, check_generated=False)
    assert m["latest_match"] is None
    assert not db.exists()


def test_contract_hash_and_generated_check():
    m = manifest.build_manifest(db_path="does-not-exist.sqlite", env={})
    assert len(m["contract"]["contract_schema_sha256"]) == 64
    assert "up_to_date" in m["contract"]["generated"]
    assert "up_to_date" in m["fixtures"]


def test_cli_json_out(tmp_path, capsys):
    out = tmp_path / "m.json"
    note = "probano rucno na stolu"
    rc = manifest.main(["--db", str(tmp_path / "x.sqlite"), "--json", "--out", str(out), "--hardware-tested-note", note])
    assert rc == 0
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["hardware_tested"] is False
    assert data["hardware_tested_note"] == note
    assert not (tmp_path / "x.sqlite").exists()
