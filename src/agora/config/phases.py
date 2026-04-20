from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True, slots=True)
class Phase:
    name: str
    mode: Literal["parallel", "serial"]
    instruction_template: str
    max_rounds: int
    include_opponents: bool


EIN_MDP_PHASES: list[Phase] = [
    Phase(
        name="positions",
        mode="parallel",
        max_rounds=1,
        include_opponents=False,
        instruction_template=(
            "State your opening position on the brief. "
            "If the brief is ambiguous, state your working interpretation at the top and proceed - "
            "do not refuse to take a position, do not stall. "
            "Max 400 words. No hedging."
        ),
    ),
    Phase(
        name="contrarian",
        mode="parallel",
        max_rounds=1,
        include_opponents=True,
        instruction_template=(
            "You have now seen all opponents' opening positions (below). "
            "For each opponent, state the single strongest objection you can raise against their opening. "
            "One specific objection per opponent, named. Max 150 words per objection."
        ),
    ),
    Phase(
        name="debate",
        mode="serial",
        max_rounds=1,
        include_opponents=True,
        instruction_template=(
            "Respond to opponents' latest points. Defend your position where attacked. "
            "Concede where they are right. Identify residual disagreements explicitly. "
            "Max 400 words."
        ),
    ),
    Phase(
        name="verdict",
        mode="parallel",
        max_rounds=1,
        include_opponents=True,
        instruction_template=(
            "State: (a) what you now agree with, naming each participant; "
            "(b) what you still disagree on and why. "
            "End your message with a final line reading exactly `AGREE` or `DISAGREE: <one-line reason>`."
        ),
    ),
]
DEFAULT_PHASES_M2 = EIN_MDP_PHASES

CRITIC_TERMINATE_PHASES: list[Phase] = [
    Phase(
        name="positions",
        mode="parallel",
        max_rounds=1,
        include_opponents=False,
        instruction_template=(
            "State your opening position on the brief. "
            "If the brief is ambiguous, state your working interpretation and proceed. "
            "400 words max. End with `TERMINATE` if you believe no further debate is needed."
        ),
    ),
    Phase(
        name="critic",
        mode="serial",
        max_rounds=1,
        include_opponents=True,
        instruction_template=(
            "Your role this round: name the single strongest unresolved objection to each opponent's position. "
            "Be specific. 150 words per objection. "
            "End with `TERMINATE` if you are satisfied nothing material remains."
        ),
    ),
    Phase(
        name="debate",
        mode="serial",
        max_rounds=1,
        include_opponents=True,
        instruction_template=(
            "Respond to the latest Critic objection against you. Defend where attacked; concede where right. "
            "300 words max. End with `TERMINATE` if no residual disagreement."
        ),
    ),
    Phase(
        name="synthesis",
        mode="parallel",
        max_rounds=1,
        include_opponents=True,
        instruction_template=(
            "Summarise the shared position you and the opponents now hold. "
            "Identify residual disagreements if any. "
            "400 words max. End with `TERMINATE`."
        ),
    ),
]

DEFAULT_STYLE = "ein-mdp"
STYLE_ROUND_CAPS: dict[str, int] = {
    "ein-mdp": 5,
    "critic-terminate": 15,
}
MIN_TOTAL_ROUNDS = 4
DEFAULT_TOTAL_ROUNDS = 5


def _clone_with_debate_rounds(phases: list[Phase], debate_rounds: int) -> list[Phase]:
    output: list[Phase] = []
    for phase in phases:
        rounds = debate_rounds if phase.name == "debate" else phase.max_rounds
        output.append(
            Phase(
                name=phase.name,
                mode=phase.mode,
                instruction_template=phase.instruction_template,
                max_rounds=rounds,
                include_opponents=phase.include_opponents,
            )
        )
    return output


def phases_for_style(style: str, max_total_rounds: int) -> list[Phase]:
    selected_style = style.strip().lower() if style else DEFAULT_STYLE
    cap = STYLE_ROUND_CAPS.get(selected_style, STYLE_ROUND_CAPS[DEFAULT_STYLE])
    total = max(MIN_TOTAL_ROUNDS, min(cap, max_total_rounds))
    debate_rounds = max(1, total - 3)
    if selected_style == "critic-terminate":
        return _clone_with_debate_rounds(CRITIC_TERMINATE_PHASES, debate_rounds)
    return _clone_with_debate_rounds(EIN_MDP_PHASES, debate_rounds)


def phases_for_total_rounds(max_total_rounds: int) -> list[Phase]:
    # Backward compatible helper kept for legacy code paths.
    return phases_for_style(DEFAULT_STYLE, max_total_rounds)


def phase_by_name(name: str) -> Phase | None:
    lowered = name.strip().lower()
    for phase in EIN_MDP_PHASES + CRITIC_TERMINATE_PHASES:
        if phase.name == lowered:
            return phase
    return None
