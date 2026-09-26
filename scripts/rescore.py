"""用既有快照裡的欄位重算分數與今日結論，完全不連網。

  python -m scripts.rescore
  python -m scripts.rescore --instrument TWMARKET

rubric 門檻調整、結論邏輯變更之後都跑這支 —— 收集到的原始欄位不變，
變的只是解讀，沒有理由重新打一次所有來源。
這也是為什麼每日快照存的是「欄位 + 來源」而不是只存分數。

刻意走 collect.score_and_save 而不是自己算一遍：大盤的波段推導、
明日展望、與前一個「有發布分數」交易日的比較都在那裡，
在這裡複製一份的話，兩邊遲早會算出不一樣的結果。
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json

import collect
from eos import store


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="以既有欄位重算分數")
    ap.add_argument("--instrument", default="00881")
    ap.add_argument("--verbose", action="store_true", help="顯示每一天的計分明細")
    args = ap.parse_args(argv)

    inst = args.instrument
    cfg = collect.load_config(inst)
    rubric = collect.rubric_for(cfg)

    days = sorted(store.recent_days(inst, 9999))
    if not days:
        print(f"{inst} 沒有任何快照")
        return 1

    changed = 0
    # 由舊到新：大盤的波段推導會讀已存檔的歷史，順序反了早期的日子會看到未來資料
    for day in days:
        snap = store.load(inst, day)
        if not snap:
            continue
        before = json.dumps(snap, ensure_ascii=False, sort_keys=True)
        sink = io.StringIO()
        with contextlib.redirect_stdout(sink if not args.verbose else io.StringIO()):
            collect.score_and_save(cfg, day, rubric, {}, None)
        if args.verbose:
            print(f"\n== {day} ==")
        after = store.load(inst, day)
        # updated_at 每次都會變，比對時要排除，否則 35 天全被算成「有變動」
        a = dict(after or {}); b = json.loads(before)
        for d in (a, b):
            d.pop("updated_at", None)
        if json.dumps(a, ensure_ascii=False, sort_keys=True) != \
           json.dumps(b, ensure_ascii=False, sort_keys=True):
            changed += 1

    collect.write_index(cfg)
    print(f"{inst}：重算 {len(days)} 天，更新 {changed} 天")

    last = store.load(inst, days[-1]) or {}
    eos = last.get("eos") or {}
    s = eos.get("summary") or {}
    print(f"\n最新交易日 {days[-1]} 的結論：\n")
    lines = [s.get("headline", "")]
    for key, prefix in (("drivers", "改善："), ("drags", "拖累：")):
        if s.get(key):
            lines.append(prefix + "、".join(s[key]))
    lines += s.get("evidence", [])
    for key, prefix in (("quality", "資料品質："), ("watch", "待觀察：")):
        if s.get(key):
            lines.append(prefix + "；".join(s[key]))
    for line in lines:
        print(f"  {line}")

    o = eos.get("outlook")
    if o:
        print(f"\n  明日展望：{o.get('eos')}/100（{o.get('rating')}）"
              f"　美股時段 {o.get('us_session')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
