"""產生 tests/fixtures/anchors.json —— 8 個錨點日的完整計分輸入。

資料來源三處：
  1. data/series_00881.json      技術面（Phase 1 重建的含息序列）
  2. tests/fixtures/cathay_nav30_CR.json  折溢價（國泰投信官方）
  3. 下方 WORKBOOK                只存在於既有工作表、目前尚無 API 的欄位
                                  （Top10 貢獻、美股、VIX/US10Y/匯率、法人）

WORKBOOK 的值連同人工給的六構面分數一起保留，作為 rubric 的回歸基準。
這些日子是唯一有「人工判讀」可對照的樣本，改動 rubric 時必須重跑。
"""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SERIES = ROOT / "data" / "series_00881.json"
NAV = ROOT / "tests" / "fixtures" / "cathay_nav30_CR.json"
OUT = ROOT / "tests" / "fixtures" / "anchors.json"

# 工作表轉錄值 + 人工六構面分數。None 代表該日工作表本來就沒有這欄。
WORKBOOK: dict[str, dict] = {
    "2026-09-01": dict(wcr=None, breadth=None,
                       sox=None, tsm=None, ndx=None, nvda=None,
                       vix=16.34, us10y=4.75, twd=31.633, twd_prev=None,
                       inst=561.39,
                       human=dict(A=11, B=17, C=18, D=10, E=10, F=4), human_eos=70),
    "2026-09-02": dict(wcr=None, breadth=2,
                       sox=0.0045, tsm=0.0036, ndx=0.0045, nvda=0.032,
                       vix=15.20, us10y=4.794, twd=31.728, twd_prev=31.633,
                       inst=None,
                       human=dict(A=11, B=14, C=5, D=4, E=6, F=7), human_eos=47),
    "2026-09-03": dict(wcr=-0.0031, breadth=3,
                       sox=0.0045, tsm=0.0036, ndx=0.0045, nvda=0.032,
                       vix=15.20, us10y=4.794, twd=31.755, twd_prev=31.728,
                       inst=-637.12,
                       human=dict(A=11, B=16, C=6, D=13, E=9, F=3), human_eos=58),
    "2026-09-09": dict(wcr=0.000970554, breadth=6,
                       sox=0.013, tsm=0.02355, ndx=-0.0032, nvda=-0.01975,
                       vix=15.33, us10y=4.789, twd=31.505, twd_prev=None,
                       inst=211.25,
                       human=dict(A=9, B=20, C=14, D=8, E=8, F=7), human_eos=66),
    "2026-09-21": dict(wcr=None, breadth=None,
                       sox=0.043, tsm=0.0102, ndx=0.0226, nvda=0.0134,
                       vix=14.87, us10y=4.95, twd=31.758, twd_prev=None,
                       inst=470.38,
                       human=dict(A=10, B=21, C=17, D=13, E=8, F=8), human_eos=77),
    "2026-09-22": dict(wcr=None, breadth=None,
                       sox=0.011, tsm=0.0154, ndx=0.004, nvda=0.0066,
                       vix=14.21, us10y=4.935, twd=31.708, twd_prev=31.758,
                       inst=608.2,
                       human=dict(A=12, B=22, C=16, D=14, E=10, F=9), human_eos=83),
    "2026-09-23": dict(wcr=None, breadth=None,
                       sox=None, tsm=0.0153, ndx=0.0045, nvda=-0.0147,
                       vix=15.18, us10y=5.106, twd=31.716, twd_prev=31.708,
                       inst=389.21,
                       human=dict(A=13, B=22, C=16, D=13, E=11, F=8), human_eos=83),
    "2026-09-24": dict(wcr=-0.00240006, breadth=6,
                       sox=-0.0123, tsm=0.0153, ndx=-0.0113, nvda=-0.0147,
                       vix=15.18, us10y=5.106, twd=31.78, twd_prev=31.716,
                       inst=-438.73,
                       human=dict(A=10, B=21, C=14, D=6, E=5, F=4), human_eos=60),
}


def main() -> int:
    series = json.loads(SERIES.read_text(encoding="utf-8"))
    by_date = {r["date"]: r for r in series}
    order = [r["date"] for r in series]

    nav_raw = json.loads(NAV.read_text(encoding="utf-8"))
    premium = {}
    for row in nav_raw.get("result") or []:
        d = row["date"].replace("/", "-")
        rate = row.get("diffRate")
        if rate:
            premium[d] = round(float(str(rate).replace("%", "")) / 100, 6)

    anchors = []
    for day in sorted(WORKBOOK):
        row = by_date.get(day)
        if row is None:
            raise SystemExit(f"series 缺少 {day}")
        wb = WORKBOOK[day]

        i = order.index(day)
        prev_close = by_date[order[i - 1]]["close"] if i > 0 else None
        direction = None
        if prev_close is not None and row["close"] is not None:
            direction = "up" if row["close"] >= prev_close else "down"

        twd_change = None
        if wb["twd"] is not None and wb["twd_prev"]:
            twd_change = wb["twd"] / wb["twd_prev"] - 1

        anchors.append({
            "date": day,
            "human": wb["human"],
            "human_eos": wb["human_eos"],
            "inputs": {
                # A
                "premium": premium.get(day),
                # B —— 全部來自 Phase 1 重建序列
                "close_adj_gt_ma20": row["close_adj_gt_ma20"],
                "ma20_gt_ma60": row["ma20_gt_ma60"],
                "close_adj_gt_ma120": row["close_adj_gt_ma120"],
                "ret60_positive": row["ret60_positive"],
                "drawdown20": row["drawdown20"],
                "rsi14": row["rsi14"],
                "rv20": row["rv20"],
                # C
                "wcr": wb["wcr"],
                "breadth_count": wb["breadth"],
                # D
                "sox_ret": wb["sox"], "tsm_ret": wb["tsm"],
                "ndx_ret": wb["ndx"], "nvda_ret": wb["nvda"],
                # E
                "vix": wb["vix"], "us10y": wb["us10y"], "twd_change": twd_change,
                # F
                "volume_ratio": row["volume_ratio"], "day_direction": direction,
                "institutional_net": wb["inst"],
            },
        })

    OUT.write_text(json.dumps(anchors, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote {OUT.relative_to(ROOT)}  ({len(anchors)} anchor days)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
