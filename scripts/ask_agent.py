from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
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


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Compatibility wrapper for contacting an Agora-linked agent session. "
            "Defaults to durable inbox messaging; use --legacy-one-shot only for the old handover artifact flow."
        )
    )
    parser.add_argument("source_session_ref", help="Conversation/session id or title, for example LISTORA.")
    parser.add_argument("question", help="Question to ask the source conversation.")
    parser.add_argument(
        "--source-driver",
        default=None,
        help="Agora source driver id. If omitted, Codex session ids/titles are detected; otherwise Claude MiniMax is used.",
    )
    parser.add_argument(
        "--interviewer-driver",
        default="codex-1",
        help="Agora interviewer driver id. Default: codex-1.",
    )
    parser.add_argument(
        "--interviewer-session",
        default=None,
        help="Optional interviewer conversation id. If omitted, Agora reuses the first active interviewer link.",
    )
    parser.add_argument("--host", default=default_host(), help="Agora gateway URL.")
    parser.add_argument("--max-runtime-minutes", type=int, default=10)
    parser.add_argument("--json", action="store_true", help="Print the full JSON response.")
    parser.add_argument(
        "--legacy-one-shot",
        action="store_true",
        help="Use the old /api/ask-agent final-artifact handover path.",
    )
    return parser.parse_args()


def post_json(url: str, payload: dict[str, object]) -> dict[str, object]:
    data = json.dumps(payload).encode("utf-8")
    request = Request(
        url,
        data=data,
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=max(60, int(payload.get("max_runtime_minutes", 10)) * 60 + 30)) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise SystemExit(f"Agora returned HTTP {exc.code}: {body}") from exc
    except URLError as exc:
        raise SystemExit(f"Could not reach Agora gateway: {exc}") from exc


def get_json(url: str, params: dict[str, object] | None = None) -> dict[str, object]:
    if params:
        url = f"{url}?{urlencode(params)}"
    request = Request(url, headers={"Accept": "application/json"}, method="GET")
    try:
        with urlopen(request, timeout=60) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise SystemExit(f"Agora returned HTTP {exc.code}: {body}") from exc
    except URLError as exc:
        raise SystemExit(f"Could not reach Agora gateway: {exc}") from exc


def codex_session_record(source_session_ref: str) -> dict[str, object] | None:
    index_path = Path.home() / ".codex" / "session_index.jsonl"
    if not index_path.exists():
        return None
    best: dict[str, object] | None = None
    for line in index_path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        record_id = str(record.get("id") or "")
        thread_name = str(record.get("thread_name") or "")
        if record_id == source_session_ref or thread_name.casefold() == source_session_ref.casefold():
            if best is None or str(record.get("updated_at") or "") > str(best.get("updated_at") or ""):
                best = record
    return best


def resolve_codex_session_ref(source_session_ref: str) -> tuple[str, str | None]:
    """Resolve a Codex thread title from the local Codex session index."""

    if "-" in source_session_ref and len(source_session_ref) >= 32:
        return source_session_ref, None

    index_path = Path.home() / ".codex" / "session_index.jsonl"
    if not index_path.exists():
        return source_session_ref, None

    matches: list[dict[str, object]] = []
    for line in index_path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if str(record.get("thread_name") or "").casefold() == source_session_ref.casefold():
            matches.append(record)

    if not matches:
        return source_session_ref, None

    matches.sort(key=lambda item: str(item.get("updated_at") or ""), reverse=True)
    resolved = str(matches[0].get("id") or source_session_ref)
    label = str(matches[0].get("thread_name") or source_session_ref)
    return resolved, label


def infer_source_driver(source_session_ref: str, explicit_driver: str | None) -> str:
    if explicit_driver:
        return explicit_driver
    if codex_session_record(source_session_ref) is not None:
        return "codex-1"
    return "claude-code-new-1"


def driver_kind(driver_id: str) -> str:
    if driver_id == "codex-1":
        return "codex"
    if driver_id == "gemini-cli-1":
        return "gemini-cli"
    if driver_id == "claude-code-new-1":
        return "claude-code-new"
    return driver_id


def find_live_link(host: str, *, driver_id: str, canonical_ref: str) -> dict[str, object] | None:
    result = get_json(f"{host}/api/live-links")
    for link in result.get("live_links", []):
        if not isinstance(link, dict):
            continue
        if link.get("driver_id") == driver_id and link.get("canonical_external_session_ref") == canonical_ref:
            return link
    return None


def first_interviewer_link(host: str, *, driver_id: str, exclude_link_id: str | None = None) -> dict[str, object] | None:
    result = get_json(f"{host}/api/live-links")
    for link in result.get("live_links", []):
        if not isinstance(link, dict):
            continue
        if link.get("driver_id") == driver_id and link.get("id") != exclude_link_id:
            return link
    return None


def ensure_live_link(host: str, *, driver_id: str, session_ref: str, label: str) -> dict[str, object]:
    existing = find_live_link(host, driver_id=driver_id, canonical_ref=session_ref)
    if existing is not None:
        return existing
    post_json(
        f"{host}/api/live-links/attach",
        {
            "label": label,
            "driver_id": driver_id,
            "external_session_ref": session_ref,
        },
    )
    existing = find_live_link(host, driver_id=driver_id, canonical_ref=session_ref)
    if existing is None:
        raise SystemExit(f"Attached {driver_id}:{session_ref}, but live link was not found afterward.")
    return existing


def get_agent_message(host: str, message_id: str) -> dict[str, object] | None:
    result = get_json(f"{host}/api/agent-messages", {"limit": 1, "include_terminal": True})
    for message in result.get("messages", []):
        if isinstance(message, dict) and message.get("id") == message_id:
            return message
    result = get_json(f"{host}/api/agent-messages", {"limit": 200, "include_terminal": True})
    for message in result.get("messages", []):
        if isinstance(message, dict) and message.get("id") == message_id:
            return message
    return None


def expected_inbox_schema(message_id: str) -> dict[str, object]:
    return {
        "type": "ANSWER",
        "body": {
            "answer": "...",
            "provenance": {
                "qmd_techlib": {"checked": True, "query": "...", "result": "..."},
                "local_filesystem": {"checked": True, "paths": ["..."], "result": "..."},
                "git": {"checked": True, "repos_or_commits": ["..."], "result": "..."},
                "agora_ledger": {"checked": True, "ids": ["..."], "result": "..."},
                "unverified_claims": [],
                "confidence": "high",
            },
        },
        "references": [],
        "in_reply_to_agent_message_id": message_id,
    }


def run_legacy(args: argparse.Namespace, host: str, source_driver: str, source_session_ref: str, source_label: str | None) -> dict[str, object]:
    payload: dict[str, object] = {
        "question": args.question,
        "source_driver_id": source_driver,
        "source_session_ref": source_session_ref,
        "interviewer_driver_id": args.interviewer_driver,
        "max_runtime_minutes": args.max_runtime_minutes,
    }
    if source_label and source_label != source_session_ref:
        payload["source_label"] = f"Codex {source_label} source"
    if args.interviewer_session:
        payload["interviewer_session_ref"] = args.interviewer_session
    return post_json(f"{host}/api/ask-agent", payload)


def run_durable_inbox(args: argparse.Namespace, host: str, source_driver: str, source_session_ref: str, source_label: str | None) -> dict[str, object]:
    label = f"{driver_kind(source_driver)} source"
    if source_label:
        label = f"{driver_kind(source_driver)} {source_label} source"
    source_link = ensure_live_link(host, driver_id=source_driver, session_ref=source_session_ref, label=label)
    source_link_id = str(source_link["id"])
    interviewer = first_interviewer_link(host, driver_id=args.interviewer_driver, exclude_link_id=source_link_id)
    from_link_id = str(interviewer["id"]) if interviewer else None
    sent = post_json(
        f"{host}/api/agent-messages",
        {
            "from_link_id": from_link_id,
            "to_link_id": source_link_id,
            "subject": "Agora contact request",
            "body": args.question,
            "requires_ack": True,
        },
    )
    processed = post_json(f"{host}/api/agent-links/{source_link_id}/process-inbox-once", {})
    message_id = str(sent["message_id"])
    final_row = get_agent_message(host, message_id)
    acknowledged: dict[str, object] | None = None
    if processed.get("status") == "answered":
        acknowledged = post_json(f"{host}/api/agent-messages/{message_id}/ack", {})
        final_row = get_agent_message(host, message_id) or final_row
    error_text = processed.get("error_text")
    if final_row and isinstance(final_row, dict):
        error_text = error_text or final_row.get("error_text")
    return {
        "mode": "durable_inbox",
        "source_link_id": source_link_id,
        "message_id": message_id,
        "receipt_trail": {
            "delivered": sent.get("status") == "delivered",
            "read": bool(processed.get("processed")),
            "answered": processed.get("status") == "answered",
            "acknowledged": bool(acknowledged and acknowledged.get("status") == "acknowledged"),
        },
        "rounds_exchanged": 1,
        "status": processed.get("status"),
        "answer": processed.get("response_body"),
        "response": processed.get("response"),
        "error_text": error_text,
        "expected_schema": expected_inbox_schema(message_id) if processed.get("status") != "answered" else None,
        "diagnostic": (
            "The target agent response was not accepted by Agora. Inspect error_text and expected_schema; "
            "do not infer a cause without the failed row's error_text/response/raw output."
            if processed.get("status") != "answered"
            else None
        ),
        "raw": {
            "sent": sent,
            "processed": processed,
            "acknowledged": acknowledged,
            "final_message": final_row,
        },
    }


def main() -> int:
    args = parse_args()
    host = args.host.rstrip("/")
    source_session_ref = args.source_session_ref
    source_label: str | None = None
    source_driver = infer_source_driver(source_session_ref, args.source_driver)
    if source_driver == "codex-1":
        source_session_ref, source_label = resolve_codex_session_ref(source_session_ref)

    if args.legacy_one_shot:
        result = run_legacy(args, host, source_driver, source_session_ref, source_label)
    else:
        result = run_durable_inbox(args, host, source_driver, source_session_ref, source_label)

    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0

    if result.get("mode") == "durable_inbox":
        print(f"mode: {result.get('mode')}")
        print(f"source_link_id: {result.get('source_link_id')}")
        print(f"message_id: {result.get('message_id')}")
        if result.get("status"):
            print(f"status: {result.get('status')}")
        print(f"rounds_exchanged: {result.get('rounds_exchanged')}")
        print(f"receipt_trail: {json.dumps(result.get('receipt_trail'), ensure_ascii=False)}")
        if result.get("error_text"):
            print(f"error_text: {result.get('error_text')}")
        if result.get("diagnostic"):
            print(f"diagnostic: {result.get('diagnostic')}")
        if result.get("expected_schema"):
            print("expected_schema:")
            print(json.dumps(result.get("expected_schema"), indent=2, ensure_ascii=False))
        answer = str(result.get("answer") or "").strip()
        if answer:
            print()
            print(answer)
        response = result.get("response")
        if isinstance(response, dict):
            body = response.get("body")
            provenance = body.get("provenance") if isinstance(body, dict) else None
            if isinstance(provenance, dict):
                print()
                print(f"provenance: {json.dumps(provenance, ensure_ascii=False)}")
        return 0

    print(f"workflow_id: {result.get('workflow_id')}")
    print(f"status: {result.get('status')} audit_ok: {result.get('audit_ok')}")
    print(f"artifact: {result.get('final_artifact_path')}")
    markdown = str(result.get("final_artifact_markdown") or "").strip()
    if markdown:
        print()
        print(markdown)
    return 0


if __name__ == "__main__":
    sys.exit(main())
