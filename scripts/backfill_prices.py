"""回填個股每日收盤與漲跌幅，供產業資金輪動的「價格同向」確認使用。

  python -m scripts.backfill_prices
  python -m scripts.backfill_prices --days 60

以已存在的逐檔法人明細（data/stocks/）為交易日清單 —— 有法人資料卻沒有
價格的那幾天才需要抓，不自行推算行事曆。

MI_INDEX 一次回傳十幾張表（約 1,300 檔個股），比其他端點重，
間隔與逐檔法人的回填一致拉到 5 秒。
"""

from __future__ import annotations

import argparse
import time
from datetime import date

from eos import stockflow
from sources import twse
from sources.base import SourceError

INTERVAL = 5.0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="回填個股每日收盤與漲跌")
    ap.add_argument("--days", type=int, help="最多回填幾個交易日（由新到舊）")
    ap.add_argument("--force", action="store_true", help="已存在的日子也重抓")
    args = ap.parse_args(argv)

    days = stockflow.available_days()
    if not days:
        print("還沒有逐檔法人明細，先跑 scripts.backfill_stocks")
        return 1
    if args.days:
        days = days[-args.days:]

    have = set(stockflow.price_days())
    todo = days if args.force else [d for d in days if d not in have]
    print(f"交易日 {len(days)} 天（{days[0]} ~ {days[-1]}），需要抓 {len(todo)} 天")
    if not todo:
        print("已經齊全")
        return 0

    ok = 0
    for i, day in enumerate(todo, 1):
        try:
            prices, _ = twse.fetch_stock_prices(day)
        except SourceError as exc:
            print(f"  [{i}/{len(todo)}] {day} 來源失敗：{exc}")
            time.sleep(INTERVAL)
            continue
        if not prices:
            # 空資料可能是休市也可能被限流，兩者都不該寫成空檔
            print(f"  [{i}/{len(todo)}] {day} 回傳空資料，略過不寫")
            time.sleep(INTERVAL)
            continue
        stockflow.save_prices(day, prices)
        ok += 1
        withpct = sum(1 for v in prices.values() if v[1] is not None)
        print(f"  [{i}/{len(todo)}] {day} 共 {len(prices)} 檔，其中 {withpct} 檔有漲跌")
        if i < len(todo):
            time.sleep(INTERVAL)

    got = stockflow.price_days()
    print(f"\n寫入 {ok} 天，目前累積 {len(got)} 天"
          + (f"（{got[0]} ~ {got[-1]}）" if got else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
