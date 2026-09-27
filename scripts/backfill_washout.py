"""把洗盤模型需要的三組新欄位補進既有的 TWMARKET 每日快照。

  python -m scripts.backfill_washout

補三樣東西：

  投信／自營商買賣超   BFI82U 本來就回這幾列，平台先前只取了外資與合計。
                       「三大法人同向家數」需要逐家方向，少一家就判不出來。
  市場廣度             MI_INDEX 的「漲跌證券數合計」表（官方股票口徑）。
  選擇權 P/C 未平倉比  TAIFEX pcRatioDown。

BFI82U 與 MI_INDEX 都要逐日打，pcRatioDown 可以一次拉一個區間 ——
所以先把 P/C 整段抓下來，再逐日補另外兩樣。

寫入走 store.merge_fields，沿用「永不降級」規則：某一天某個來源失敗，
不會把先前已經成功的值蓋成缺值。補完要跑 scripts.rescore 重算。
"""

from __future__ import annotations

import argparse
import time
from datetime import date

from eos import store
from sources import taifex, twse
from sources.base import SourceError, fetch_text

INSTRUMENT = "TWMARKET"
THROTTLE = 1.2          # 秒。TWSE 對連續請求會擋，35 天分兩支 API 打 70 次。


def _pc_range(days: list[date]) -> dict[date, dict[str, float | None]]:
    """P/C 比逐月拉，再拆成逐日。

    端點支援區間查詢，但**跨月就回查詢頁的 HTML 而不是 CSV**，而且不報錯。
    照月切開是唯一可靠的做法 —— 35 個交易日只要兩次請求。
    """
    out: dict[date, dict[str, float | None]] = {}
    months: dict[tuple[int, int], list[date]] = {}
    for d in days:
        months.setdefault((d.year, d.month), []).append(d)

    for (_y, _m), group in sorted(months.items()):
        d0, d1 = min(group), max(group)
        text = fetch_text(taifex.PC_RATIO_URL, encoding="big5",
                          data={"queryStartDate": f"{d0:%Y/%m/%d}",
                                "queryEndDate": f"{d1:%Y/%m/%d}"})
        if text.lstrip().startswith("<"):
            print(f"  P/C {d0}~{d1}：端點回 HTML，該月留缺")
            continue
        for d in group:
            out[d] = taifex.parse_pc_ratio(text, d, url=taifex.PC_RATIO_URL)
        time.sleep(THROTTLE)
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="回填洗盤模型的新欄位")
    ap.add_argument("--instrument", default=INSTRUMENT)
    args = ap.parse_args(argv)

    days = sorted(store.all_days(args.instrument))
    if not days:
        print(f"{args.instrument} 沒有任何快照")
        return 1

    try:
        pc = _pc_range(days)
        print(f"P/C 比：一次取得 {sum(1 for v in pc.values() if v.get('txo_pc_oi_pct'))}/{len(days)} 天")
    except SourceError as exc:
        print(f"P/C 比整段取得失敗，逐日欄位留缺：{exc}")
        pc = {}

    touched = 0
    for day in days:
        collected = {}
        for label, fn in (("法人", lambda: twse.fetch_institutional(day)),
                          ("廣度", lambda: twse.fetch_breadth(day))):
            try:
                collected.update(fn())
            except SourceError as exc:
                print(f"  {day} [{label}] 來源失敗：{exc}")
            time.sleep(THROTTLE)

        parsed = pc.get(day)
        if parsed:
            collected.update(taifex.pc_ratio_fields(parsed, day, url=taifex.PC_RATIO_URL))

        if not collected:
            continue
        snap = store.load(args.instrument, day) or {}
        fields, changed = store.merge_fields(snap.get("fields", {}), collected)
        if not changed:
            continue
        store.save(args.instrument, day, fields=fields, eos=snap.get("eos"), windows=[])
        touched += 1
        print(f"  {day} 更新 {len(changed)} 欄：{', '.join(changed[:5])}")

    print(f"完成：{touched}/{len(days)} 天有更新")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
