from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class ParticipantPromptView:
    participant_id: str
    display_name: str
    kind: str
    last_content: str


@dataclass(slots=True)
class RoomFrameInput:
    topic: str
    phase_name: str
    phase_instruction: str
    participants: list[ParticipantPromptView]
    self_view: ParticipantPromptView


@dataclass(slots=True)
class DeltaInput:
    phase_name: str
    round_number: int
    include_opponents: bool
    opponents: list[ParticipantPromptView]
    phase_instruction: str


@dataclass(slots=True)
class PromptRenderer:
    room_frame_template: str
    delta_template: str

    def render_room_frame(self, payload: RoomFrameInput) -> str:
        from jinja2 import Environment

        env = Environment(autoescape=False, trim_blocks=True, lstrip_blocks=True)
        template = env.from_string(self.room_frame_template)
        return template.render(payload=payload).strip() + "\n"

    def render_delta(self, payload: DeltaInput) -> str:
        from jinja2 import Environment

        env = Environment(autoescape=False, trim_blocks=True, lstrip_blocks=True)
        template = env.from_string(self.delta_template)
        return template.render(payload=payload).strip() + "\n"


ROOM_FRAME_TEMPLATE = """
[AGORA ROOM FRAME]
You are a participant in a structured debate room called "Agora".
Other participants: {% for p in payload.participants %}{{ p.display_name }}{% if not loop.last %}, {% endif %}{% endfor %}.
Your identity in this room: {{ payload.self_view.display_name }} ({{ payload.self_view.kind }}).

Rules:
- If the brief is ambiguous, state your working interpretation at the top and proceed.
- Do not ask the room for clarification - commit to your interpretation.
- Keep arguments concrete and falsifiable.
- Address opponents by their room id when replying to them.

[BRIEF]
{{ payload.topic }}

[PHASE - {{ payload.phase_name }}]
{{ payload.phase_instruction }}

Respond now.
"""


DELTA_TEMPLATE = """
[PHASE - {{ payload.phase_name }}, round {{ payload.round_number }}]

{% if payload.include_opponents %}
Opponents' latest contributions:
{% for p in payload.opponents %}
--- {{ p.display_name }} ---
{{ p.last_content }}
{% endfor %}
{% endif %}

{{ payload.phase_instruction }}

Respond now.
"""


def default_renderer() -> PromptRenderer:
    return PromptRenderer(room_frame_template=ROOM_FRAME_TEMPLATE, delta_template=DELTA_TEMPLATE)
