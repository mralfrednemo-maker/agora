from __future__ import annotations

from dataclasses import dataclass

from agora.drivers.web_base import WebBrowserDriver

CLAUDE_SCRIPT = "C:/Users/chris/PROJECTS/the-thinker/browser-automation/test_claude_research.py"


@dataclass(slots=True)
class ClaudeWebDriver(WebBrowserDriver):
    id: str = "claude-web-1"
    display_name: str = "Claude Web"
    script_path: str = CLAUDE_SCRIPT
    token_ceiling: int = 200_000

    @property
    def _kind_tag(self) -> str:
        return "claude-web"
