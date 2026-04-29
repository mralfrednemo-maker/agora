from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from agora.engine.primary_pair import analyze_primary_pair_ledger


def main() -> int:
    parser = argparse.ArgumentParser(description="Analyze an Agora Primary Pair turn ledger.")
    parser.add_argument("ledger", help="Path to turn-ledger.jsonl")
    args = parser.parse_args()
    result = analyze_primary_pair_ledger(Path(args.ledger))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
