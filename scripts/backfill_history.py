"""回填一段期間的每日快照，讓前端有真實歷史可顯示。

  python -m scripts.backfill_history --from 2026-08-14 --to 2026-09-24

範圍上限由國泰投信的 NAV API 決定 —— 它只回傳 30 個交易日，
更早的日期 A 構面必然缺值、覆蓋率過不了閘門。成分股權重更嚴格，
只有當日快照，所以歷史日的 C 構面一律缺值（這是已知且無解的限制）。

效率考量：TWSE 日線以「月」為單位快取，所以回填同一個月的 20 天
只需要各檔股票抓一次月檔，不是 20 次。
"""

from __future__ import annotations

import argparse
import json
import time
from datetime import date
from pathlib import Path

import collect
from eos import store
from eos.rubric import Rubric

ROOT = Path(__file__).resolve().parent.parent


def trading_days(instrument: str, start: date, end: date) -> list[date]:
    """從重建序列取實際交易日，不自己推算行事曆。"""
    p = ROOT / "data" / f"series_{instrument}.json"
    rows = json.loads(p.read_text(encoding="utf-8"))
    out = []
    for r in rows:
        d = date.fromisoformat(r["date"])
        if start <= d <= end:
            out.append(d)
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--instrument", default="00881")
    ap.add_argument("--from", dest="start", required=True)
    ap.add_argument("--to", dest="end", required=True)
    ap.add_argument("--skip-existing", action="store_true",
                    help="已有快照且無缺值者跳過")
    args = ap.parse_args(argv)

    cfg = collect.load_config(args.instrument)
    rubric = Rubric.load()
    days = trading_days(args.instrument, date.fromisoformat(args.start),
                        date.fromisoformat(args.end))
    print(f"回填 {len(days)} 個交易日：{days[0]} .. {days[-1]}\n")

    parts = tuple(dict.fromkeys(
        p for w in ("w1", "w2", "w3") for p in collect.PARTS_BY_WINDOW[w]))

    for i, day in enumerate(days, 1):
        if args.skip_existing:
            snap = store.load(args.instrument, day)
            if snap and not store.missing_fields(snap, rubric.required_inputs()):
                print(f"[{i}/{len(days)}] {day} 已完整，跳過")
                continue
        print(f"[{i}/{len(days)}] {day}")
        collected = collect.run_parts(cfg, day, parts, force=False)
        if collected:
            collect.score_and_save(cfg, day, rubric, collected, "backfill")
        time.sleep(1)
        print()

    out = store.write_series_index(args.instrument)
    print(f"寫出 {out.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
