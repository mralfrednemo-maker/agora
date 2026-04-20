from __future__ import annotations

from dataclasses import dataclass
from math import ceil

from agora.engine.transcript import TranscriptEntry


def _last_non_empty_line(text: str) -> str:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return lines[-1] if lines else ""


@dataclass(frozen=True, slots=True)
class ConvergenceCheck:
    name: str

    def check(self, entries: list[TranscriptEntry]) -> bool:
        lowered = self.name.lower()
        if lowered == "agree-marker":
            return bool(entries) and all(_last_non_empty_line(e.content) == "AGREE" for e in entries)
        if lowered == "consensus-prefix":
            return bool(entries) and all(e.content.lstrip().startswith("CONSENSUS:") for e in entries)
        if lowered == "disagree-absent":
            return bool(entries) and all("DISAGREE" not in e.content for e in entries)
        if lowered == "terminate-majority":
            threshold = ceil(len(entries) / 2)
            terminate_votes = sum(1 for entry in entries if _last_non_empty_line(entry.content) == "TERMINATE")
            return terminate_votes >= threshold
        if lowered == "none":
            return False
        raise ValueError(f"Unknown convergence check: {self.name}")


def build_convergence(name: str) -> ConvergenceCheck:
    return ConvergenceCheck(name=name)
