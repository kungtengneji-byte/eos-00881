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


def _as_date(v: Any) -> date:
    return v if isinstance(v, date) else date.fromisoformat(v)


def _ranked(rows: list[dict[str, Any]], key: str,
            *, reverse: bool = True) -> list[tuple[date, float]]:
    """視窗內某個欄位的逐日值，由大到小（或由小到大）排序。"""
    vals = [(_as_date(r["date"]), float(r[key])) for r in rows if r.get(key) is not None]
    return sorted(vals, key=lambda x: x[1], reverse=reverse)


def _extreme(rows: list[dict[str, Any]], key: str,
             *, reverse: bool = True) -> tuple[date | None, float | None]:
    ranked = _ranked(rows, key, reverse=reverse)
    return ranked[0] if ranked else (None, None)


def highest_close(rows: list[dict[str, Any]], *, key: str = "close") -> tuple[date | None, float | None]:
    """視窗內最高收盤。壓力區下緣用得到（與前波外資成本取高者）。"""
    return _extreme(rows, key)


def lowest_close(rows: list[dict[str, Any]]) -> tuple[date | None, float | None]:
    """視窗內最低收盤 —— 工作表的支撐3。"""
    return _extreme(rows, "close", reverse=False)


def highest_high(rows: list[dict[str, Any]]) -> tuple[date | None, float | None]:
    """視窗內最高**盤中**價 —— 工作表的壓力2，也是 F4 的分母。

    原本這裡用最高收盤，理由是「收盤才有官方定版」。那個理由本身沒錯，
    但與工作表不符：工作表 2026-09-18 的 F4 指標值是 0.84%（距 47,578.24，
    09-08 的盤中高），用最高收盤只會得到 0.31%，F4 從 4.2 分掉到 1.5 分。
    而且盤中高永遠 >= 最高收盤，用收盤會**系統性低估**壓力空間。
    TWSE MI_5MINS_HIST 提供定版的開高低收，重現性的疑慮不成立。
    """
    return _extreme(rows, "high")


def second_highest_high(rows: list[dict[str, Any]]) -> tuple[date | None, float | None]:
    """次高盤中價 —— 工作表的壓力1，與壓力2 構成「前高壓力帶」。

    取不同日的第二高，不是同一天的第二筆：同一天只有一個盤中高。
    """
    ranked = highest_highs_ranked(rows)
    return ranked[1] if len(ranked) > 1 else (None, None)


def highest_highs_ranked(rows: list[dict[str, Any]]) -> list[tuple[date, float]]:
    return _ranked(rows, "high")


def gap_up_edge(rows: list[dict[str, Any]]
                ) -> tuple[date | None, float | None, float | None]:
    """最近一次向上跳空的缺口區間，回傳 (跳空日, 上緣, 下緣)。

    判定：某日開盤高於前一日盤中高。缺口區間是「前一日最高」到「跳空日最低」，
    上緣＝跳空日最低（回檔先測這裡），下緣＝前一日最高（跌破代表缺口補滿）。

    **這不是工作表的支撐1。** 工作表支撐1 取 09-17 的盤中高 46,874.84 並註明
    「跳空缺口上緣概念」，但 09-18 開盤 46,449.56 低於 09-17 最高，那天根本
    沒有跳空 —— 那是人工挑的點位，沒有可複製的規則，因此平台不宣稱能重現它，
    改為提供這個定義明確的缺口區間。
    """
    best: tuple[date | None, float | None, float | None] = (None, None, None)
    prev = None
    for r in rows:
        if prev is not None:
            o, lo, ph = r.get("open"), r.get("low"), prev.get("high")
            if o is not None and ph is not None and float(o) > float(ph):
                upper = float(lo) if lo is not None else float(o)
                best = (_as_date(r["date"]), upper, float(ph))
        prev = r
    return best


def wave_cost(rows: list[dict[str, Any]], wave: "Wave | None") -> float | None:
    """某一段買波的「外資成本」：以每日買超金額為權重的指數加權平均。

    實測 2026-09-04~09-09 這一段算出 47,049.35，與工作表的
    〈外資波段與壓力點〉「前波外資成本」完全相同（到小數第二位）。

    只對買波有意義 —— 賣波的權重是負的，加權平均會失去「成本」的語意。
    """
    if wave is None or wave.direction != "buy":
        return None
    num = den = 0.0
    for r in rows:
        d = _as_date(r["date"])
        if not (wave.start <= d <= wave.end):
            continue
        w, c = r.get("foreign_net_100m"), r.get("close")
        if w is None or c is None:
            return None               # 缺一天就不算，不用剩下的硬湊一個成本
        num += float(w) * float(c)
        den += float(w)
    return num / den if den else None


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
    hi_date, hi = highest_high(win)
    latest_close = next((r["close"] for r in reversed(win) if r.get("close") is not None), None)

    # F4 的分母是期間最高**盤中**價（工作表的壓力2），不是最高收盤。
    # 盤中高缺值時 F4 就不計分 —— 退而用收盤會算出一個看起來合理、
    # 但系統性偏低的分數，那比少一個因子更糟。
    gap = None
    if hi is not None and latest_close:
        gap = (hi - float(latest_close)) / float(latest_close)

    return {
        **stats,
        "resistance_close": peak,
        "resistance_date": peak_date.isoformat() if peak_date else None,
        "resistance_high": hi,
        "resistance_high_date": hi_date.isoformat() if hi_date else None,
        "latest_close": latest_close,
        "gap_to_resistance_pct": gap,
        "foreign_futures_oi_change_5d": _lookback_delta(
            win, "foreign_futures_net_oi", FUTURES_OI_LOOKBACK),
        "margin_change_2d_100m": _lookback_delta(
            win, "margin_balance_100m", MARGIN_LOOKBACK),
        "window_days": len(win),
    }


# ---------------------------------------------------------------- 快照 -> 序列

# 快照欄位名 -> 波段計算需要的鍵。兩邊刻意用不同名字：
# market_foreign_net_100m 與 00881 的 foreign_net_100m 是同一筆 TWSE 資料，
# 但分屬兩個模型，混用會讓其中一邊的改動意外影響另一邊。
SNAPSHOT_KEYS = {
    "close": "taiex",
    "open": "taiex_open",
    "high": "taiex_high",
    "low": "taiex_low",
    "foreign_net_100m": "market_foreign_net_100m",
    "foreign_futures_net_oi": "foreign_futures_net_oi",
    "margin_balance_100m": "margin_balance_100m",
}


def rows_from_snapshots(snapshots: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """把每日快照轉成波段計算用的序列，依日期排序。

    只取 status 為 ok/stale 的值 —— missing 與 unavailable 一律視為缺值，
    由 segment_waves 與 _lookback_delta 各自處理，不在這裡填補。
    """
    rows: list[dict[str, Any]] = []
    for snap in snapshots:
        fields = snap.get("fields") or {}
        row: dict[str, Any] = {"date": snap["trade_date"]}
        for out_key, field_name in SNAPSHOT_KEYS.items():
            f = fields.get(field_name)
            row[out_key] = (f.get("value")
                            if f and f.get("status") in ("ok", "stale") else None)
        rows.append(row)
    rows.sort(key=lambda r: r["date"])
    return rows
