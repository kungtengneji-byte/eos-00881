"""檢視某一天的快照：每個欄位的值、資料日期、狀態與來源。

  python -m scripts.show_snapshot 2026-09-24
  python -m scripts.show_snapshot 2026-09-24 --only sox_ret,vix

這是「每個數字都說得出哪裡來、什麼時候的」那條原則的檢查工具。
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def fmt(v) -> str:
    if v is None:
        return "--"
    if isinstance(v, bool):
        return "是" if v else "否"
    if isinstance(v, float):
        return f"{v:,.6g}"
    return str(v)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("date")
    ap.add_argument("--instrument", default="00881")
    ap.add_argument("--only", help="逗號分隔的欄位名，只看這些")
    args = ap.parse_args(argv)

    p = ROOT / "data" / "daily" / args.instrument / f"{args.date}.json"
    if not p.exists():
        raise SystemExit(f"找不到快照：{p}")
    snap = json.loads(p.read_text(encoding="utf-8"))
    fields = snap["fields"]

    names = ([n.strip() for n in args.only.split(",")] if args.only
             else sorted(fields))

    eos = snap.get("eos") or {}
    print(f"{args.instrument}  {snap['trade_date']}   "
          f"EOS {eos.get('eos')}（{eos.get('rating')}）  "
          f"覆蓋率 {eos.get('available')}  {eos.get('coverage_status')}")
    print(f"收集窗 {', '.join(snap.get('windows', []))}   更新於 {snap.get('updated_at')}\n")

    head = f"{'欄位':<22}{'值':>16}  {'資料日期':<12} {'狀態':<12} 來源"
    print(head)
    print("-" * 96)
    for n in names:
        f = fields.get(n)
        if f is None:
            print(f"{n:<22}{'(無此欄位)':>16}")
            continue
        print(f"{n:<22}{fmt(f['value']):>16}  {str(f['as_of'] or '--'):<12} "
              f"{f['status']:<12} {f['source']}")
        if f.get("note"):
            print(f"{'':<22}  註：{f['note']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
