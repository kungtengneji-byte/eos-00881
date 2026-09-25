"""替既有快照補上 top_holdings 明細，全部從 raw/ 快取重算，不連網。

回填當時只存了 WCR 與上漲家數兩個彙總值，看到 WCR -0.24% 也不知道是誰拖累的。
權重快照（raw/cathay/）與個股日線（raw/twse/）都已在本機，
因此不需要重跑一次十分鐘的回填。

  python -m scripts.patch_holdings
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import collect
from eos import engine, store
from eos.models import Field, Holding, Status
from eos.rubric import Rubric

ROOT = Path(__file__).resolve().parent.parent


def cached_weights(instrument: str, day: date) -> tuple[date | None, list[Holding]]:
    p = ROOT / "raw" / "cathay" / instrument / f"weights_{day.isoformat()}.json"
    if not p.exists():
        return None, []
    raw = json.loads(p.read_text(encoding="utf-8"))
    as_of = date.fromisoformat(raw["as_of"]) if raw.get("as_of") else None
    holdings = [Holding(code=h["code"], name=h["name"], weight=float(h["weight"]))
                for h in raw.get("holdings", [])]
    return as_of, holdings


def main(argv: list[str] | None = None) -> int:
    instrument = "00881"
    cfg = collect.load_config(instrument)
    rubric = Rubric.load()

    days = sorted(store.recent_days(instrument, 9999))
    patched = skipped = missing = 0

    for day in days:
        snap = store.load(instrument, day)
        if not snap:
            continue
        if "top_holdings" in (snap.get("fields") or {}):
            skipped += 1
            continue

        as_of, holdings = cached_weights(instrument, day)
        if not holdings:
            print(f"  {day}  無快取權重，略過")
            missing += 1
            continue

        top = holdings[: int(cfg.get("top_n", 10))]
        rows, problems = collect.holdings_breakdown(top, day, force=False)
        if len(rows) < len(top):
            print(f"  {day}  僅 {len(rows)}/{len(top)} 檔可算：{'；'.join(problems[:2])}")

        # 沿用該日 wcr 欄位既有的狀態與註記，保持一致
        src_field = (snap["fields"] or {}).get("wcr", {})
        field = Field(
            name="top_holdings", value=rows,
            source=src_field.get("source", "國泰投信權重 + TWSE 個股收盤"),
            url=src_field.get("url", ""), as_of=src_field.get("as_of", day.isoformat()),
            status=Status(src_field.get("status", "stale")),
            note=src_field.get("note", ""),
        )
        fields, _ = store.merge_fields(snap["fields"], {"top_holdings": field})
        inputs = {n: f.get("value") for n, f in fields.items()
                  if f.get("status") in ("ok", "stale")}
        result = engine.compute(rubric, inputs)
        store.save(instrument, day, fields=fields, eos=result.to_dict(),
                   windows=snap.get("windows", []))
        patched += 1

    store.write_series_index(instrument)
    store.write_instrument_meta(cfg)
    print(f"\n補上 {patched} 天、已有 {skipped} 天、無快取權重 {missing} 天")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
