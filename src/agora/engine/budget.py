from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

try:
    import tiktoken
except Exception:  # pragma: no cover - fallback used only if tiktoken missing
    tiktoken = None

from agora.engine.transcript import TranscriptEntry

_TIKTOKEN_BROKEN = False


def _approx_tokens(text: str) -> int:
    return max(1, len(text) // 4)


@dataclass(slots=True)
class TokenBudget:
    ceiling: int
    encoder_name: str = "cl100k_base"

    def count(self, text: str) -> int:
        global _TIKTOKEN_BROKEN
        if tiktoken is None or _TIKTOKEN_BROKEN:
            return _approx_tokens(text)
        try:
            encoding = tiktoken.get_encoding(self.encoder_name)
            return len(encoding.encode(text))
        except Exception:
            _TIKTOKEN_BROKEN = True
            return _approx_tokens(text)

    @property
    def hard_cap(self) -> int:
        return max(1, int(self.ceiling * 0.8))


def truncate_transcript(entries: list[TranscriptEntry], participant_ids: list[str]) -> list[TranscriptEntry]:
    if not entries:
        return []
    rounds = sorted({entry.round for entry in entries})
    keep_rounds = set(rounds[-2:])
    keep: list[TranscriptEntry] = []

    opening_kept: set[str] = set()
    for entry in entries:
        if entry.round == 1 and entry.participant_id in participant_ids and entry.participant_id not in opening_kept:
            keep.append(entry)
            opening_kept.add(entry.participant_id)
            continue
        if entry.round in keep_rounds:
            keep.append(entry)
    return keep


@dataclass(slots=True)
class BudgetManager:
    summarizer: Callable[[list[TranscriptEntry]], str]

    def fit(
        self,
        prompt_text: str,
        budget: TokenBudget,
        entries: list[TranscriptEntry],
        participant_ids: list[str],
    ) -> tuple[int, list[TranscriptEntry], str | None]:
        tokens = budget.count(prompt_text)
        if tokens <= budget.hard_cap:
            return tokens, entries, None

        truncated = truncate_transcript(entries, participant_ids)
        summary_stub = None
        if truncated != entries:
            summary_stub = self.summarizer(entries)
        return tokens, truncated, summary_stub


def stub_summarizer(_: list[TranscriptEntry]) -> str:
    return "[M1 STUB SUMMARY] Prior rounds were compressed due to token budget constraints."
