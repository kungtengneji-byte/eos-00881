"""用 Python engine 對錨點日計分，並與人工判讀比對。

這支同時是 PowerShell calibrate.ps1 的交叉驗證：兩套獨立實作讀同一份
rubric YAML，若結果一致，代表 rubric 的規格是明確的、不是靠某一邊的
實作細節撐著。
"""

from __future__ import annotations

import json
from pathlib import Path

from eos import engine
from eos.rubric import Rubric

ROOT = Path(__file__).resolve().parent.parent


def main() -> int:
    rubric = Rubric.load()
    anchors = json.loads((ROOT / "tests" / "fixtures" / "anchors.json")
                         .read_text(encoding="utf-8"))
    print(f"rubric {rubric.version}   構面 {list(rubric.dimensions)}")
    print(f"必要輸入 {len(rubric.required_inputs())} 項\n")

    head = (f"{'date':<12}{'A':>9}{'B':>9}{'C':>9}{'D':>9}{'E':>9}{'F':>9}"
            f"{'avail':>7}{'EOS':>5}{'human':>7}{'diff':>6}  status")
    print(head)
    print("-" * len(head))

    for a in anchors:
        res = engine.compute(rubric, a["inputs"])
        cells = ""
        for k in "ABCDEF":
            d = res.dimensions[k]
            cell = "--" if d.earned is None else f"{d.earned:.1f}/{d.available:g}"
            cells += f"{cell:>9}"
        eos = res.eos
        diff = "" if eos is None else f"{eos - a['human_eos']:+d}"
        shown = str(eos) if eos is not None else "--"
        print(f"{a['date']:<12}{cells}{res.available:>7g}{shown:>5}"
              f"{a['human_eos']:>7}{diff:>6}  {res.tier.status}")

    print("\n通過覆蓋率閘門者的誤差：")
    errs = []
    for a in anchors:
        res = engine.compute(rubric, a["inputs"])
        if res.eos is not None:
            errs.append((a["date"], res.available, res.eos - a["human_eos"]))
    for d, av, e in sorted(errs, key=lambda t: -t[1]):
        print(f"  {d}  覆蓋率 {av:>3g}  誤差 {e:+d}")
    if errs:
        mae = sum(abs(e) for _, _, e in errs) / len(errs)
        print(f"  平均絕對誤差 {mae:.2f}（n={len(errs)}）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
