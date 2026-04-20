from __future__ import annotations

from dataclasses import dataclass

from agora.drivers.web_base import WebBrowserDriver

CHATGPT_SCRIPT = "C:/Users/chris/PROJECTS/the-thinker/browser-automation/test_chatgpt_upload.py"


@dataclass(slots=True)
class ChatGPTWebDriver(WebBrowserDriver):
    id: str = "chatgpt-web-1"
    display_name: str = "ChatGPT Web"
    script_path: str = CHATGPT_SCRIPT
    token_ceiling: int = 128_000

    @property
    def _kind_tag(self) -> str:
        return "chatgpt-web"
