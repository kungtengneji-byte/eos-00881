"""回填逐檔法人買賣超（T86），供〈連續買賣Top5〉使用。

  python -m scripts.backfill_stocks --days 60
  python -m scripts.backfill_stocks --from 2026-07-01 --to 2026-09-24

連續天數只能靠逐日檔往回數 —— TWSE 沒有任何「已連續幾天」的欄位，
所以新上線時必須先把視窗長度的歷史補齊，否則第一天算出來的
最長連續就是 1 天。

交易日以既有的大盤快照為準（data/daily/TWMARKET/），不自行推算行事曆；
需要更長的區間時再向 TWSE 問一次大盤成交資訊。
"""

from __future__ import annotations

import argparse
import time
from datetime import date, timedelta

from eos import stockflow, store
from sources import twse
from sources.base import SourceError, fetch_json

# T86 是整份市場的資料（實測 1,326 列 / 約 190KB），比其他端點重得多。
# 回填會連打數十次，間隔拉長到 5 秒 —— TWSE 被打太快時是「回空資料」
# 而不是回錯誤，靜默的空檔會被當成「當天沒有法人動作」，直接切斷連續。
BACKFILL_INTERVAL = 5.0


def trading_days(start: date, end: date) -> list[date]:
    """先用已有的大盤快照；不足時再向 TWSE 問大盤成交資訊補足日曆。"""
    known = [d for d in store.all_days("TWMARKET") if start <= d <= end]
    if known:
        return sorted(known)

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
    ap = argparse.ArgumentParser(description="回填逐檔法人買賣超")
    ap.add_argument("--days", type=int, help="往前回填幾個交易日（由今天起算）")
    ap.add_argument("--from", dest="start", help="起始日 YYYY-MM-DD")
    ap.add_argument("--to", dest="end", help="結束日 YYYY-MM-DD")
    ap.add_argument("--force", action="store_true", help="已存在的日子也重抓")
    args = ap.parse_args(argv)

    end = date.fromisoformat(args.end) if args.end else date.today()
    if args.start:
        start = date.fromisoformat(args.start)
    else:
        # 交易日約為日曆日的 0.7 倍，多抓一點再由 trading_days 篩掉非交易日
        start = end - timedelta(days=int((args.days or 60) * 1.6))

    days = trading_days(start, end)
    if args.days:
        days = days[-args.days:]
    if not days:
        print("區間內沒有交易日")
        return 1

    todo = days if args.force else stockflow.missing_days(days)
    print(f"區間 {days[0]} ~ {days[-1]}　交易日 {len(days)} 天　需要抓 {len(todo)} 天")
    if not todo:
        print("已經齊全，沒有要抓的")
        return 0

    ok = 0
    for i, day in enumerate(todo, 1):
        try:
            nets, names, _ = twse.fetch_stock_institutional_all(day)
        except SourceError as exc:
            print(f"  [{i}/{len(todo)}] {day} 來源失敗：{exc}")
            time.sleep(BACKFILL_INTERVAL)
            continue
        if not nets:
            # 空資料可能是休市，也可能是被限流。兩者都不該寫成空檔 ——
            # 寫了之後就再也分不出「當天沒人動」與「我們沒抓到」。
            print(f"  [{i}/{len(todo)}] {day} 回傳空資料，略過不寫")
            time.sleep(BACKFILL_INTERVAL)
            continue
        stockflow.save_day(day, nets, names)
        ok += 1
        print(f"  [{i}/{len(todo)}] {day} 共 {len(nets)} 檔")
        if i < len(todo):
            time.sleep(BACKFILL_INTERVAL)

    have = stockflow.available_days()
    print(f"\n寫入 {ok} 天，目前累積 {len(have)} 個交易日"
          + (f"（{have[0]} ~ {have[-1]}）" if have else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
