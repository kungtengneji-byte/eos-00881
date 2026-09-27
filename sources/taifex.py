"""臺灣期貨交易所 adapter —— 外資台指期未平倉。

大盤資金流向模型裡，外資的期貨部位是判斷「法人真實方向」的關鍵：
現貨買超搭配期貨淨空擴大，跟現貨買超搭配期貨回補，意義完全不同。

兩個實作細節：
  * 下載端點只接受 POST，GET 會回查詢頁的 HTML
  * 回傳的 CSV 是 **Big5** 編碼，不是 UTF-8 —— 用 utf-8 解會整片亂碼
"""

from __future__ import annotations

from datetime import date
from typing import Any

from eos.models import Field, Status
from sources.base import UnexpectedPayload, fetch_text, to_float

DOWNLOAD_URL = "https://www.taifex.com.tw/cht/3/futContractsDateDown"

# 「三大法人－區分各期貨契約」的欄位位置
_C_DATE, _C_PRODUCT, _C_IDENTITY = 0, 1, 2
_C_LONG_OI = 9          # 多方未平倉口數
_C_SHORT_OI = 11        # 空方未平倉口數
_C_NET_OI = 13          # 多空未平倉口數淨額
_C_NET_AMOUNT = 14      # 多空未平倉契約金額淨額（千元）
_EXPECTED_COLS = 15

# 期交所的身份別字樣。用「包含」比對而非等值，避免全形空白之類的差異。
IDENTITIES = {"dealer": "自營商", "trust": "投信", "foreign": "外資"}

# 商品名稱。臺股期貨即大台（TXF）。
PRODUCT_TXF = "臺股期貨"


def download_url() -> str:
    return DOWNLOAD_URL


def _payload(day: date, commodity: str) -> dict[str, str]:
    d = f"{day:%Y/%m/%d}"
    return {
        "firstDate": d, "lastDate": d,
        "queryStartDate": d, "queryEndDate": d,
        "commodityId": commodity,
    }


def parse_futures_oi(csv_text: str, day: date, *,
                     product: str = PRODUCT_TXF) -> dict[str, dict[str, float | None]]:
    """解析 CSV，回傳 {身份別鍵: {long_oi, short_oi, net_oi, net_amount_k}}。

    只取指定商品（預設臺股期貨）。查無資料時回傳空 dict，不丟例外 ——
    休市日與尚未發布都會走到這裡，屬正常缺值。
    """
    out: dict[str, dict[str, float | None]] = {}
    lines = [ln for ln in csv_text.splitlines() if ln.strip()]
    if not lines:
        return out

    target = f"{day:%Y/%m/%d}"
    for ln in lines[1:]:                     # 第一列是標題
        cells = [c.strip() for c in ln.split(",")]
        if len(cells) < _EXPECTED_COLS:
            continue
        if cells[_C_DATE] != target:
            continue
        if product not in cells[_C_PRODUCT]:
            continue
        label = cells[_C_IDENTITY]
        for key, zh in IDENTITIES.items():
            if zh in label:
                out[key] = {
                    "long_oi": to_float(cells[_C_LONG_OI]),
                    "short_oi": to_float(cells[_C_SHORT_OI]),
                    "net_oi": to_float(cells[_C_NET_OI]),
                    "net_amount_k": to_float(cells[_C_NET_AMOUNT]),
                }
                break
    return out


def oi_fields(parsed: dict[str, dict[str, float | None]], day: date,
              *, url: str) -> dict[str, Field]:
    """包成 Field。外資淨未平倉是模型主要讀取的欄位。"""
    src = "期交所 三大法人-區分各期貨契約"
    as_of = day.isoformat()

    def mk(name: str, key: str, sub: str, note: str = "") -> Field:
        rec = parsed.get(key)
        if not rec or rec.get(sub) is None:
            return Field.missing(name, source=src, url=url,
                                 note="當日尚未發布或休市")
        return Field(name=name, value=rec[sub], source=src, url=url,
                     as_of=as_of, status=Status.OK, note=note)

    return {
        "foreign_futures_net_oi": mk(
            "foreign_futures_net_oi", "foreign", "net_oi",
            "負值為淨空單；淨空擴大代表外資對後市偏空避險"),
        "foreign_futures_long_oi": mk("foreign_futures_long_oi", "foreign", "long_oi"),
        "foreign_futures_short_oi": mk("foreign_futures_short_oi", "foreign", "short_oi"),
        "dealer_futures_net_oi": mk("dealer_futures_net_oi", "dealer", "net_oi"),
        "trust_futures_net_oi": mk("trust_futures_net_oi", "trust", "net_oi"),
    }


def fetch_futures_oi(day: date, *, commodity: str = "TXF") -> dict[str, Field]:
    text = fetch_text(DOWNLOAD_URL, data=_payload(day, commodity), encoding="big5")
    if text.lstrip().startswith("<"):
        raise UnexpectedPayload(f"{DOWNLOAD_URL} 回傳 HTML 而非 CSV，端點可能已改版")
    return oi_fields(parse_futures_oi(text, day), day, url=DOWNLOAD_URL)


# ---------------------------------------------------------------- 選擇權 P/C 比

PC_RATIO_URL = "https://www.taifex.com.tw/cht/3/pcRatioDown"

# 欄位：日期, 賣權成交量, 買權成交量, 買賣權成交量比率%, 賣權未平倉, 買權未平倉, 買賣權未平倉比率%
_PC_DATE, _PC_VOL_PCT, _PC_OI_PCT = 0, 3, 6
_PC_EXPECTED_COLS = 7


def pc_ratio_url() -> str:
    return PC_RATIO_URL


def parse_pc_ratio(csv_text: str, day: date, *, url: str = "") -> dict[str, float | None]:
    """臺指選擇權買賣權比率。只取指定日那一列。

    **未平倉比才是部位訊號，成交量比不是。** 成交量比一天之內就會被當沖
    與價差單洗掉；未平倉比反映的是留倉的方向。兩個都存，但模型只用未平倉比。
    """
    want = f"{day:%Y/%m/%d}"
    for line in csv_text.splitlines():
        cells = [c.strip().strip('"') for c in line.split(",")]
        if len(cells) < _PC_EXPECTED_COLS or cells[_PC_DATE] != want:
            continue
        return {"txo_pc_volume_pct": to_float(cells[_PC_VOL_PCT]),
                "txo_pc_oi_pct": to_float(cells[_PC_OI_PCT])}
    return {"txo_pc_volume_pct": None, "txo_pc_oi_pct": None}


def pc_ratio_fields(parsed: dict[str, float | None], day: date, *, url: str) -> dict[str, Field]:
    src, as_of = "TAIFEX 選擇權買賣權比", day.isoformat()

    def mk(name: str, val: float | None) -> Field:
        if val is None:
            return Field.missing(name, source=src, url=url, note="當日尚未發布或查無該日")
        return Field(name=name, value=val, source=src, url=url,
                     as_of=as_of, status=Status.OK)

    return {n: mk(n, v) for n, v in parsed.items()}


def fetch_pc_ratio(day: date) -> dict[str, Field]:
    d = f"{day:%Y/%m/%d}"
    text = fetch_text(PC_RATIO_URL, data={"queryStartDate": d, "queryEndDate": d},
                      encoding="big5")
    if text.lstrip().startswith("<"):
        raise UnexpectedPayload(f"{PC_RATIO_URL} 回傳 HTML 而非 CSV，端點可能已改版")
    return pc_ratio_fields(parse_pc_ratio(text, day, url=PC_RATIO_URL), day, url=PC_RATIO_URL)
