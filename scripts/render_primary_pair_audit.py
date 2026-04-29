from __future__ import annotations

import argparse
import html
import json
from pathlib import Path
from typing import Any

from agora.engine.primary_pair import analyze_primary_pair_ledger


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not path.exists():
        return rows
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        rows.append(json.loads(line))
    return rows


def phase_label(artifact_id: str) -> str:
    if artifact_id == "A0":
        return "LLM1 Seed"
    if artifact_id == "B0":
        return "LLM2 Seed"
    if artifact_id == "S0":
        return "LLM3 Seed"
    if artifact_id == "A1":
        return "LLM1 First Document"
    if artifact_id == "B1":
        return "LLM2 First Document"
    if artifact_id.startswith("A"):
        return f"LLM1 Revision {int(artifact_id[1:]) - 1}"
    if artifact_id.startswith("B"):
        return f"LLM2 Revision {int(artifact_id[1:]) - 1}"
    return artifact_id


def logical_round(artifact_id: str) -> int:
    if artifact_id in {"A0", "B0", "S0"}:
        return 1
    if artifact_id in {"A1", "B1"}:
        return 2
    if artifact_id[:1] in {"A", "B"} and artifact_id[1:].isdigit():
        return int(artifact_id[1:]) + 1
    return 0


def card(title: str, body: str, extra_class: str = "") -> str:
    klass = f"card {extra_class}".strip()
    return f"<section class='{klass}'><h3>{html.escape(title)}</h3>{body}</section>"


def code_block(text: str) -> str:
    return f"<pre>{html.escape(text)}</pre>"


def list_items(items: list[str]) -> str:
    if not items:
        return "<p class='muted'>None.</p>"
    return "<ul>" + "".join(f"<li>{html.escape(item)}</li>" for item in items) + "</ul>"


def file_link(path: str) -> str:
    href = Path(path).as_uri()
    return f"<a href='{html.escape(href)}'>{html.escape(path)}</a>"


def render_html(room: dict[str, Any], run: dict[str, Any], prompts: list[dict[str, Any]], ledger: list[dict[str, Any]], transcript: list[dict[str, Any]]) -> str:
    prompt_by_artifact = {row["artifact_id"]: row for row in prompts if row.get("artifact_id")}
    complete_rows = {row["artifact_id"]: row for row in ledger if row.get("event") == "turn_complete" and row.get("artifact_id")}
    start_rows = {row["artifact_id"]: row for row in ledger if row.get("event") == "turn_start" and row.get("artifact_id")}
    artifact_order: list[str] = []
    seen_artifacts: set[str] = set()
    for row in prompts:
        artifact_id = row.get("artifact_id")
        if not artifact_id or artifact_id in seen_artifacts:
            continue
        seen_artifacts.add(artifact_id)
        artifact_order.append(str(artifact_id))

    flow_nodes: list[str] = []
    for artifact_id in artifact_order:
        start = start_rows.get(artifact_id, {})
        role = (start.get("role") or {}).get("label") or artifact_id
        inputs = start.get("input_artifacts") or []
        inputs_text = ", ".join(inputs) if inputs else "brief only"
        flow_nodes.append(
            f"<div class='flow-node'><div class='flow-head'>{html.escape(artifact_id)} · R{logical_round(artifact_id)}</div>"
            f"<div class='flow-title'>{html.escape(phase_label(artifact_id))}</div>"
            f"<div class='flow-meta'>{html.escape(str(role))}</div>"
            f"<div class='flow-meta'>Inputs: {html.escape(inputs_text)}</div></div>"
        )

    timeline_cards: list[str] = []
    for artifact_id in artifact_order:
        prompt_row = prompt_by_artifact.get(artifact_id, {})
        start = start_rows.get(artifact_id, {})
        done = complete_rows.get(artifact_id, {})
        prompt_path = prompt_row.get("prompt_path")
        reply_path = done.get("reply_path")
        prompt_text = Path(prompt_path).read_text(encoding="utf-8", errors="replace") if prompt_path and Path(prompt_path).exists() else ""
        reply_text = Path(reply_path).read_text(encoding="utf-8", errors="replace") if reply_path and Path(reply_path).exists() else ""
        role = (start.get("role") or {}).get("label") or artifact_id
        inputs = start.get("input_artifacts") or []
        input_refs = "".join(f"<span class='pill'>{html.escape(ref)}</span>" for ref in inputs) if inputs else "<span class='pill'>brief only</span>"
        meta = (
            f"<div class='meta-row'><span class='pill'>{html.escape(artifact_id)}</span>"
            f"<span class='pill'>R{logical_round(artifact_id)}</span>"
            f"<span class='pill'>{html.escape(phase_label(artifact_id))}</span>"
            f"<span class='pill'>{html.escape(str(role))}</span>"
            f"<span class='pill'>latency {html.escape(str(done.get('latency_ms', '')))} ms</span>"
            f"<span class='pill'>marker {html.escape(str(done.get('reply_marker', '')))}</span></div>"
        )
        io = (
            "<div class='io-grid'>"
            f"<div><h4>Inputs</h4><div class='pill-row'>{input_refs}</div><h4>Prompt</h4>{code_block(prompt_text)}</div>"
            f"<div><h4>Output</h4>{code_block(reply_text)}</div>"
            "</div>"
        )
        files = "<p class='muted'>Files: "
        links = []
        if prompt_path:
            links.append(file_link(prompt_path))
        if reply_path:
            links.append(file_link(reply_path))
        files += " · ".join(links) + "</p>"
        timeline_cards.append(card(f"{artifact_id} · {phase_label(artifact_id)}", meta + files + io))

    transcript_rows = []
    for entry in transcript:
        transcript_rows.append(
            "<tr>"
            f"<td>{entry.get('seq')}</td>"
            f"<td>{html.escape(str(entry.get('phase')))}</td>"
            f"<td>{entry.get('round')}</td>"
            f"<td>{html.escape(str(entry.get('participant_id')))}</td>"
            f"<td>{html.escape(str(entry.get('ts')))}</td>"
            f"<td>{html.escape((entry.get('content') or '')[:180])}</td>"
            "</tr>"
        )

    roles = []
    for role_key, info in (run.get("roles") or {}).items():
        roles.append(
            f"<tr><td>{html.escape(role_key)}</td><td>{html.escape(str(info.get('label')))}</td>"
            f"<td>{html.escape(str(info.get('driver_id')))}</td><td>{html.escape(str(info.get('driver_kind')))}</td>"
            f"<td>{html.escape(str(info.get('model')))}</td><td>{html.escape(str(info.get('effort')))}</td></tr>"
        )

    summary = (
        f"<div class='summary-grid'>"
        f"<div><strong>Room ID</strong><br>{html.escape(str(room.get('id')))}</div>"
        f"<div><strong>Run ID</strong><br>{html.escape(str(run.get('run_id')))}</div>"
        f"<div><strong>Status</strong><br>{html.escape(str(run.get('status')))}</div>"
        f"<div><strong>Stop Reason</strong><br>{html.escape(str(run.get('stop_reason')))}</div>"
        f"<div><strong>Turns</strong><br>{html.escape(str((run.get('validation') or {}).get('turns')))}</div>"
        f"<div><strong>Final Artifact</strong><br>{html.escape(str((run.get('final_artifact') or {}).get('artifact_id')))}</div>"
        f"</div>"
        f"<p><strong>Topic</strong><br>{html.escape(str(room.get('topic')))}</p>"
    )

    protocol = (
        "<ol>"
        "<li><strong>R1</strong>: A0, B0, S0 are seed views started in parallel from the original brief.</li>"
        "<li><strong>R2</strong>: A1 and B1 are first full documents started in parallel from A0/B0/S0.</li>"
        "<li><strong>R3+</strong>: alternating revisions, one LLM revising against the other LLM's latest full document.</li>"
        "<li><strong>Stop</strong>: this run stopped at the configured revision cap.</li>"
        "</ol>"
    )

    issues = list_items((run.get("validation") or {}).get("issues") or [])
    final_doc_path = (run.get("final_artifact") or {}).get("path")
    final_doc = Path(final_doc_path).read_text(encoding="utf-8", errors="replace") if final_doc_path and Path(final_doc_path).exists() else ""

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Agora Primary Pair Audit</title>
  <style>
    :root {{
      --bg:#f5f7fb; --panel:#ffffff; --line:#d6deea; --text:#132133; --muted:#5d6b80; --accent:#0f766e;
    }}
    * {{ box-sizing:border-box; }}
    body {{ margin:0; font:14px/1.45 Inter, Segoe UI, Arial, sans-serif; color:var(--text); background:var(--bg); }}
    .wrap {{ max-width: 1480px; margin: 0 auto; padding: 24px; }}
    h1,h2,h3,h4 {{ margin:0 0 10px; }}
    h1 {{ font-size: 28px; }}
    h2 {{ font-size: 20px; margin-top: 28px; }}
    h3 {{ font-size: 16px; }}
    .card {{ background:var(--panel); border:1px solid var(--line); border-radius:10px; padding:16px; margin-bottom:16px; }}
    .summary-grid {{ display:grid; grid-template-columns: repeat(6, minmax(0,1fr)); gap:12px; margin-bottom:16px; }}
    .summary-grid > div {{ background:#f8fbff; border:1px solid var(--line); border-radius:8px; padding:10px; }}
    .muted {{ color:var(--muted); }}
    .pill-row {{ display:flex; flex-wrap:wrap; gap:8px; margin: 6px 0 12px; }}
    .pill {{ display:inline-block; padding:4px 8px; border-radius:999px; background:#eef6f6; border:1px solid #c7e5e2; color:#0b5d57; font-size:12px; font-weight:600; }}
    .flow {{ display:grid; grid-template-columns: repeat(4, minmax(0,1fr)); gap:12px; }}
    .flow-node {{ background:#fbfcfe; border:1px solid var(--line); border-radius:10px; padding:12px; }}
    .flow-head {{ font:600 12px/1.4 ui-monospace, SFMono-Regular, Consolas, monospace; color:var(--accent); }}
    .flow-title {{ font-weight:700; margin-top:6px; }}
    .flow-meta {{ margin-top:4px; color:var(--muted); font-size:12px; }}
    .meta-row {{ display:flex; flex-wrap:wrap; gap:8px; margin-bottom:10px; }}
    .io-grid {{ display:grid; grid-template-columns: 1fr 1fr; gap:16px; }}
    pre {{ margin:0; white-space:pre-wrap; word-break:break-word; font:12px/1.5 ui-monospace, SFMono-Regular, Consolas, monospace; background:#0f1720; color:#e9f0fb; border-radius:8px; padding:14px; max-height:520px; overflow:auto; }}
    table {{ width:100%; border-collapse:collapse; }}
    th, td {{ border:1px solid var(--line); padding:8px 10px; text-align:left; vertical-align:top; }}
    th {{ background:#f0f5fb; }}
    a {{ color:#0b63ce; text-decoration:none; }}
    .warning {{ border-color:#f1cc84; background:#fff9ec; }}
    @media (max-width: 1100px) {{ .summary-grid, .flow, .io-grid {{ grid-template-columns:1fr; }} }}
  </style>
</head>
<body>
  <div class="wrap">
    <h1>Agora Primary Pair Audit</h1>
    <p class="muted">Standalone troubleshooting view for room <code>{html.escape(str(room.get('id')))}</code>.</p>
    {card("Run Summary", summary)}
    {card("Protocol Overview", protocol)}
    {card("Role Assignments", "<table><thead><tr><th>Role</th><th>Label</th><th>Driver</th><th>Kind</th><th>Model</th><th>Effort</th></tr></thead><tbody>" + "".join(roles) + "</tbody></table>")}
    {card("Flow Map", "<div class='flow'>" + "".join(flow_nodes) + "</div>")}
    {card("Validation Snapshot", f"<p><strong>Validation OK:</strong> {html.escape(str((run.get('validation') or {}).get('ok')))}</p><p><strong>Issues</strong></p>{issues}", "warning" if not (run.get('validation') or {}).get('ok') else "")}
    {card("Final Artifact", (f"<p class='muted'>File: {file_link(final_doc_path)}</p>" if final_doc_path else "") + code_block(final_doc))}
    <h2>Turn-by-Turn Inputs and Outputs</h2>
    {"".join(timeline_cards)}
    {card("Transcript Table", "<table><thead><tr><th>#</th><th>Artifact</th><th>Round</th><th>Participant</th><th>Timestamp</th><th>Preview</th></tr></thead><tbody>" + "".join(transcript_rows) + "</tbody></table>")}
    {card("Source Files", "<ul>" +
      f"<li>Run manifest: {file_link(str(Path(run.get('run_id') and Path(run.get('ledger_path')).parent / 'run.json' or '')))}</li>" +
      f"<li>Turn ledger: {file_link(str(run.get('ledger_path')))}</li>" +
      f"<li>Prompt ledger: {file_link(str(run.get('prompt_ledger_path')))}</li>" +
      f"<li>Room manifest: {file_link(str(Path('C:/Users/chris/PROJECTS/agora/data/rooms') / str(room.get('id')) / 'room.json'))}</li>" +
      f"<li>Transcript: {file_link(str(Path('C:/Users/chris/PROJECTS/agora/data/rooms') / str(room.get('id')) / 'transcript.jsonl'))}</li>" +
      "</ul>")}
  </div>
</body>
</html>"""


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--room-id", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    root = Path("C:/Users/chris/PROJECTS/agora")
    room_dir = root / "data" / "rooms" / args.room_id
    room = read_json(room_dir / "room.json")
    run_dir = root / "data" / "primary-pair-runs" / f"room-{args.room_id}"
    run = read_json(run_dir / "run.json")
    recomputed_validation = analyze_primary_pair_ledger(
        run_dir / "turn-ledger.jsonl",
        expected_roles=dict(run.get("roles") or {}),
    )
    run["validation"] = recomputed_validation
    if recomputed_validation.get("ok"):
        run["status"] = "converged" if run.get("converged") else (run.get("status") if run.get("status") != "failed_validation" else "stopped_at_cap")
    elif run.get("status") == "failed_validation":
        run["status"] = "failed_validation"
    prompts = read_jsonl(run_dir / "prompt-ledger.jsonl")
    ledger = read_jsonl(run_dir / "turn-ledger.jsonl")
    transcript = read_jsonl(room_dir / "transcript.jsonl")

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(render_html(room, run, prompts, ledger, transcript), encoding="utf-8")
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
