"""用既有快照裡的欄位重算 EOS 與今日結論，完全不連網。

  python -m scripts.rescore

rubric 門檻調整、結論邏輯變更之後都跑這支 —— 收集到的原始欄位不變，
變的只是解讀，沒有理由重新打一次所有來源。
這也是為什麼每日快照存的是「欄位 + 來源」而不是只存分數。
"""

from __future__ import annotations

import json
from pathlib import Path

import collect
from eos import engine, store, summary
from eos.rubric import Rubric

ROOT = Path(__file__).resolve().parent.parent


def main(argv: list[str] | None = None) -> int:
    instrument = "00881"
    cfg = collect.load_config(instrument)
    rubric = Rubric.load()
    meta_path = store.write_instrument_meta(cfg)
    meta = json.loads(meta_path.read_text(encoding="utf-8"))

    days = sorted(store.recent_days(instrument, 9999))
    prev_result = None
    prev_date = None
    changed = 0

    for day in days:
        snap = store.load(instrument, day)
        if not snap:
            continue
        fields = snap.get("fields") or {}
        inputs = {n: f.get("value") for n, f in fields.items()
                  if f.get("status") in ("ok", "stale")}
        result = engine.compute(rubric, inputs)
        summ = summary.build(rubric, result, prev_result, fields,
                             meta=meta, prev_date=prev_date)

        payload = result.to_dict()
        payload["summary"] = summ
        before = json.dumps(snap.get("eos"), ensure_ascii=False, sort_keys=True)
        after = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        if before != after:
            store.save(instrument, day, fields=fields, eos=payload,
                       windows=snap.get("windows", []))
            changed += 1

        if result.published:
            prev_result, prev_date = result, day.isoformat()

    store.write_series_index(instrument)
    print(f"重算 {len(days)} 天，更新 {changed} 天")

    last = store.load(instrument, days[-1])
    print(f"\n最新交易日 {days[-1]} 的結論：\n")
    s = (last.get("eos") or {}).get("summary") or {}
    for line in [s.get("headline", "")] + \
                (["改善：" + "、".join(s["drivers"])] if s.get("drivers") else []) + \
                (["拖累：" + "、".join(s["drags"])] if s.get("drags") else []) + \
                s.get("evidence", []) + \
                (["資料品質：" + "；".join(s["quality"])] if s.get("quality") else []) + \
                (["待觀察：" + "；".join(s["watch"])] if s.get("watch") else []):
        print(f"  {line}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
