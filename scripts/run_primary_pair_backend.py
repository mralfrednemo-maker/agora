from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from agora.drivers.claude_code_new import ClaudeCodeNewDriver
from agora.drivers.codex import CodexDriver
from agora.drivers.gemini_cli import GeminiCliDriver
from agora.engine.primary_pair import PrimaryPairConfig, PrimaryPairRunner, RoleSpec


def build_config(args: argparse.Namespace) -> PrimaryPairConfig:
    brief = args.brief
    if args.brief_file:
        brief = Path(args.brief_file).read_text(encoding="utf-8")
    codex = CodexDriver(
        id=f"{args.run_id or 'primary-pair'}-codex",
        display_name="Codex",
        model="gpt-5.4-mini",
        effort="low",
    )
    gemini = GeminiCliDriver(
        id=f"{args.run_id or 'primary-pair'}-gemini",
        display_name="Gemini CLI",
        model="gemini-2.5-flash-lite",
    )
    claude = ClaudeCodeNewDriver(
        id=f"{args.run_id or 'primary-pair'}-claude-minimax",
        display_name="Claude MiniMax",
        model="MiniMax-M2.7-highspeed",
        timeout_s=args.timeout,
    )
    return PrimaryPairConfig(
        brief=brief,
        run_id=args.run_id,
        max_revision_turns=args.max_revision_turns,
        primary_a=RoleSpec(
            role_key="primary_a",
            label="Primary A / Codex",
            logical_role="primary_a",
            driver=codex,
            model="gpt-5.4-mini",
            effort="low",
        ),
        primary_b=RoleSpec(
            role_key="primary_b",
            label="Primary B / Gemini CLI",
            logical_role="primary_b",
            driver=gemini,
            model="gemini-2.5-flash-lite",
        ),
        secondary=RoleSpec(
            role_key="secondary",
            label="Secondary Seed / Claude MiniMax",
            logical_role="secondary_seed",
            driver=claude,
            model="MiniMax-M2.7-highspeed",
        ),
    )


async def main_async(args: argparse.Namespace) -> int:
    config = build_config(args)
    runner = PrimaryPairRunner(config)
    result = await runner.run()
    print(json.dumps(
        {
            "run_id": result.run_id,
            "status": result.status,
            "stop_reason": result.stop_reason,
            "run_dir": result.run_dir,
            "ledger_path": result.ledger_path,
            "turns": result.turns,
            "validation": result.validation,
            "final_artifact": result.final_artifact.path if result.final_artifact else None,
        },
        ensure_ascii=False,
        indent=2,
    ))
    return 0 if result.validation.get("ok") else 2


def main() -> int:
    parser = argparse.ArgumentParser(description="Run Agora Primary Pair backend protocol with real CLI drivers.")
    parser.add_argument("--brief", default="", help="Brief text.")
    parser.add_argument("--brief-file", help="Path to a brief file.")
    parser.add_argument("--run-id", help="Stable run id / artifact directory name.")
    parser.add_argument("--max-revision-turns", type=int, default=4)
    parser.add_argument("--timeout", type=int, default=420, help="Claude MiniMax timeout in seconds.")
    args = parser.parse_args()
    if not args.brief and not args.brief_file:
        parser.error("provide --brief or --brief-file")
    return asyncio.run(main_async(args))


if __name__ == "__main__":
    raise SystemExit(main())
