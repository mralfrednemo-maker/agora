from __future__ import annotations

from agora.config.phases import (
    DEFAULT_PHASES_M2,
    STYLE_ROUND_CAPS,
    phase_by_name,
    phases_for_style,
    phases_for_total_rounds,
)


def test_default_phases_are_m2_order() -> None:
    names = [phase.name for phase in DEFAULT_PHASES_M2]
    assert names == ["positions", "contrarian", "debate", "verdict"]


def test_phase_modes_match_spec() -> None:
    modes = {phase.name: phase.mode for phase in DEFAULT_PHASES_M2}
    assert modes["positions"] == "parallel"
    assert modes["contrarian"] == "parallel"
    assert modes["debate"] == "serial"
    assert modes["verdict"] == "parallel"


def test_round_budget_for_total_four() -> None:
    phases = phases_for_total_rounds(4)
    rounds = {phase.name: phase.max_rounds for phase in phases}
    assert rounds == {"positions": 1, "contrarian": 1, "debate": 1, "verdict": 1}


def test_round_budget_for_ein_mdp_capped_at_five() -> None:
    phases = phases_for_style("ein-mdp", 9)
    rounds = {phase.name: phase.max_rounds for phase in phases}
    assert rounds["debate"] == 2


def test_phase_lookup() -> None:
    assert phase_by_name("debate") is not None
    assert phase_by_name("unknown") is None


def test_style_round_caps_match_spec() -> None:
    assert STYLE_ROUND_CAPS["ein-mdp"] == 5
    assert STYLE_ROUND_CAPS["critic-terminate"] == 15


def test_critic_terminate_phase_shape_for_total_eight() -> None:
    phases = phases_for_style("critic-terminate", 8)
    names = [phase.name for phase in phases]
    modes = [phase.mode for phase in phases]
    rounds = {phase.name: phase.max_rounds for phase in phases}
    assert names == ["positions", "critic", "debate", "synthesis"]
    assert modes == ["parallel", "serial", "serial", "parallel"]
    assert rounds["debate"] == 5


def test_critic_terminate_round_budget_caps_at_fifteen() -> None:
    phases = phases_for_style("critic-terminate", 20)
    rounds = {phase.name: phase.max_rounds for phase in phases}
    assert rounds["debate"] == 12
