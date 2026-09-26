"""把大盤開高低（盤中高低）補進既有的 TWMARKET 每日快照。

  python -m scripts.backfill_taiex_ohlc

壓力2（期間最高盤中價）與 F4 的分母都需要盤中高，但平台前期只收了
FMTQIK 的收盤。MI_5MINS_HIST 一次回傳整月，所以補 35 個交易日
其實只要打兩次 —— 不必逐日重跑收集器。

寫入走 store.merge_fields，沿用「永不降級」規則：已存在的欄位不會被
這支腳本的失敗覆蓋掉。補完之後要跑 scripts.rescore 重算燈號。
"""

from __future__ import annotations

import argparse
from datetime import date

from eos import store
from eos.models import Field
from sources import twse
from sources.base import SourceError, fetch_json

INSTRUMENT = "TWMARKET"
FIELDS = ("taiex_open", "taiex_high", "taiex_low")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="回填大盤開高低")
    ap.add_argument("--instrument", default=INSTRUMENT)
    args = ap.parse_args(argv)

    days = sorted(store.all_days(args.instrument))
    if not days:
        print(f"{args.instrument} 沒有任何快照")
        return 1

    months = sorted({f"{d:%Y%m}" for d in days})
    history: dict[date, dict[str, float | None]] = {}
    urls: dict[str, str] = {}
    for ym in months:
        url = twse.taiex_ohlc_url(ym)
        urls[ym] = url
        try:
            history.update(twse.parse_taiex_ohlc(fetch_json(url), url=url))
        except SourceError as exc:
            print(f"  {ym} 取得失敗：{exc}")
    print(f"取得 {len(months)} 個月、{len(history)} 個交易日的開高低")

    filled = skipped = 0
    for day in days:
        snap = store.load(args.instrument, day)
        if not snap:
            continue
        have = snap.get("fields") or {}
        # 已經有盤中高就不重寫：這支只負責補洞，不負責覆蓋
        if all((have.get(n) or {}).get("value") is not None for n in FIELDS):
            skipped += 1
            continue

        url = urls.get(f"{day:%Y%m}", "")
        incoming = twse.taiex_ohlc_fields(history, day, url=url)
        merged, changed = store.merge_fields(have, incoming)
        if not changed:
            continue
        store.save(args.instrument, day, fields=merged, eos=snap.get("eos"),
                   windows=snap.get("windows", []))
        filled += 1
        hi = (merged.get("taiex_high") or {}).get("value")
        print(f"  {day}  最高 {hi:,.2f}" if hi else f"  {day}  仍缺")

    print(f"\n補上 {filled} 天，{skipped} 天原本就有")
    print("接著跑：python -m scripts.rescore --instrument TWMARKET")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
