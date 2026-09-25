"""國泰投信 adapter —— A 構面（NAV/折溢價）與 C 構面（成分股權重）的權威來源。

API 主機是 cwapi.cathaysite.com.tw（不是 www），走 GET + query string。
基金代碼不是網址上的 'ECR'，而是 'CR'（'E' 是 ETF 前綴）。這點卡了一陣子，
所以 INSTRUMENT_FUND_CODE 明確列出對照，新增標的時在此登記。

兩個重要性質：
  * GetEtf30DaysNavAndPrice 回傳 30 個交易日，所以 NAV 可回補近一個月，
    不是只有當日。NAV 當晚未發布時，隔天回補窗會自動補上。
  * diffRate 是發行商自己算的折溢價，直接採用，不用市價/NAV 自行相除，
    避免與官方揭露值產生微小但無謂的差異。
"""

from __future__ import annotations

from datetime import date
from typing import Any

from eos.models import Field, Holding, Status
from sources.base import (
    UnexpectedPayload,
    fetch_json,
    slash_to_date,
    to_float,
)

BASE = "https://cwapi.cathaysite.com.tw/api/ETF"

# 標的代號 -> 國泰投信內部基金代碼
INSTRUMENT_FUND_CODE = {
    "00881": "CR",
}


def fund_code_for(instrument: str) -> str:
    try:
        return INSTRUMENT_FUND_CODE[instrument]
    except KeyError:
        raise UnexpectedPayload(
            f"{instrument} 尚未登記國泰投信基金代碼，請在 INSTRUMENT_FUND_CODE 補上"
        ) from None


def _unwrap(payload: Any, *, url: str) -> Any:
    """國泰 API 統一格式 {result, returnCode, success, returnMessage}。

    success=False 多半是「查無資料」（例如代碼錯、當日尚未發布），
    屬於正常缺值而非錯誤，因此回傳 None 而不是丟例外。
    """
    if not isinstance(payload, dict):
        raise UnexpectedPayload(f"{url} 回應不是物件")
    if not payload.get("success"):
        return None
    return payload.get("result")


# ---------------------------------------------------------------- NAV / 折溢價

def nav_history_url(fund_code: str) -> str:
    return f"{BASE}/GetEtf30DaysNavAndPrice?FundCode={fund_code}"


def parse_nav_history(payload: dict[str, Any], *, url: str = "") -> dict[date, dict[str, float | None]]:
    """回傳 {交易日: {close, nav, premium}}，premium 為小數（-0.38% -> -0.0038）。"""
    result = _unwrap(payload, url=url)
    if not result:
        return {}
    out: dict[date, dict[str, float | None]] = {}
    for row in result:
        if not isinstance(row, dict) or "date" not in row:
            continue
        rate = to_float(row.get("diffRate"))       # to_float 已剝除 '%'
        out[slash_to_date(row["date"])] = {
            "close": to_float(row.get("closingPrice")),
            "nav": to_float(row.get("nav")),
            "premium": None if rate is None else rate / 100.0,
        }
    return out


def nav_fields(history: dict[date, dict[str, float | None]], day: date,
               *, url: str) -> dict[str, Field]:
    """把某一天的 NAV/折溢價包成 Field。查無該日即為 MISSING（不以鄰日代算）。"""
    src = "國泰投信 cwapi"
    rec = history.get(day)
    if not rec:
        note = "當日正式 NAV 尚未發布；不以前一日 NAV 代算折溢價"
        return {
            "nav": Field.missing("nav", source=src, url=url, note=note),
            "premium": Field.missing("premium", source=src, url=url, note=note),
        }
    as_of = day.isoformat()

    def mk(name: str, val: float | None) -> Field:
        if val is None:
            return Field.missing(name, source=src, url=url, note="該日此欄位為空")
        return Field(name=name, value=val, source=src, url=url,
                     as_of=as_of, status=Status.OK)

    return {"nav": mk("nav", rec["nav"]), "premium": mk("premium", rec["premium"])}


def fetch_nav_history(instrument: str) -> tuple[dict[date, dict[str, float | None]], str]:
    url = nav_history_url(fund_code_for(instrument))
    return parse_nav_history(fetch_json(url), url=url), url


# ---------------------------------------------------------------- 成分股權重

def weights_url(fund_code: str) -> str:
    return f"{BASE}/GetIndexStockWeights?FundCode={fund_code}"


def parse_weights(payload: dict[str, Any], *, url: str = "") -> tuple[date | None, list[Holding]]:
    """回傳 (權重基準日, 依權重降序的成分股)。權重轉為小數：37.97 -> 0.3797。"""
    result = _unwrap(payload, url=url)
    if not result:
        return None, []
    raw_date = result.get("date")
    as_of = slash_to_date(raw_date) if raw_date else None

    holdings: list[Holding] = []
    for row in result.get("stockWeights") or []:
        w = to_float(row.get("weights"))
        code = str(row.get("stockCode", "")).strip()
        if w is None or not code:
            continue
        holdings.append(Holding(code=code, name=str(row.get("stockName", "")).strip(),
                                weight=w / 100.0))
    holdings.sort(key=lambda h: h.weight, reverse=True)
    return as_of, holdings


def top_n(holdings: list[Holding], n: int = 10) -> list[Holding]:
    return holdings[:n]


def fetch_weights(instrument: str) -> tuple[date | None, list[Holding], str]:
    url = weights_url(fund_code_for(instrument))
    as_of, holdings = parse_weights(fetch_json(url), url=url)
    return as_of, holdings, url
