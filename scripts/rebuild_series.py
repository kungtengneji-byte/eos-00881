"""由 raw/ 的原始 TWSE 回應重建含息調整序列。

  python -m scripts.rebuild_series            # 重建並寫入 data/
  python -m scripts.rebuild_series --check    # 只比對，不寫檔

--check 是跨實作驗證：Phase 1 的 build_series.ps1（PowerShell）與
eos/series.py（Python）讀同一批原始回應，結果必須逐欄一致。
兩套獨立實作對上了，才有理由相信公式是照規格寫的，而不是照某一邊的實作寫的。
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from datetime import date
from pathlib import Path

from eos import series as series_mod
from sources import twse
from sources.base import fetch_json

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "raw" / "twse"
DATA = ROOT / "data"

# 比對容差：浮點累積誤差，不是實質差異
ABS_TOL = 1e-9
REL_TOL = 1e-9


def load_bars(instrument: str) -> list:
    files = sorted((RAW / instrument).glob("STOCK_DAY_*.json"))
    if not files:
        raise SystemExit(f"raw/twse/{instrument}/ 沒有任何 STOCK_DAY 檔")
    bars = []
    for f in files:
        bars.extend(twse.parse_stock_day(json.loads(f.read_text(encoding="utf-8")),
                                         url=str(f)))
    return bars


def load_dividends(instrument: str, *, refresh: bool) -> dict[date, float]:
    path = RAW / instrument / "EXRIGHT.json"
    if refresh or not path.exists():
        url = twse.dividends_url("20201201", "20261231")
        payload = fetch_json(url)
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        print(f"  refreshed {path.relative_to(ROOT)}")
    else:
        payload = json.loads(path.read_text(encoding="utf-8"))
    return twse.parse_dividends(payload, instrument, url=str(path))


def close_enough(a, b) -> bool:
    if a is None and b is None:
        return True
    if isinstance(a, bool) or isinstance(b, bool):
        return a == b
    if a is None or b is None:
        return False
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return math.isclose(float(a), float(b), rel_tol=REL_TOL, abs_tol=ABS_TOL)
    return a == b


def compare(new: list[dict], old_path: Path) -> int:
    old = json.loads(old_path.read_text(encoding="utf-8"))
    print(f"  PowerShell 版 {len(old)} 列 / Python 版 {len(new)} 列")
    if len(old) != len(new):
        print("  !! 列數不同")
        return 1

    diffs: dict[str, int] = {}
    examples: list[str] = []
    for o, n in zip(old, new):
        if o["date"] != n["date"]:
            print(f"  !! 日期錯位 {o['date']} vs {n['date']}")
            return 1
        for key in n:
            if key not in o:
                continue
            if not close_enough(o[key], n[key]):
                diffs[key] = diffs.get(key, 0) + 1
                if len(examples) < 8:
                    examples.append(f"    {n['date']} {key}: ps={o[key]!r} py={n[key]!r}")
    if not diffs:
        print("  兩套實作逐欄一致")
        return 0
    print("  欄位差異：")
    for k, c in sorted(diffs.items(), key=lambda kv: -kv[1]):
        print(f"    {k}: {c} 列")
    print("\n".join(examples))
    return 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--instrument", default="00881")
    ap.add_argument("--check", action="store_true", help="只比對既有輸出，不寫檔")
    ap.add_argument("--refresh-dividends", action="store_true",
                    help="重新抓配息（原始回應存回 raw/）")
    args = ap.parse_args(argv)

    bars = load_bars(args.instrument)
    divs = load_dividends(args.instrument, refresh=args.refresh_dividends)
    print(f"bars {len(bars)}  {bars[0].date} .. {bars[-1].date}   dividends {len(divs)}")

    rows = series_mod.build(bars, divs)
    out = DATA / f"series_{args.instrument}.json"

    if args.check:
        return compare(rows, out)

    DATA.mkdir(exist_ok=True)
    out.write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"wrote {out.relative_to(ROOT)} ({len(rows)} rows)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
