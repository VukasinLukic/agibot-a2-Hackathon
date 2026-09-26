"""Vision stub (owner: person 1, branch ``comp-vision``).

FixtureVisionProducer implements the VisionProducer port without a camera:
it reads proposal fixtures, fills rally/calibration/assignment from the
current backend snapshot, validates everything through the shared contract and
sends a real ``point.propose`` command. It never computes the score or the
server. Replace/extend with capture -> calibration -> tracker -> events.
"""

from __future__ import annotations

import json
import threading
import uuid
from pathlib import Path
from typing import Any, Iterable, Optional

from table_tennis.contracts import MatchSnapshot, PointProposal, parse_command
from table_tennis.core.ports import CommandSink, ContextProvider

FROM_CONTEXT = "from_context"


def build_proposal(snapshot: MatchSnapshot, spec: dict[str, Any], proposal_id: Optional[str] = None) -> PointProposal:
    """Build a validated PointProposal. ``spec`` values equal to "from_context"
    are taken from the snapshot (active rally, calibration, assignment)."""
    data = {
        "proposal_id": proposal_id or str(uuid.uuid4()),
        "rally_id": FROM_CONTEXT,
        "calibration_id": FROM_CONTEXT,
        "assignment_version": FROM_CONTEXT,
        "capture_start_seq": 0,
        "capture_end_seq": 0,
        "evidence_ref": None,
    }
    data.update(spec)
    ctx = {
        "rally_id": snapshot.active_rally_id,
        "calibration_id": snapshot.calibration_id,
        "assignment_version": snapshot.assignment_version,
    }
    for key, value in list(data.items()):
        if value == FROM_CONTEXT:
            data[key] = ctx[key]
    return PointProposal.model_validate(data)


def propose_command(snapshot: MatchSnapshot, proposal: PointProposal, command_id: Optional[str] = None) -> dict:
    cmd = {
        "command_id": command_id or str(uuid.uuid4()),
        "expected_revision": snapshot.revision,
        "type": "point.propose",
        "payload": proposal.model_dump(mode="json"),
    }
    parse_command(cmd)  # validate against the contract before sending
    return cmd


class FixtureVisionProducer:
    """Emits one validated point.propose per fixture item, then stops."""

    def __init__(self, items: Iterable[dict[str, Any]], *, announce_camera_ready: bool = True):
        self.items = list(items)
        self.announce_camera_ready = announce_camera_ready
        self._closed = threading.Event()
        self.sent: list[dict] = []

    @classmethod
    def from_file(cls, path: str | Path, **kw: Any) -> "FixtureVisionProducer":
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls(data["proposals"] if isinstance(data, dict) else data, **kw)

    def run(self, sink: CommandSink, context_provider: ContextProvider) -> None:
        if self.announce_camera_ready and not self._closed.is_set():
            cmd = {
                "command_id": str(uuid.uuid4()),
                "expected_revision": None,
                "type": "camera.ready.set",
                "payload": {"ready": True, "reason": "fixture producer (simulated camera)"},
            }
            parse_command(cmd)
            sink(cmd)
            self.sent.append(cmd)
        for spec in self.items:
            if self._closed.is_set():
                return
            snapshot = context_provider()
            proposal = build_proposal(snapshot, spec)
            cmd = propose_command(snapshot, proposal)
            sink(cmd)
            self.sent.append(cmd)

    def close(self) -> None:
        self._closed.set()
