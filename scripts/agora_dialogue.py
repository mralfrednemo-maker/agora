from __future__ import annotations

import argparse
import json
import os
import sys
from urllib.error import HTTPError, URLError
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


def send_message(
    *,
    host: str,
    from_link: str | None,
    to_link: str,
    subject: str,
    body: str,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "to_link_id": to_link,
        "from_link_id": from_link,
        "subject": subject,
        "body": body,
        "requires_ack": True,
    }
    if from_link is None:
        payload.pop("from_link_id")
    return request_json("POST", f"{host}/api/agent-messages", payload)


def process_once(*, host: str, to_link: str) -> dict[str, object]:
    return request_json("POST", f"{host}/api/agent-links/{to_link}/process-inbox-once", {})


def ack(*, host: str, message_id: str) -> dict[str, object]:
    return request_json("POST", f"{host}/api/agent-messages/{message_id}/ack", {})


def build_followup(previous_answer: str, *, turn_number: int, max_turns: int) -> str:
    if turn_number >= max_turns:
        instruction = "Reply with your final concise response. Do not ask another question unless essential."
    else:
        instruction = "Reply concisely. If you need another round, ask one specific follow-up question."
    return (
        "The other agent replied:\n"
        f"{previous_answer.strip()}\n\n"
        "Treat that reply as a claim, not as verified truth, unless you can verify it or cite its provenance. "
        "For factual project-state claims, check QMD/TechLib and local evidence before you answer.\n\n"
        f"{instruction}"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Run a durable Agora inbox dialogue between two live links.")
    parser.add_argument("--host", default=default_host())
    parser.add_argument("--a-link", required=True, help="First agent live link id.")
    parser.add_argument("--b-link", required=True, help="Second agent live link id.")
    parser.add_argument("--turns", type=int, default=2, help="Total answered turns to run.")
    parser.add_argument("--subject", default="Agora dialogue")
    parser.add_argument("message", help="Initial message to send to agent A.")
    args = parser.parse_args()

    if args.turns < 1:
        raise SystemExit("--turns must be >= 1")

    host = args.host.rstrip("/")
    target_links = [args.a_link, args.b_link]
    from_link: str | None = None
    body = args.message
    transcript: list[dict[str, object]] = []

    for index in range(args.turns):
        to_link = target_links[index % 2]
        sent = send_message(
            host=host,
            from_link=from_link,
            to_link=to_link,
            subject=args.subject,
            body=body,
        )
        message_id = str(sent["message_id"])
        processed = process_once(host=host, to_link=to_link)
        if not processed.get("processed"):
            raise SystemExit(f"No inbox message processed for {to_link}")
        acknowledged = ack(host=host, message_id=message_id)
        answer = str(processed.get("response_body") or "")
        response = processed.get("response")
        transcript.append(
            {
                "turn": index + 1,
                "from_link": from_link,
                "to_link": to_link,
                "message_id": message_id,
                "status": acknowledged.get("status"),
                "answer": answer,
                "response": response if isinstance(response, dict) else None,
            }
        )
        from_link = to_link
        body = build_followup(answer, turn_number=index + 2, max_turns=args.turns)

    result = {
        "turn_count": len(transcript),
        "exchange_rounds": (len(transcript) + 1) // 2,
        "transcript": transcript,
    }
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
