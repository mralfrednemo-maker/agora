from __future__ import annotations

from dataclasses import dataclass

from agora.drivers.web_base import WebBrowserDriver

GEMINI_SCRIPT = "C:/Users/chris/PROJECTS/the-thinker/browser-automation/test_gemini_upload.py"


@dataclass(slots=True)
class GeminiWebDriver(WebBrowserDriver):
    id: str = "gemini-web-1"
    display_name: str = "Gemini Web"
    script_path: str = GEMINI_SCRIPT
    token_ceiling: int = 900_000

    @property
    def _kind_tag(self) -> str:
        return "gemini-web"
