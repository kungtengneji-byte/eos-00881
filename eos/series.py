"""含息調整序列與技術指標。

公式刻意與既有工作表 Raw_History 一致（也與 Phase 1 的 build_series.ps1 一致），
因此新舊結果可直接比對：

    ret    = (Close_t + Div_t) / Close_t-1 - 1
    TRI    = TRI_t-1 * (1 + ret_t),  TRI_0 = 100
    adj    = Close_0 * TRI_t / TRI_0
    retN   = PRODUCT(1 + ret 最近 N 日) - 1
    ddN    = adj_t / MAX(adj 最近 N 日) - 1
    RV20   = STDEV.S(ret 最近 20 日) * SQRT(252)
    RSI14  = Wilder，對 adj 的逐日差分

重要：技術指標一律走含息調整價。00881 在 2026-08-18 配息 4.60 元，
若用未調整價格計算 MA/RSI/回檔，除息造成的機械式下跌會被誤判成真跌幅。
"""

from __future__ import annotations

import math
from datetime import date
from typing import Any, Iterable

from eos.models import Bar

TRADING_DAYS_PER_YEAR = 252
RSI_PERIOD = 14

# 「站上均線」這類布林判斷的相對容差。
# 2026-03-04 實測：close_adj 與 ma20 只差 7.1e-15（純浮點累加順序造成），
# 用裸 > 比較會讓 B1 的 3 分條件翻面、EOS 差約 3 分。
# 價格與均線相差不到十億分之一時在經濟意義上完全相同，判成哪一邊都是任意的，
# 因此統一視為「未站上」—— 趨勢條件要成立就必須真的成立。
GT_REL_TOL = 1e-9


def strictly_greater(a: float | None, b: float | None) -> bool | None:
    """a 是否顯著大於 b。實質相等回傳 False，任一為 None 回傳 None。"""
    if a is None or b is None:
        return None
    return (a - b) > abs(b) * GT_REL_TOL


def _ma(values: list[float], i: int, window: int) -> float | None:
    if i < window - 1:
        return None
    return sum(values[i - window + 1: i + 1]) / window


def _ret_n(rets: list[float], i: int, window: int) -> float | None:
    """N 日總報酬 —— 連乘而非相加，與工作表 PRODUCT(1+ret)-1 一致。"""
    if i < window:
        return None
    p = 1.0
    for k in range(i - window + 1, i + 1):
        p *= 1 + rets[k]
    return p - 1


def _drawdown_n(adj: list[float], i: int, window: int) -> float | None:
    if i < window - 1:
        return None
    peak = max(adj[i - window + 1: i + 1])
    return adj[i] / peak - 1 if peak else None


def _rv(rets: list[float], i: int, window: int) -> float | None:
    """年化實現波動率。用樣本標準差（STDEV.S，分母 n-1）以對齊工作表。"""
    if i < window:
        return None
    seg = rets[i - window + 1: i + 1]
    mean = sum(seg) / window
    var = sum((r - mean) ** 2 for r in seg) / (window - 1)
    return math.sqrt(var) * math.sqrt(TRADING_DAYS_PER_YEAR)


def _avg_vol(lots: list[float | None], i: int, window: int) -> float | None:
    """20 日均量，含當日 —— 對齊工作表 AVERAGE(C5:C24)，其中第 24 列為當日。"""
    if i < window:
        return None
    seg = lots[i - window + 1: i + 1]
    if any(v is None for v in seg):
        return None
    return sum(v for v in seg if v is not None) / window


def _wilder_rsi(adj: list[float]) -> list[float | None]:
    n = len(adj)
    out: list[float | None] = [None] * n
    avg_gain = avg_loss = 0.0
    for i in range(1, n):
        change = adj[i] - adj[i - 1]
        gain, loss = max(change, 0.0), max(-change, 0.0)
        if i <= RSI_PERIOD:
            avg_gain += gain / RSI_PERIOD
            avg_loss += loss / RSI_PERIOD
            if i < RSI_PERIOD:
                continue
        else:
            avg_gain = (avg_gain * (RSI_PERIOD - 1) + gain) / RSI_PERIOD
            avg_loss = (avg_loss * (RSI_PERIOD - 1) + loss) / RSI_PERIOD
        out[i] = 100.0 if avg_loss == 0 else 100 - 100 / (1 + avg_gain / avg_loss)
    return out


def build(bars: Iterable[Bar], dividends: dict[date, float] | None = None) -> list[dict[str, Any]]:
    """由日線與配息建出完整序列。bars 會先依日期排序，重複日期以後者為準。"""
    divs = dividends or {}
    uniq: dict[date, Bar] = {}
    for b in bars:
        if b.close is None:
            continue                      # 沒有收盤價的列無法納入報酬序列
        uniq[b.date] = b
    ordered = [uniq[d] for d in sorted(uniq)]
    n = len(ordered)
    if n == 0:
        return []

    closes = [b.close for b in ordered]
    lots = [b.volume_lots for b in ordered]

    rets: list[float] = [0.0] * n
    tri: list[float] = [0.0] * n
    adj: list[float] = [0.0] * n
    tri[0] = 100.0
    adj[0] = closes[0]
    base = closes[0]

    for i in range(1, n):
        d = float(divs.get(ordered[i].date, 0.0))
        rets[i] = (closes[i] + d) / closes[i - 1] - 1
        tri[i] = tri[i - 1] * (1 + rets[i])
        adj[i] = base * tri[i] / 100.0

    rsi = _wilder_rsi(adj)

    rows: list[dict[str, Any]] = []
    for i, bar in enumerate(ordered):
        ma20 = _ma(adj, i, 20)
        ma60 = _ma(adj, i, 60)
        ma120 = _ma(adj, i, 120)
        avg20 = _avg_vol(lots, i, 20)
        ret60 = _ret_n(rets, i, 60)
        lot = lots[i]

        rows.append({
            "date": bar.date.isoformat(),
            "open": bar.open, "high": bar.high, "low": bar.low, "close": bar.close,
            "volume_lots": None if lot is None else round(lot),
            "turnover_100m": None if bar.turnover is None else round(bar.turnover / 1e8, 4),
            "dividend": divs.get(bar.date, 0),
            "ret": rets[i] if i > 0 else None,
            "tri": tri[i],
            "close_adj": adj[i],
            "ma20": ma20, "ma60": ma60, "ma120": ma120,
            "ret5": _ret_n(rets, i, 5),
            "ret20": _ret_n(rets, i, 20),
            "ret60": ret60,
            "drawdown20": _drawdown_n(adj, i, 20),
            "drawdown60": _drawdown_n(adj, i, 60),
            "rsi14": rsi[i],
            "rv20": _rv(rets, i, 20),
            "avg_vol20": avg20,
            "volume_ratio": (lot / avg20) if (avg20 and lot is not None) else None,
            # B1 檢核清單的四個條件
            "close_adj_gt_ma20": strictly_greater(adj[i], ma20),
            "ma20_gt_ma60": strictly_greater(ma20, ma60),
            "close_adj_gt_ma120": strictly_greater(adj[i], ma120),
            "ret60_positive": None if ret60 is None else ret60 > 0,
        })
    return rows


def day_direction(rows: list[dict[str, Any]], i: int) -> str | None:
    """F1 量比需要的漲跌方向。與前一交易日收盤比較，平盤視為 up。"""
    if i <= 0 or i >= len(rows):
        return None
    cur, prev = rows[i]["close"], rows[i - 1]["close"]
    if cur is None or prev is None:
        return None
    return "up" if cur >= prev else "down"


def to_rubric_inputs(rows: list[dict[str, Any]], i: int) -> dict[str, Any]:
    """把序列第 i 列轉成 rubric 的 B / F1 輸入。"""
    r = rows[i]
    return {
        "close_adj_gt_ma20": r["close_adj_gt_ma20"],
        "ma20_gt_ma60": r["ma20_gt_ma60"],
        "close_adj_gt_ma120": r["close_adj_gt_ma120"],
        "ret60_positive": r["ret60_positive"],
        "drawdown20": r["drawdown20"],
        "rsi14": r["rsi14"],
        "rv20": r["rv20"],
        "volume_ratio": r["volume_ratio"],
        "day_direction": day_direction(rows, i),
    }
