"""Yahoo Finance chart API adapter —— D 構面（海外科技）與 E 構面（風險環境）。

為什麼是 Yahoo 而不是 Stooq：Stooq 在 2026-09 起對 CSV 端點加上 JavaScript
proof-of-work 驗證，台灣銀行牌告 CSV 則加上 Imperva challenge。平台不繞過
這類機制，兩者皆已剔除。若 Yahoo 日後也加上驗證，base.fetch_text 會丟
BotChallengeDetected，該欄位變成 UNAVAILABLE 而不是靜默給錯值。

兩個必須處理的細節：
  1. 最後一根 K 可能還在盤中（實測 ^VIX 會多出當日未收盤的點）。
     模型規格要求「前一完整美股交易時段」，因此未完成的 K 必須排除。
  2. close 可能為 null（實測 TWD=X 的 2026-09-24）。跳過，不做內插。
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any

from eos.models import Field, Status
from sources.base import UnexpectedPayload, fetch_json, to_float

BASE = "https://query1.finance.yahoo.com/v8/finance/chart"

# EOS 欄位名 -> Yahoo 代號
SYMBOLS = {
    "sox_ret": "^SOX",
    "ndx_ret": "^IXIC",
    "tsm_ret": "TSM",
    "nvda_ret": "NVDA",
    "vix": "^VIX",
    "us10y": "^TNX",
    "usdtwd": "TWD=X",
}


def chart_url(symbol: str, rng: str = "3mo") -> str:
    from urllib.parse import quote
    return f"{BASE}/{quote(symbol)}?interval=1d&range={rng}"


def _session_date(ts: int, gmtoffset: int) -> date:
    return (datetime.fromtimestamp(ts, tz=timezone.utc)
            + timedelta(seconds=gmtoffset)).date()


def _in_progress_session(meta: dict[str, Any]) -> date | None:
    """回傳「目前正在進行中、尚未收盤」的時段日期；沒有則回 None。

    注意 currentTradingPeriod.regular 指的是「目前或下一場」時段，不一定是
    最後一根 K 所屬的時段。實測 ^SOX：regular.start 為 2026-09-25 09:30，
    而 regularMarketTime 為 2026-09-24 17:15 —— 盤已收，下一場還沒開，
    此時最後一根 K（9/24）是完整的，不可刪。

    因此判斷條件是 start <= regularMarketTime < end，也就是「此刻確實在盤中」。
    實測 TWD=X：start 9/25 00:00 <= regularMarketTime 9/25 11:00 < end 9/25 23:59，
    盤中成立，9/25 的兩筆（含 null 與即時報價）都要剔除。
    """
    try:
        market_time = int(meta["regularMarketTime"])
        regular = meta["currentTradingPeriod"]["regular"]
        start, end = int(regular["start"]), int(regular["end"])
        gmtoffset = int(meta.get("gmtoffset") or 0)
    except (KeyError, TypeError, ValueError):
        return None
    if start <= market_time < end:
        return _session_date(start, gmtoffset)
    return None


def parse_chart(payload: dict[str, Any], *, url: str = "") -> list[tuple[date, float]]:
    """回傳已完成交易時段的 [(日期, 收盤價)]，時間升冪，已剔除 null 與未收盤的 K。"""
    try:
        result = payload["chart"]["result"][0]
    except (KeyError, IndexError, TypeError) as exc:
        err = (payload or {}).get("chart", {}).get("error")
        raise UnexpectedPayload(f"{url} 回應結構不符（error={err}）") from exc

    meta = result.get("meta") or {}
    gmtoffset = int(meta.get("gmtoffset") or 0)
    stamps = result.get("timestamp") or []
    try:
        closes = result["indicators"]["quote"][0]["close"]
    except (KeyError, IndexError, TypeError) as exc:
        raise UnexpectedPayload(f"{url} 缺少 close 序列") from exc

    in_progress = _in_progress_session(meta)

    by_date: dict[date, float] = {}
    for i, ts in enumerate(stamps):
        if i >= len(closes):
            break
        c = to_float(closes[i])
        if c is None:                      # 實測 ^SOX 9/22、TWD=X 9/25 為 null，跳過不內插
            continue
        d = _session_date(int(ts), gmtoffset)
        if in_progress is not None and d == in_progress:
            continue                       # 盤中未收，不可當正式收盤
        by_date[d] = c                     # 同一時段有多筆時取最後一筆

    return sorted(by_date.items())


def pick_on_or_before(series: list[tuple[date, float]], cutoff: date) -> int | None:
    """回傳最後一個日期 <= cutoff 的索引。用於避免取到目標日之後的資料。"""
    idx = None
    for i, (d, _) in enumerate(series):
        if d <= cutoff:
            idx = i
        else:
            break
    return idx


def us_session_cutoff(taiwan_day: date) -> date:
    """台股 T 日該用的美股時段是 T-1。

    美股 T-1 的收盤發生在台灣時間 T 日清晨，是台股 T 日開盤前最後一個完整的
    海外訊號。若直接取「最新可得時段」，在台股收盤後收資料時會拿到美股 T 日
    的盤後結果 —— 那是台股收盤之後才發生的事，屬前視偏誤（lookahead bias），
    會讓日後 Phase 6 的回測結果虛高。
    """
    return taiwan_day - timedelta(days=1)


def level_at(series: list[tuple[date, float]], cutoff: date) -> tuple[date, float] | None:
    """cutoff 當日或之前最後一個完整時段的水準值（VIX、US10Y、匯率用）。"""
    i = pick_on_or_before(series, cutoff)
    return None if i is None else series[i]


def change_at(series: list[tuple[date, float]], cutoff: date) -> tuple[date, float] | None:
    """cutoff 當日或之前最後一個完整時段的漲跌幅（SOX、Nasdaq、TSM、NVDA 用）。"""
    i = pick_on_or_before(series, cutoff)
    if i is None or i == 0:
        return None
    prev = series[i - 1][1]
    d, cur = series[i]
    if not prev:
        return None
    return d, cur / prev - 1


def to_field(name: str, symbol: str, series: list[tuple[date, float]],
             *, as_change: bool, target_day: date, url: str) -> Field:
    """包成 Field。資料日期比預期的 T-1 更舊時標記為 STALE。

    美股休市而台股開市時會走到 STALE：資料仍可計分（模型本來就用前一晚），
    但 UI 必須看得出來它不是最新的海外訊號。
    """
    src = f"Yahoo Finance {symbol}"
    cutoff = us_session_cutoff(target_day)
    picked = change_at(series, cutoff) if as_change else level_at(series, cutoff)
    if picked is None:
        return Field.missing(name, source=src, url=url,
                             note=f"{cutoff.isoformat()} 之前無足夠的完整交易時段資料")
    d, v = picked
    lag = (cutoff - d).days
    status = Status.OK if lag == 0 else Status.STALE
    note = "" if lag == 0 else f"美股資料為 {d.isoformat()}，較預期的 {cutoff.isoformat()} 舊 {lag} 天（休市或缺值）"
    return Field(name=name, value=v, source=src, url=url, as_of=d.isoformat(),
                 status=status, note=note)


def fetch_field(name: str, target_day: date, *, as_change: bool, rng: str = "3mo") -> Field:
    symbol = SYMBOLS[name]
    url = chart_url(symbol, rng)
    series = parse_chart(fetch_json(url), url=url)
    return to_field(name, symbol, series, as_change=as_change, target_day=target_day, url=url)


CHANGE_FIELDS = ("sox_ret", "ndx_ret", "tsm_ret", "nvda_ret")
LEVEL_FIELDS = ("vix", "us10y", "usdtwd")


def fetch_all(target_day: date) -> dict[str, Field]:
    out: dict[str, Field] = {}
    for name in CHANGE_FIELDS:
        out[name] = fetch_field(name, target_day, as_change=True)
    for name in LEVEL_FIELDS:
        out[name] = fetch_field(name, target_day, as_change=False)
    return out
