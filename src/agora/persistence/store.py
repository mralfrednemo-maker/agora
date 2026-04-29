from __future__ import annotations

import json
import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    with temp.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    temp.replace(path)


@dataclass(slots=True)
class RoomStore:
    base_dir: Path

    def __post_init__(self) -> None:
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def room_dir(self, room_id: str) -> Path:
        return self.base_dir / room_id

    def room_file(self, room_id: str) -> Path:
        return self.room_dir(room_id) / "room.json"

    def transcript_file(self, room_id: str) -> Path:
        return self.room_dir(room_id) / "transcript.jsonl"

    def summary_file(self, room_id: str) -> Path:
        return self.room_dir(room_id) / "summary.json"

    def events_file(self, room_id: str) -> Path:
        return self.room_dir(room_id) / "events.log"

    def turn_ledger_file(self, room_id: str) -> Path:
        return self.room_dir(room_id) / "turn-ledger.jsonl"

    def verdict_file(self, room_id: str) -> Path:
        return self.room_dir(room_id) / "verdict.md"

    def save_room(self, room_id: str, payload: dict[str, Any]) -> None:
        _atomic_write_json(self.room_file(room_id), payload)

    def save_summary(self, room_id: str, payload: dict[str, Any]) -> None:
        _atomic_write_json(self.summary_file(room_id), payload)

    def append_transcript(self, room_id: str, line: dict[str, Any]) -> None:
        target = self.transcript_file(room_id)
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(line, ensure_ascii=False))
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())

    def append_event(self, room_id: str, line: dict[str, Any]) -> None:
        target = self.events_file(room_id)
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(line, ensure_ascii=False))
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())

    def append_turn_ledger(self, room_id: str, line: dict[str, Any]) -> None:
        target = self.turn_ledger_file(room_id)
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(line, ensure_ascii=False))
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())

    def save_verdict(self, room_id: str, markdown: str) -> None:
        target = self.verdict_file(room_id)
        target.parent.mkdir(parents=True, exist_ok=True)
        temp = target.with_suffix(target.suffix + ".tmp")
        with temp.open("w", encoding="utf-8", newline="\n") as handle:
            handle.write(markdown)
            handle.flush()
            os.fsync(handle.fileno())
        temp.replace(target)

    def load_verdict(self, room_id: str) -> str | None:
        target = self.verdict_file(room_id)
        if not target.exists():
            return None
        return target.read_text(encoding="utf-8")

    def load_room(self, room_id: str) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any] | None]:
        room = json.loads(self.room_file(room_id).read_text(encoding="utf-8"))
        transcript_lines: list[dict[str, Any]] = []
        tfile = self.transcript_file(room_id)
        if tfile.exists():
            for raw in tfile.read_text(encoding="utf-8").splitlines():
                if raw.strip():
                    transcript_lines.append(json.loads(raw))
        summary = None
        sfile = self.summary_file(room_id)
        if sfile.exists():
            summary = json.loads(sfile.read_text(encoding="utf-8"))
        return room, transcript_lines, summary

    def discover_rooms(self) -> list[str]:
        ids: list[str] = []
        for child in self.base_dir.iterdir():
            if child.is_dir() and (child / "room.json").exists():
                ids.append(child.name)
        return sorted(ids)

    def delete_room(self, room_id: str) -> None:
        target = self.room_dir(room_id).resolve()
        base = self.base_dir.resolve()
        if base not in target.parents:
            raise ValueError(f"Refusing to delete outside rooms base dir: {target}")
        if target.exists():
            shutil.rmtree(target)
