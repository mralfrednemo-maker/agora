from __future__ import annotations

import pytest

from agora.engine.convergence import build_convergence
from agora.engine.transcript import make_entry


def _entry(content: str):
    return make_entry(1, "verdict", 1, "p1", "fake", "participant", content, 1, 1, 1)


def test_agree_marker() -> None:
    check = build_convergence("agree-marker")
    assert check.check([_entry("x\nAGREE"), _entry("y\nAGREE")]) is True
    assert check.check([_entry("x\nDISAGREE")]) is False


def test_consensus_prefix() -> None:
    check = build_convergence("consensus-prefix")
    assert check.check([_entry("CONSENSUS: yes"), _entry("CONSENSUS: ok")]) is True
    assert check.check([_entry("No")]) is False


def test_disagree_absent() -> None:
    check = build_convergence("disagree-absent")
    assert check.check([_entry("All good"), _entry("Also good")]) is True
    assert check.check([_entry("DISAGREE: no")]) is False


def test_none_never_converges() -> None:
    check = build_convergence("none")
    assert check.check([_entry("AGREE")]) is False


def test_terminate_majority_two_of_three() -> None:
    check = build_convergence("terminate-majority")
    assert check.check([_entry("one\nTERMINATE"), _entry("two\nTERMINATE"), _entry("three\nkeep going")]) is True


def test_terminate_majority_one_of_three_not_enough() -> None:
    check = build_convergence("terminate-majority")
    assert check.check([_entry("one\nTERMINATE"), _entry("two\ncontinue"), _entry("three\ncontinue")]) is False


@pytest.mark.parametrize("name", ["agree-marker", "consensus-prefix", "disagree-absent", "terminate-majority", "none"])
def test_build_convergence_known(name: str) -> None:
    check = build_convergence(name)
    assert check.name == name


def test_unknown_convergence_raises() -> None:
    check = build_convergence("not-a-real-check")
    with pytest.raises(ValueError):
        check.check([_entry("anything")])
