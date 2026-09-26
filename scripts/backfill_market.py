"""回填大盤每日快照。

燈號的 F1/F2/F4 需要歷史序列才算得出來，所以新標的上線必須先回填 ——
只有一天資料切不出任何波段。

  python -m scripts.backfill_market --from 2026-08-07 --to 2026-09-24

刻意**依日期由舊到新**逐日處理：score_and_save 每次都會重讀已存檔的
歷史來推導波段，順序反了會讓早期的日子看到未來資料。
"""

from __future__ import annotations

import argparse
import json
import time
from datetime import date
from pathlib import Path

import collect
from sources import twse
from sources.base import fetch_json

ROOT = Path(__file__).resolve().parent.parent


def trading_days(start: date, end: date) -> list[date]:
    """交易日以 TWSE 的大盤成交資訊為準，不自行推算行事曆。"""
    days: set[date] = set()
    cur = date(start.year, start.month, 1)
    while cur <= end:
        url = twse.market_index_url(f"{cur:%Y%m}")
        for d in twse.parse_market_index(fetch_json(url), url=url):
            if start <= d <= end:
                days.add(d)
        cur = date(cur.year + (cur.month // 12), (cur.month % 12) + 1, 1)
        time.sleep(3)
    return sorted(days)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="start", required=True)
    ap.add_argument("--to", dest="end", required=True)
    ap.add_argument("--instrument", default="TWMARKET")
    args = ap.parse_args(argv)

    cfg = collect.load_config(args.instrument)
    rubric = collect.rubric_for(cfg)
    wins = collect.windows_for(cfg)
    parts = tuple(dict.fromkeys(p for w in ("w1", "w2", "w3") for p in wins[w]))

    days = trading_days(date.fromisoformat(args.start), date.fromisoformat(args.end))
    print(f"回填 {len(days)} 個交易日：{days[0]} .. {days[-1]}\n")

    for i, day in enumerate(days, 1):
        print(f"[{i}/{len(days)}] {day}")
        collected = collect.run_parts(cfg, day, parts, force=False)
        if collected:
            collect.score_and_save(cfg, day, rubric, collected, "backfill")
        time.sleep(1)
        print()

    collect.write_index(cfg)
    hist = json.loads((ROOT / "data" / f"eos_history_{args.instrument}.json")
                      .read_text(encoding="utf-8"))
    print(f"完成：{len(hist)} 個交易日進入歷史索引")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
