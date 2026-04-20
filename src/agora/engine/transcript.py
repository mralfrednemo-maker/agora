from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


@dataclass(slots=True)
class TranscriptEntry:
    seq: int
    ts: str
    phase: str
    round: int
    participant_id: str
    participant_kind: str
    role: str
    content: str
    tokens_in: int
    tokens_out: int
    latency_ms: int
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TranscriptEntry:
        return cls(**data)


@dataclass(slots=True)
class Transcript:
    entries: list[TranscriptEntry] = field(default_factory=list)

    def next_seq(self) -> int:
        return len(self.entries) + 1

    def append(self, entry: TranscriptEntry) -> None:
        self.entries.append(entry)

    def by_phase_round(self, phase: str, round_number: int) -> list[TranscriptEntry]:
        return [e for e in self.entries if e.phase == phase and e.round == round_number and e.role == "participant"]

    def latest_by_participant(self, participant_id: str) -> TranscriptEntry | None:
        for entry in reversed(self.entries):
            if entry.participant_id == participant_id and entry.role == "participant":
                return entry
        return None

    def round_entries_for_participant(
        self,
        phase: str,
        round_number: int,
        participant_order: list[str],
        participant_id: str,
    ) -> list[TranscriptEntry]:
        spoken_before: list[str] = []
        for pid in participant_order:
            if pid == participant_id:
                break
            spoken_before.append(pid)
        return [
            e
            for e in self.entries
            if e.phase == phase and e.round == round_number and e.participant_id in spoken_before and e.role == "participant"
        ]

    def to_jsonable(self) -> list[dict[str, Any]]:
        return [entry.to_dict() for entry in self.entries]

    @classmethod
    def from_jsonable(cls, data: list[dict[str, Any]]) -> Transcript:
        return cls(entries=[TranscriptEntry.from_dict(item) for item in data])


def make_entry(
    seq: int,
    phase: str,
    round_number: int,
    participant_id: str,
    participant_kind: str,
    role: str,
    content: str,
    tokens_in: int,
    tokens_out: int,
    latency_ms: int,
    error: str | None = None,
) -> TranscriptEntry:
    return TranscriptEntry(
        seq=seq,
        ts=utc_now_iso(),
        phase=phase,
        round=round_number,
        participant_id=participant_id,
        participant_kind=participant_kind,
        role=role,
        content=content,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        latency_ms=latency_ms,
        error=error,
    )
