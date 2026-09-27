"""洗盤假說評分的輸入推導。

對齊工作表〈動能與洗盤〉分頁的「大盤洗盤構面」。

## 這個模型在回答什麼

「今天的價格與籌碼行為，比較像主力洗盤（甩掉浮額後還要往上），
還是像派發（真的在出貨）？」——**只評估行為相似度，不證明任何人的意圖。**
這是工作表自己的原話，這裡不放寬。

## 為什麼輸入要自己推導

七個構面裡有五個看的是「變化」而不是「水位」：三日法人累計、成交量日變化、
均線位置、期貨部位日變化、近三日收復。這些都不是單日快照抓得到的欄位，
必須從歷史序列算。marketflow 已經在做同樣的事（F1/F2/F4），
但它的 row 只帶波段需要的欄位，所以這裡自己讀一次快照。

## 與工作表的差異（三項，都是刻意的）

1. **當沖比與分點集中度沒有可用來源。** 工作表把它們跟融資放在同一個
   15 分構面裡，取不到時就給部分分數。平台取不到就是取不到 ——
   整個 15 分由融資承擔（不是給 0，也不是假裝有資料）。
2. **廣度用官方股票口徑**，不是新聞家數。9/23 官方 388 漲 / 562 跌、
   新聞 397 / 571，差幾家不影響落在哪一段，但可稽核。
3. **上漲日照常計分。** 工作表從 9/3 起每天都出分，包含上漲日
   （9/7 六七分、9/22 約四十分、9/23 五十七分）。量縮在下跌日是
   「賣壓竭盡」的正面訊號，在上漲日卻是「追價力道不足」的負面訊號 ——
   W2 用條件式門檻處理這個方向反轉，而不是把上漲日排除。
"""

from __future__ import annotations

from datetime import date
from typing import Any, Iterable

# 快照欄位 -> row 欄位
SNAPSHOT_KEYS = {
    "close": "taiex",
    "open": "taiex_open",
    "high": "taiex_high",
    "low": "taiex_low",
    "change_pct": "taiex_change_pct",
    "turnover_100m": "market_turnover_100m",
    "foreign_net_100m": "market_foreign_net_100m",
    "trust_net_100m": "trust_net_100m",
    "dealer_net_100m": "dealer_net_100m",
    "institutional_net_100m": "institutional_net_100m",
    "foreign_futures_net_oi": "foreign_futures_net_oi",
    "advancing": "advancing",
    "declining": "declining",
}

INSTITUTIONS = ("foreign_net_100m", "trust_net_100m", "dealer_net_100m")

# 均線長度與它們在 W3a 的權重。5 日線權重最重：
# 「守不守得住」在洗盤／派發的判斷上是短線問題，月線是慢變數。
MA_WINDOWS = {"ma5": 5, "ma10": 10, "ma20": 20}

RECOVER_LOOKBACK = 3     # W7 看近三個交易日
CUMULATIVE_DAYS = 3      # W1 的法人累計天數


def rows_from_snapshots(snapshots: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """把每日快照轉成序列，依日期排序。只取 status 為 ok/stale 的值。"""
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


def _upto(rows: list[dict[str, Any]], as_of: date) -> list[dict[str, Any]]:
    day = as_of.isoformat()
    return [r for r in rows if r["date"] <= day]


def _series(rows: list[dict[str, Any]], key: str) -> list[float]:
    """由新到舊、跳過缺值的數列。"""
    return [float(r[key]) for r in reversed(rows) if r.get(key) is not None]


def moving_average(rows: list[dict[str, Any]], length: int) -> float | None:
    """收盤均線。資料不足 length 天就回 None —— 用 8 天算「20 日線」
    會得到一個看起來正常、但系統性偏離的數字，比沒有更糟。"""
    closes = _series(rows, "close")
    if len(closes) < length:
        return None
    return sum(closes[:length]) / length


def cumulative(rows: list[dict[str, Any]], key: str, days: int) -> float | None:
    """最近 days 個交易日的累計。任一天缺值就回 None（不以較少天數冒充）。"""
    vals = [r.get(key) for r in rows[-days:]]
    if len(vals) < days or any(v is None for v in vals):
        return None
    return sum(float(v) for v in vals)


def buying_count(rows: list[dict[str, Any]], days: int = 1) -> int | None:
    """三大法人中，最近 days 日累計為買超的家數（0–3）。

    days=1 是當日方向（W6 法人別一致性），days=3 是中期方向（W1b）。
    任一法人缺值就回 None —— 「兩家在買」和「兩家在買、一家不知道」
    是不同的事，後者不該算成前者。
    """
    n = 0
    for key in INSTITUTIONS:
        total = cumulative(rows, key, days)
        if total is None:
            return None
        if total > 0:
            n += 1
    return n


def recovered_days(rows: list[dict[str, Any]], lookback: int = RECOVER_LOOKBACK) -> int | None:
    """當日收盤站上前 lookback 個交易日收盤的天數（0–lookback）。

    工作表 9/23 的 W7 記 8 分，證據寫「連續3日站上47,700並守住48,000」——
    是**回看**近三日有沒有站回來，不是等 T+3 再回填。這樣這 10 分當天就
    評得出來，不會永遠掛在「待確認」。
    """
    closes = _series(rows, "close")
    if len(closes) < lookback + 1:
        return None
    latest, prior = closes[0], closes[1:lookback + 1]
    return sum(1 for c in prior if latest > c)


# 收在當日區間下緣這個比例以內，就算收盤名目上漲，量價也照「回檔日」判讀。
WEAK_CLOSE = 0.25


def _direction(change_pct: float | None, close_position: float | None) -> str | None:
    """回檔日（down）還是推升日（up）—— 決定 W2 用哪一組量能門檻。

    **不能只看收盤漲跌號。** 9/22 加權盤中衝到 48,601 創新高、收 47,800.17
    正好是全日最低，留下約 800 點上影線，收盤卻還是 +0.17%。只看漲跌號
    會把當天的量增 24.5% 判成「推升日放量」給滿分，但工作表當天的結論是
    「高檔換手偏派發」—— 放量而收在最低是派發特徵，不是追價。

    工作表〈模型與定義〉自己的反證欄寫的就是「收最低、放量跌」，
    收盤位置本來就是判斷的一部分，這裡只是把它寫進條件。
    """
    if change_pct is None:
        return None
    if change_pct < 0:
        return "down"
    if close_position is not None and close_position < WEAK_CLOSE:
        return "down"
    return "up"


def rubric_inputs(rows: list[dict[str, Any]], as_of: date) -> dict[str, Any]:
    """由每日序列算出洗盤模型 W1–W7 所需的輸入。"""
    win = _upto(rows, as_of)
    if not win:
        return {}
    cur = win[-1]
    prev = win[-2] if len(win) > 1 else {}
    out: dict[str, Any] = {}

    # W1 法人中期方向
    out["institutional_net_3d_100m"] = cumulative(win, "institutional_net_100m", CUMULATIVE_DAYS)
    out["institutions_buying_3d"] = buying_count(win, CUMULATIVE_DAYS)

    # W3 均線與收盤結構（收盤位置要先算，W2 的方向判定會用到）
    close = cur.get("close")
    hi, lo = cur.get("high"), cur.get("low")
    close_pos = (None if close is None or hi is None or lo is None or float(hi) == float(lo)
                 else (float(close) - float(lo)) / (float(hi) - float(lo)))
    out["close_position"] = close_pos

    # W2 量價。方向決定要用哪一組量能門檻：回檔日量縮是賣壓竭盡（正面），
    # 推升日量縮是追價力道不足（負面）。
    chg = cur.get("change_pct")
    out["taiex_direction"] = _direction(chg, close_pos)
    t0, t1 = cur.get("turnover_100m"), prev.get("turnover_100m")
    out["turnover_change_pct"] = (None if t0 is None or not t1
                                  else (float(t0) - float(t1)) / float(t1) * 100)
    adv, dec = cur.get("advancing"), cur.get("declining")
    out["breadth_positive"] = None if adv is None or dec is None else bool(adv > dec)

    for name, length in MA_WINDOWS.items():
        ma = moving_average(win, length)
        out[f"taiex_gt_{name}"] = (None if ma is None or close is None
                                   else bool(float(close) > ma))
        out[f"taiex_{name}"] = ma

    # W5 期貨部位變化。看變化不看水位：外資長期淨空是結構性的，回補才是轉折訊號。
    o0, o1 = cur.get("foreign_futures_net_oi"), prev.get("foreign_futures_net_oi")
    out["foreign_futures_oi_change_1d"] = (None if o0 is None or o1 is None
                                           else float(o0) - float(o1))

    # W6 當日一致性
    out["institutions_buying"] = buying_count(win, 1)

    # W7 近三日收復
    out["recovered_days_3"] = recovered_days(win)

    return out
