"""Reproducible generator: Pydantic contract -> JSON Schema, OpenAPI, TypeScript.

Usage (from the repo root):

    python -m table_tennis.contracts.generate           # (re)write generated files
    python -m table_tennis.contracts.generate --check   # exit 1 if anything is stale

Outputs:
    table_tennis/contracts/schema/contract.schema.json
    table_tennis/contracts/schema/openapi.json
    robot_supervisor_v2/frontend/src/features/table-tennis/generated/contract.ts

The TypeScript emitter is a small, dependency-free converter for the JSON
Schema subset Pydantic produces for these models. Never edit generated files
by hand; change the models and re-run this command.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Optional, get_args

from pydantic import BaseModel, create_model

from . import primitives as P
from .api import CommandResult, StreamEventMessage, StreamSnapshotMessage
from .commands import CommandEnvelope, CommandType
from .events import EventEnvelope
from .models import (
    CreateMatchRequest,
    DebugOutputs,
    ErrorResponse,
    HealthResponse,
    MatchSnapshot,
    RobotCall,
    RobotCallRequest,
    RobotCancelRequest,
    RobotStatus,
    VisionObservation,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
SCHEMA_DIR = Path(__file__).resolve().parent / "schema"
SCHEMA_PATH = SCHEMA_DIR / "contract.schema.json"
OPENAPI_PATH = SCHEMA_DIR / "openapi.json"
TS_PATH = (
    REPO_ROOT / "robot_supervisor_v2" / "frontend" / "src" / "features" / "table-tennis" / "generated" / "contract.ts"
)

# Named literal aliases, emitted as TS union types and reused when an enum matches.
NAMED_LITERALS: dict[str, Any] = {
    "PlayerId": P.PlayerId,
    "CourtEnd": P.CourtEnd,
    "RobotSide": P.RobotSide,
    "MatchStatus": P.MatchStatus,
    "ScoringMode": P.ScoringMode,
    "Persona": P.Persona,
    "PointReason": P.PointReason,
    "ObservationKind": P.ObservationKind,
    "Actor": P.Actor,
    "ServiceMode": P.ServiceMode,
    "RobotCallState": P.RobotCallState,
    "RobotAvailability": P.RobotAvailability,
    "NavigationState": P.NavigationState,
    "CommandType": CommandType,
}

# Top-level types exported to TS. Union aliases get a named TS type.
TOP_LEVEL: dict[str, Any] = {
    "CommandEnvelope": CommandEnvelope,
    "EventEnvelope": EventEnvelope,
    "MatchSnapshot": MatchSnapshot,
    "CreateMatchRequest": CreateMatchRequest,
    "CommandResult": CommandResult,
    "ErrorResponse": ErrorResponse,
    "RobotCall": RobotCall,
    "RobotCallRequest": RobotCallRequest,
    "RobotCancelRequest": RobotCancelRequest,
    "RobotStatus": RobotStatus,
    "VisionObservation": VisionObservation,
    "HealthResponse": HealthResponse,
    "DebugOutputs": DebugOutputs,
    "StreamSnapshotMessage": StreamSnapshotMessage,
    "StreamEventMessage": StreamEventMessage,
}


def _bundle_model() -> type[BaseModel]:
    fields = {name: (tp, ...) for name, tp in TOP_LEVEL.items()}
    return create_model("ContractBundle", **fields)  # type: ignore[call-overload]


def build_json_schema() -> dict[str, Any]:
    schema = _bundle_model().model_json_schema(ref_template="#/$defs/{model}")
    defs = dict(sorted(schema.get("$defs", {}).items()))
    top: dict[str, Any] = {}
    for name in TOP_LEVEL:
        prop = schema["properties"][name]
        prop = {k: v for k, v in prop.items() if k != "title"}
        top[name] = prop
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "table_tennis/contract/v1",
        "title": "A2 table tennis referee contract",
        "schema_version": P.SCHEMA_VERSION,
        "top_level": top,
        "$defs": defs,
    }


def build_openapi() -> dict[str, Any]:
    from table_tennis.api.app import create_openapi_app

    return create_openapi_app().openapi()


# --------------------------------------------------------------------------- TypeScript


class TSEmitter:
    def __init__(self, defs: dict[str, Any]):
        self.defs = defs
        self.literal_lookup: dict[tuple[str, ...], str] = {}
        for name, tp in NAMED_LITERALS.items():
            self.literal_lookup[tuple(sorted(get_args(tp)))] = name

    def ref_name(self, ref: str) -> str:
        return ref.rsplit("/", 1)[-1]

    def type_of(self, s: dict[str, Any]) -> str:
        if "$ref" in s:
            return self.ref_name(s["$ref"])
        if "const" in s:
            return json.dumps(s["const"])
        if "enum" in s:
            key = tuple(sorted(str(v) for v in s["enum"]))
            if key in self.literal_lookup:
                return self.literal_lookup[key]
            return " | ".join(json.dumps(v) for v in s["enum"])
        for key in ("oneOf", "anyOf"):
            if key in s:
                parts = []
                for sub in s[key]:
                    t = self.type_of(sub)
                    if t not in parts:
                        parts.append(t)
                return " | ".join(parts)
        if "allOf" in s and len(s["allOf"]) == 1:
            return self.type_of(s["allOf"][0])
        t = s.get("type")
        if t == "string":
            return "string"
        if t in ("integer", "number"):
            return "number"
        if t == "boolean":
            return "boolean"
        if t == "null":
            return "null"
        if t == "array":
            inner = self.type_of(s.get("items", {}))
            return f"Array<{inner}>"
        if t == "object":
            if "properties" in s:
                return self.inline_object(s)
            add = s.get("additionalProperties")
            if isinstance(add, dict):
                return f"Record<string, {self.type_of(add)}>"
            return "Record<string, unknown>"
        return "unknown"

    def inline_object(self, s: dict[str, Any]) -> str:
        req = set(s.get("required", []))
        parts = []
        for name, sub in s.get("properties", {}).items():
            opt = "" if name in req else "?"
            parts.append(f"{name}{opt}: {self.type_of(sub)}")
        return "{ " + "; ".join(parts) + " }"

    def interface(self, name: str, s: dict[str, Any]) -> str:
        lines = []
        desc = s.get("description")
        if desc:
            lines.append("/** " + " ".join(desc.split()) + " */")
        if s.get("type") != "object" or "properties" not in s:
            lines.append(f"export type {name} = {self.type_of(s)};")
            return "\n".join(lines)
        lines.append(f"export interface {name} {{")
        req = set(s.get("required", []))
        for field, sub in s["properties"].items():
            opt = "" if field in req else "?"
            lines.append(f"  {field}{opt}: {self.type_of(sub)};")
        lines.append("}")
        return "\n".join(lines)


def build_typescript(schema: dict[str, Any]) -> str:
    defs = schema["$defs"]
    em = TSEmitter(defs)
    out = [
        "// AUTO-GENERATED by `python -m table_tennis.contracts.generate`. DO NOT EDIT.",
        "// Source of truth: table_tennis/contracts (Pydantic, contract v1).",
        "/* eslint-disable */",
        "",
        f"export const SCHEMA_VERSION = {json.dumps(P.SCHEMA_VERSION)} as const;",
        "",
    ]
    for name, tp in NAMED_LITERALS.items():
        values = " | ".join(json.dumps(v) for v in get_args(tp))
        out.append(f"export type {name} = {values};")
    out.append("")
    for name in sorted(defs):
        out.append(em.interface(name, defs[name]))
        out.append("")
    for name, prop in schema["top_level"].items():
        if "$ref" in prop and em.ref_name(prop["$ref"]) == name:
            continue
        out.append(f"export type {name} = {em.type_of(prop)};")
    out.append("")
    out.append("export type CommandOf<T extends CommandType> = Extract<CommandEnvelope, { type: T }>;")
    out.append("export type EventType = EventEnvelope['type'];")
    out.append("export type EventOf<T extends EventType> = Extract<EventEnvelope, { type: T }>;")
    out.append("")
    return "\n".join(out)


def _dump_json(data: Any) -> str:
    return json.dumps(data, indent=2, sort_keys=False, ensure_ascii=False) + "\n"


def render_all() -> dict[Path, str]:
    schema = build_json_schema()
    return {
        SCHEMA_PATH: _dump_json(schema),
        OPENAPI_PATH: _dump_json(build_openapi()),
        TS_PATH: build_typescript(schema),
    }


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--check", action="store_true", help="fail if generated files are missing or stale")
    args = parser.parse_args(argv)
    outputs = render_all()
    stale = []
    for path, content in outputs.items():
        current = path.read_text(encoding="utf-8") if path.exists() else None
        if current != content:
            stale.append(path)
            if not args.check:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(content, encoding="utf-8", newline="\n")
    rel = [str(p.relative_to(REPO_ROOT)) for p in stale]
    if args.check:
        if stale:
            print("STALE generated files (run `python -m table_tennis.contracts.generate`):")
            for r in rel:
                print("  " + r)
            return 1
        print("generated contract files are up to date")
        return 0
    print("wrote: " + (", ".join(rel) if rel else "nothing (already up to date)"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
