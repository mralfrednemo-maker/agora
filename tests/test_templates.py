from __future__ import annotations

from agora.engine.templates import DeltaInput, ParticipantPromptView, RoomFrameInput, default_renderer


def test_room_frame_template_includes_core_sections() -> None:
    renderer = default_renderer()
    payload = RoomFrameInput(
        topic="Test topic",
        phase_name="positions",
        phase_instruction="State your position.",
        participants=[ParticipantPromptView("p2", "Bob", "codex", "")],
        self_view=ParticipantPromptView("p1", "Alice", "fake", ""),
    )
    text = renderer.render_room_frame(payload)
    assert "[AGORA ROOM FRAME]" in text
    assert "[BRIEF]" in text
    assert "[PHASE - positions]" in text
    assert "Respond now." in text


def test_delta_template_with_opponents() -> None:
    renderer = default_renderer()
    payload = DeltaInput(
        phase_name="debate",
        round_number=2,
        include_opponents=True,
        opponents=[ParticipantPromptView("p2", "Bob", "codex", "Counterpoint.")],
        phase_instruction="Respond directly.",
    )
    text = renderer.render_delta(payload)
    assert "[YOUR LAST CONTRIBUTION — PREVIOUS ROUND]" in text
    assert "[OPPONENTS' LAST CONTRIBUTIONS — PREVIOUS ROUND]" in text
    assert "--- Bob ---" in text
    assert "Counterpoint." in text
    assert "[PHASE - debate, round 2]" in text


def test_delta_template_without_opponents() -> None:
    renderer = default_renderer()
    payload = DeltaInput(
        phase_name="positions",
        round_number=1,
        include_opponents=False,
        opponents=[],
        phase_instruction="Opening statement.",
    )
    text = renderer.render_delta(payload)
    assert "[YOUR LAST CONTRIBUTION — PREVIOUS ROUND]" in text
    assert "[OPPONENTS' LAST CONTRIBUTIONS — PREVIOUS ROUND]" not in text
    assert "Opening statement." in text
