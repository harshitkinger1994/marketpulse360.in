#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.pattern_oi_vwap_ema_scanner import main as base_main


def main(argv: list[str] | None = None) -> int:
    args = [
        "--market",
        "india",
        "--interval",
        "4h",
        "--store-timeframe",
        "4h",
        "--strategy-name",
        "Pattern+OI+VWAP/EMA 4h",
    ]
    if argv:
        args.extend(argv)
    return base_main(args)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
