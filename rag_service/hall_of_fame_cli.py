#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

from rag_service.hall_of_fame import (
    NARRATIVE_COUNT,
    PANEL_COUNT,
    RECORD_COUNT,
    HallOfFameStore,
    load_jsonl,
)


ACCEPTANCE_QUESTIONS = [
    ("When was Comtrade founded?", "panel-04-bringing-business-back"),
    ("Who founded the company / who is Veselin Jevrosimovic?", "panel-01-beginnings"),
    ("What is the Tesla brand?", "panel-25-tesla"),
    ("Tell me about the CERN partnership.", "panel-26-cern"),
    ("When did Comtrade acquire Hermes SoftLab?", "panel-19-hermes"),
    ("What happened with Citrix?", "panel-27-citrix"),
    ("Where is the headquarters now?", "panel-34-hq-switzerland"),
    ("What was the famous TV commercial?", "panel-05-tv-commercial"),
    ("Tell me about the gaming business.", "panel-23-gaming"),
    ("What is HYCU?", "panel-29-hycu"),
    ("Who acquired Comtrade Digital Services?", "panel-31-strategic-transactions"),
    ("How many countries is distribution present in?", "panel-28-distribution-expansion"),
    ("What awards has Comtrade System Integration won?", "panel-37-csi-achievements"),
    ("Tell me about the Belgrade Marathon / athletics.", "panel-36-athletics"),
    ("What is Comtrade 360?", "panel-32-comtrade-360"),
]


def main() -> int:
    parser = argparse.ArgumentParser(description="Build and test the Hall of Fame Qdrant collection")
    subparsers = parser.add_subparsers(dest="command", required=True)
    ingest_parser = subparsers.add_parser("ingest")
    ingest_parser.add_argument("jsonl", type=Path)
    smoke_parser = subparsers.add_parser("smoke")
    smoke_parser.add_argument("--strict", action="store_true")
    activate_parser = subparsers.add_parser("activate")
    activate_parser.add_argument("panel_id")
    search_parser = subparsers.add_parser("search")
    search_parser.add_argument("question")
    args = parser.parse_args()

    store = HallOfFameStore()
    if args.command == "ingest":
        records = load_jsonl(args.jsonl)
        store.ingest(records)
        print(
            f"hall_of_fame: {RECORD_COUNT} records verified "
            f"({PANEL_COUNT} panel, {NARRATIVE_COUNT} narrative)"
        )
        return 0
    if args.command == "activate":
        record = store.activate_panel(args.panel_id)
        if not record:
            print("not found")
            return 1
        print(f"{record['id']} | {record['title']}")
        print(record["spoken"])
        return 0
    if args.command == "search":
        point = store.voice_search(args.question)
        if point is None:
            print("not found")
            return 1
        print(f"{point.payload['id']} | {point.payload['title']}")
        return 0

    failures = 0
    for question, expected in ACCEPTANCE_QUESTIONS:
        point = store.voice_search(question)
        record = dict(point.payload or {}) if point is not None else {}
        actual = record.get("id", "NOT_FOUND")
        status = "PASS" if actual == expected else "FAIL"
        failures += status == "FAIL"
        print(f"{status} | expected={expected} | actual={actual} | {record.get('title', '')}")
    print(f"smoke: {len(ACCEPTANCE_QUESTIONS) - failures}/{len(ACCEPTANCE_QUESTIONS)} passed")
    return 1 if args.strict and failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
