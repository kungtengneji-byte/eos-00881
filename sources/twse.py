"""臺灣證券交易所 adapter。

主來源地位：00881 的成交量/成交值以 TWSE 為唯一準據。
實測 2026-09-24 官方為 10,442 張 / 5.471 億，Investing 一致、Yahoo 為 10,204 張，
時間序列若混用來源會不可比，因此不接受第三方行情作為主來源。
"""

from __future__ import annotations

from datetime import date
from typing import Any

from eos.models import Bar, Field, Status
from sources.base import (
    UnexpectedPayload,
    fetch_json,
    roc_to_date,
    rows_of,
    to_float,
)

BASE = "https://www.twse.com.tw/rwd/zh"

# STOCK_DAY 欄位位置（fields: 日期 成交股數 成交金額 開盤價 最高價 最低價 收盤價 漲跌價差 成交筆數 註記）
_SD_DATE, _SD_SHARES, _SD_TURNOVER = 0, 1, 2
_SD_OPEN, _SD_HIGH, _SD_LOW, _SD_CLOSE = 3, 4, 5, 6
_SD_TRADES = 8

# TWT49U 欄位位置（資料日期 股票代號 股票名稱 除權息前收盤價 除權息參考價 權值+息值 權/息 ...）
_EX_DATE, _EX_CODE, _EX_PRE, _EX_REF, _EX_CASH, _EX_KIND = 0, 1, 3, 4, 5, 6

# T86 欄位位置
_T86_CODE = 0
_T86_FOREIGN_NET = 4        # 外陸資買賣超股數(不含外資自營商)
_T86_TOTAL_NET = 18         # 三大法人買賣超股數


# ---------------------------------------------------------------- 日線

def stock_day_url(stock_no: str, yyyymm: str) -> str:
    return f"{BASE}/afterTrading/STOCK_DAY?date={yyyymm}01&stockNo={stock_no}&response=json"


def parse_stock_day(payload: dict[str, Any], *, url: str = "") -> list[Bar]:
    bars: list[Bar] = []
    for row in rows_of(payload, url=url):
        if len(row) <= _SD_CLOSE:
            raise UnexpectedPayload(f"STOCK_DAY 欄位數不足：{row!r}")
        bars.append(
            Bar(
                date=roc_to_date(row[_SD_DATE]),
                open=to_float(row[_SD_OPEN]),
                high=to_float(row[_SD_HIGH]),
                low=to_float(row[_SD_LOW]),
                close=to_float(row[_SD_CLOSE]),
                volume_shares=to_float(row[_SD_SHARES]),
                turnover=to_float(row[_SD_TURNOVER]),
                trades=to_float(row[_SD_TRADES]) if len(row) > _SD_TRADES else None,
            )
        )
    bars.sort(key=lambda b: b.date)
    return bars


def fetch_stock_day(stock_no: str, yyyymm: str) -> list[Bar]:
    url = stock_day_url(stock_no, yyyymm)
    return parse_stock_day(fetch_json(url), url=url)


# ---------------------------------------------------------------- 配息

def dividends_url(start: str, end: str) -> str:
    """start/end 為西元 yyyymmdd。實測支援跨年度區間，一次可取完整歷史。"""
    return f"{BASE}/exRight/TWT49U?startDate={start}&endDate={end}&response=json"


def parse_dividends(payload: dict[str, Any], stock_no: str, *, url: str = "") -> dict[date, float]:
    """回傳 {除息交易日: 每單位現金股利}。

    注意 TWT49U 的 stockNo 參數實測無效（仍回傳全市場），必須在此處過濾。
    只取「息」，不取「權」—— 股票股利會改變單位數，不能當現金股利處理。
    """
    out: dict[date, float] = {}
    for row in rows_of(payload, url=url):
        if len(row) <= _EX_KIND:
            continue
        if str(row[_EX_CODE]).strip() != stock_no:
            continue
        if "息" not in str(row[_EX_KIND]):
            continue
        cash = to_float(row[_EX_CASH])
        if cash is None:
            continue
        out[roc_to_date(row[_EX_DATE])] = cash
    return out


def fetch_dividends(stock_no: str, start: str, end: str) -> dict[date, float]:
    url = dividends_url(start, end)
    return parse_dividends(fetch_json(url), stock_no, url=url)


# ---------------------------------------------------------------- 三大法人

def institutional_url(day: date) -> str:
    return f"{BASE}/fund/BFI82U?dayDate={day:%Y%m%d}&type=day&response=json"


def parse_institutional(payload: dict[str, Any], day: date, *, url: str = "") -> dict[str, Field]:
    """回傳外資與三大法人合計買賣超，單位為億元。

    對應既有工作表：
      外資     = 「外資及陸資(不含外資自營商)」列（9/9 實測 209.24 億）
      三大法人 = 「合計」列（9/9 實測 211.25 億）
    """
    rows = rows_of(payload, url=url)
    foreign_net: float | None = None
    total_net: float | None = None
    for row in rows:
        if len(row) < 4:
            continue
        label = str(row[0]).strip()
        net = to_float(row[3])
        if net is None:
            continue
        if label.startswith("外資及陸資"):
            foreign_net = net / 1e8
        elif label == "合計":
            total_net = net / 1e8

    as_of = day.isoformat()
    src, u = "TWSE BFI82U", url or institutional_url(day)

    def mk(name: str, val: float | None) -> Field:
        if val is None:
            return Field.missing(name, source=src, url=u, note="當日尚未發布或查無資料")
        return Field(name=name, value=round(val, 2), source=src, url=u,
                     as_of=as_of, status=Status.OK)

    return {
        "foreign_net_100m": mk("foreign_net_100m", foreign_net),
        "institutional_net_100m": mk("institutional_net_100m", total_net),
    }


def fetch_institutional(day: date) -> dict[str, Field]:
    url = institutional_url(day)
    return parse_institutional(fetch_json(url), day, url=url)


# ---------------------------------------------------------------- 個股法人

def stock_institutional_url(day: date) -> str:
    return f"{BASE}/fund/T86?date={day:%Y%m%d}&selectType=ALLBUT0999&response=json"


def parse_stock_institutional(payload: dict[str, Any], stock_no: str, day: date,
                              *, url: str = "") -> dict[str, Field]:
    """個別標的（含 ETF）的法人買賣超股數。比大盤總額更貼近該標的的籌碼面。"""
    src, u = "TWSE T86", url or stock_institutional_url(day)
    foreign = total = None
    for row in rows_of(payload, url=url):
        if len(row) <= _T86_TOTAL_NET:
            continue
        if str(row[_T86_CODE]).strip() != stock_no:
            continue
        foreign = to_float(row[_T86_FOREIGN_NET])
        total = to_float(row[_T86_TOTAL_NET])
        break

    def mk(name: str, val: float | None) -> Field:
        if val is None:
            return Field.missing(name, source=src, url=u, note=f"{stock_no} 當日無法人交易紀錄")
        return Field(name=name, value=val / 1000.0, source=src, url=u,   # 股 -> 張
                     as_of=day.isoformat(), status=Status.OK)

    return {
        "stock_foreign_net_lots": mk("stock_foreign_net_lots", foreign),
        "stock_institutional_net_lots": mk("stock_institutional_net_lots", total),
    }


def fetch_stock_institutional(stock_no: str, day: date) -> dict[str, Field]:
    url = stock_institutional_url(day)
    return parse_stock_institutional(fetch_json(url), stock_no, day, url=url)
