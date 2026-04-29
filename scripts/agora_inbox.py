from __future__ import annotations

import argparse
import json
import os
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


def default_host() -> str:
    explicit = os.environ.get("AGORA_GATEWAY_URL") or os.environ.get("AGORA_URL")
    if explicit:
        return explicit.rstrip("/")
    bind_host = os.environ.get("AGORA_BIND_HOST", "127.0.0.1")
    port = os.environ.get("AGORA_PORT", "8890")
    return f"http://{bind_host}:{port}"


def request_json(method: str, url: str, payload: dict[str, object] | None = None) -> dict[str, object]:
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = Request(
        url,
        data=data,
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method=method,
    )
    try:
        with urlopen(request, timeout=330) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise SystemExit(f"Agora returned HTTP {exc.code}: {body}") from exc
    except URLError as exc:
        raise SystemExit(f"Could not reach Agora gateway: {exc}") from exc


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Agora durable agent inbox CLI.")
    parser.add_argument("--host", default=default_host(), help="Agora gateway URL.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    send = subparsers.add_parser("send", help="Send a durable inbox message to a live link.")
    send.add_argument("--to-link", required=True)
    send.add_argument("--from-link")
    send.add_argument("--subject")
    send.add_argument("--no-ack", action="store_true")
    send.add_argument("body")

    list_cmd = subparsers.add_parser("list", help="List inbox messages.")
    list_cmd.add_argument("--to-link")
    list_cmd.add_argument("--status")
    list_cmd.add_argument("--open", action="store_true", help="Show only delivered/read messages.")
    list_cmd.add_argument("--limit", type=int, default=50)

    read = subparsers.add_parser("read", help="Mark a message read.")
    read.add_argument("message_id")

    ack = subparsers.add_parser("ack", help="Acknowledge an answered message.")
    ack.add_argument("message_id")

    process = subparsers.add_parser("process-once", help="Wake an agent for one pending inbox message.")
    process.add_argument("--to-link", required=True)

    loop = subparsers.add_parser("loop", help="Poll and process an agent inbox.")
    loop.add_argument("--to-link", required=True)
    loop.add_argument("--interval", type=float, default=5.0)
    loop.add_argument("--max-iterations", type=int, default=0, help="0 means run forever.")

    return parser


def main() -> int:
    args = build_parser().parse_args()
    host = args.host.rstrip("/")

    if args.command == "send":
        payload: dict[str, object] = {
            "to_link_id": args.to_link,
            "body": args.body,
            "requires_ack": not args.no_ack,
        }
        if args.from_link:
            payload["from_link_id"] = args.from_link
        if args.subject:
            payload["subject"] = args.subject
        result = request_json("POST", f"{host}/api/agent-messages", payload)
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0

    if args.command == "list":
        params: dict[str, object] = {"limit": args.limit, "include_terminal": not args.open}
        if args.to_link:
            params["to_link_id"] = args.to_link
        if args.status:
            params["status"] = args.status
        result = request_json("GET", f"{host}/api/agent-messages?{urlencode(params)}")
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0

    if args.command == "read":
        result = request_json("POST", f"{host}/api/agent-messages/{args.message_id}/read", {})
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0

    if args.command == "ack":
        result = request_json("POST", f"{host}/api/agent-messages/{args.message_id}/ack", {})
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0

    if args.command == "process-once":
        result = request_json("POST", f"{host}/api/agent-links/{args.to_link}/process-inbox-once", {})
        print(json.dumps(result, indent=2, ensure_ascii=False))
        if result.get("processed") and result.get("status") != "answered":
            print(
                "\nAgora rejected the target agent response. Do not infer the cause from the prompt text alone; "
                "inspect error_text and the message row.",
                file=sys.stderr,
            )
        return 0

    if args.command == "loop":
        iterations = 0
        while args.max_iterations <= 0 or iterations < args.max_iterations:
            iterations += 1
            result = request_json("POST", f"{host}/api/agent-links/{args.to_link}/process-inbox-once", {})
            print(json.dumps(result, ensure_ascii=False))
            if not result.get("processed"):
                time.sleep(max(0.25, args.interval))
        return 0

    return 2


if __name__ == "__main__":
    sys.exit(main())
