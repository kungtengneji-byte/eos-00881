"""外資資金流的波段切分、統計與壓力點計算。

燈號模型的七個因子裡，F1/F2/F4 的輸入不是直接抓得到的數字，
而是要從外資逐日買賣超與加權指數推導出來的：

    F1  本波天數 ÷ 回看視窗內最長買波天數
    F2  本波累計 ÷ 回看視窗內最大買波累計
    F4  (近 N 日最高收盤 − 最新收盤) ÷ 最新收盤

工作表是一次性期間彙整，「期間」直接等於那份檔案的起訖日。
每日自動化不能這樣 —— 同一天在不同時候重算必須得到同一個答案，
因此改為**固定回看 N 個交易日的滾動視窗**（預設 60）。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any, Iterable

DEFAULT_LOOKBACK = 60
FUTURES_OI_LOOKBACK = 5      # F3：5 日淨未平倉變化
MARGIN_LOOKBACK = 2          # F5：2 日融資增減


@dataclass(frozen=True)
class Wave:
    """外資現貨買賣超的同向連續區間。"""

    direction: str            # "buy" / "sell"
    start: date
    end: date
    days: int
    cumulative: float         # 億元，賣波為負
    completed: bool           # 已翻向者為 True；最後一段為進行中
    truncated: bool           # 視窗起點就落在波段中間，真實起日不可知

    @property
    def usable(self) -> bool:
        """可納入統計：必須是完整波段，且起點沒有被視窗切掉。"""
        return self.completed and not self.truncated


def _sign(v: float) -> int:
    return 1 if v > 0 else -1 if v < 0 else 0


def segment_waves(rows: list[dict[str, Any]], *, key: str = "foreign_net_100m") -> list[Wave]:
    """把逐日買賣超切成同向連續波段。

    缺值日直接跳過而不是當成 0 —— 0 沒有方向，強行歸入任一邊都會
    把兩個波段錯誤地黏成一段。平盤（剛好 0）同理跳過。

    第一段一律標記 truncated：資料起點之前發生什麼我們不知道，
    它的「天數」與「累計」都可能是被切掉的一部分。
    """
    waves: list[Wave] = []
    cur_sign = 0
    start = end = None
    days = 0
    total = 0.0

    def flush(completed: bool) -> None:
        nonlocal start, end, days, total, cur_sign
        if cur_sign == 0 or start is None or end is None:
            return
        waves.append(Wave(
            direction="buy" if cur_sign > 0 else "sell",
            start=start, end=end, days=days, cumulative=round(total, 6),
            # 第一段的起點落在視窗邊界上，真實起日不可知
            completed=completed, truncated=(len(waves) == 0),
        ))
        cur_sign, start, end, days, total = 0, None, None, 0, 0.0

    for row in rows:
        v = row.get(key)
        if v is None:
            continue
        s = _sign(float(v))
        if s == 0:
            continue
        d = row["date"] if isinstance(row["date"], date) else date.fromisoformat(row["date"])
        if s != cur_sign:
            flush(completed=True)
            cur_sign, start, days, total = s, d, 0, 0.0
        end = d
        days += 1
        total += float(v)
    flush(completed=False)          # 最後一段仍在進行中
    return waves


def window(rows: list[dict[str, Any]], as_of: date, lookback: int) -> list[dict[str, Any]]:
    """截出以 as_of 結尾、最多 lookback 個交易日的視窗。"""
    picked = []
    for r in rows:
        d = r["date"] if isinstance(r["date"], date) else date.fromisoformat(r["date"])
        if d <= as_of:
            picked.append(r)
    return picked[-lookback:]


def wave_stats(waves: list[Wave]) -> dict[str, Any]:
    """回看視窗內已完成波段的統計，以及進行中的本波。"""
    buys = [w for w in waves if w.usable and w.direction == "buy"]
    sells = [w for w in waves if w.usable and w.direction == "sell"]
    current = next((w for w in reversed(waves) if not w.completed), None)

    def avg(xs: list[float]) -> float | None:
        return sum(xs) / len(xs) if xs else None

    return {
        "completed_buy_waves": len(buys),
        "completed_sell_waves": len(sells),
        "avg_buy_wave_days": avg([w.days for w in buys]),
        "max_buy_wave_days": max((w.days for w in buys), default=None),
        "avg_buy_wave_cumulative_100m": avg([w.cumulative for w in buys]),
        "max_buy_wave_cumulative_100m": max((w.cumulative for w in buys), default=None),
        "avg_sell_wave_days": avg([w.days for w in sells]),
        "avg_sell_wave_cumulative_100m": avg([w.cumulative for w in sells]),
        "wave_direction": current.direction if current else None,
        "wave_days": current.days if current else None,
        "wave_cumulative_100m": current.cumulative if current else None,
        "wave_start": current.start.isoformat() if current else None,
    }


def highest_close(rows: list[dict[str, Any]], *, key: str = "close") -> tuple[date | None, float | None]:
    """視窗內最高收盤。

    刻意用收盤而非盤中高：收盤是唯一有官方定版、可重現的價格。
    盤中高會因資料來源與是否含試撮而異，每日自動重算會飄。
    """
    best_d = best_v = None
    for r in rows:
        v = r.get(key)
        if v is None:
            continue
        if best_v is None or float(v) > best_v:
            best_v = float(v)
            best_d = r["date"] if isinstance(r["date"], date) else date.fromisoformat(r["date"])
    return best_d, best_v


def _lookback_delta(rows: list[dict[str, Any]], key: str, n: int) -> float | None:
    """最新值與 n 個交易日前的差。缺任一端即回 None，不以鄰日頂替。"""
    vals = [r.get(key) for r in rows]
    have = [(i, v) for i, v in enumerate(vals) if v is not None]
    if not have:
        return None
    last_i, last_v = have[-1]
    target_i = last_i - n
    if target_i < 0 or vals[target_i] is None:
        return None
    return float(last_v) - float(vals[target_i])


def rubric_inputs(rows: list[dict[str, Any]], as_of: date, *,
                  lookback: int = DEFAULT_LOOKBACK) -> dict[str, Any]:
    """由每日序列算出燈號模型 F1–F5 所需的輸入。

    F6（費半）與 F7（10 年債）來自海外來源，不在這裡。
    """
    win = window(rows, as_of, lookback)
    if not win:
        return {}

    stats = wave_stats(segment_waves(win))
    peak_date, peak = highest_close(win)
    latest_close = next((r["close"] for r in reversed(win) if r.get("close") is not None), None)

    gap = None
    if peak is not None and latest_close:
        gap = (peak - float(latest_close)) / float(latest_close)

    return {
        **stats,
        "resistance_close": peak,
        "resistance_date": peak_date.isoformat() if peak_date else None,
        "latest_close": latest_close,
        "gap_to_resistance_pct": gap,
        "foreign_futures_oi_change_5d": _lookback_delta(
            win, "foreign_futures_net_oi", FUTURES_OI_LOOKBACK),
        "margin_change_2d_100m": _lookback_delta(
            win, "margin_balance_100m", MARGIN_LOOKBACK),
        "window_days": len(win),
    }
