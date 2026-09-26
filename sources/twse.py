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
_T86_NAME = 1
_T86_FOREIGN_NET = 4        # 外陸資買賣超股數(不含外資自營商)
_T86_FOREIGN_DEALER_NET = 7  # 外資自營商買賣超股數
_T86_TRUST_NET = 10         # 投信買賣超股數
_T86_DEALER_NET = 11        # 自營商買賣超股數（自行買賣＋避險）
_T86_TOTAL_NET = 18         # 三大法人買賣超股數

# 逐檔存檔時的欄位順序。三大法人合計＝這四項之和
# （實測 2026-09-24 00403A：64,496,136 + 0 + 0 + 127,820,386 = 192,316,522 ✓），
# 所以合計不另外存，避免同一個數字有兩份可能不一致的來源。
INSTITUTION_COLUMNS = (_T86_FOREIGN_NET, _T86_FOREIGN_DEALER_NET,
                       _T86_TRUST_NET, _T86_DEALER_NET)
INSTITUTIONS = ("foreign", "foreign_dealer", "trust", "dealer")


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


def parse_stock_institutional_all(payload: dict[str, Any], *,
                                  url: str = "") -> tuple[dict[str, list[int]], dict[str, str]]:
    """整份 T86 拆成「代號 -> 四類法人買賣超股數」與「代號 -> 名稱」。

    單位保留**股**而不換成張：零股交易會讓 股/1000 出現小數，
    存成浮點數之後連續加總會累積誤差，而連續買賣超的判定只看正負號與
    累計量，用整數股最乾淨，要顯示成張時再除。

    四項全為 0 的檔不存 —— 當天沒有任何法人動作，對連續判定而言
    與「沒有這一檔」等價，卻佔掉四分之一的檔數。
    """
    nets: dict[str, list[int]] = {}
    names: dict[str, str] = {}
    for row in rows_of(payload, url=url):
        if len(row) <= _T86_TOTAL_NET:
            continue
        code = str(row[_T86_CODE]).strip()
        if not code:
            continue
        vals = [int(to_float(row[c]) or 0) for c in INSTITUTION_COLUMNS]
        if not any(vals):
            continue
        nets[code] = vals
        names[code] = str(row[_T86_NAME]).strip()
    return nets, names


def fetch_stock_institutional_all(day: date) -> tuple[dict[str, list[int]], dict[str, str], str]:
    url = stock_institutional_url(day)
    nets, names = parse_stock_institutional_all(fetch_json(url), url=url)
    return nets, names, url


def fetch_stock_institutional(stock_no: str, day: date) -> dict[str, Field]:
    url = stock_institutional_url(day)
    return parse_stock_institutional(fetch_json(url), stock_no, day, url=url)


# ================================================================ 大盤

# FMTQIK 欄位位置（日期 成交股數 成交金額 成交筆數 發行量加權股價指數 漲跌點數）
_MK_DATE, _MK_SHARES, _MK_TURNOVER, _MK_TRADES, _MK_INDEX, _MK_CHANGE = 0, 1, 2, 3, 4, 5

BILLION = 1e8          # 億元
THOUSAND = 1e3         # 仟元


def market_index_url(yyyymm: str) -> str:
    """大盤每日成交資訊（含發行量加權股價指數），一次回傳整月。"""
    return f"{BASE}/afterTrading/FMTQIK?date={yyyymm}01&response=json"


def parse_market_index(payload: dict[str, Any], *, url: str = "") -> dict[date, dict[str, float | None]]:
    """回傳 {交易日: {index, change, turnover_100m, shares, trades}}。"""
    out: dict[date, dict[str, float | None]] = {}
    for row in rows_of(payload, url=url):
        if len(row) <= _MK_CHANGE:
            continue
        turnover = to_float(row[_MK_TURNOVER])
        out[roc_to_date(row[_MK_DATE])] = {
            "index": to_float(row[_MK_INDEX]),
            "change": to_float(row[_MK_CHANGE]),
            "turnover_100m": None if turnover is None else turnover / BILLION,
            "shares": to_float(row[_MK_SHARES]),
            "trades": to_float(row[_MK_TRADES]),
        }
    return out


def market_index_fields(history: dict[date, dict[str, float | None]], day: date,
                        *, url: str) -> dict[str, Field]:
    src = "TWSE FMTQIK"
    rec = history.get(day)
    names = ("taiex", "taiex_change", "taiex_change_pct", "market_turnover_100m")
    if not rec:
        return {n: Field.missing(n, source=src, url=url, note="當日無大盤成交資訊（休市或尚未發布）")
                for n in names}

    idx, chg = rec["index"], rec["change"]
    # 漲跌幅由指數與漲跌點數回推前日收盤，TWSE 本身不提供百分比欄位
    pct = None
    if idx is not None and chg is not None and (idx - chg) != 0:
        pct = chg / (idx - chg)

    def mk(name: str, value: float | None) -> Field:
        if value is None:
            return Field.missing(name, source=src, url=url, note="該欄位為空")
        return Field(name=name, value=value, source=src, url=url,
                     as_of=day.isoformat(), status=Status.OK)

    return {
        "taiex": mk("taiex", idx),
        "taiex_change": mk("taiex_change", chg),
        "taiex_change_pct": mk("taiex_change_pct", pct),
        "market_turnover_100m": mk("market_turnover_100m", rec["turnover_100m"]),
    }


def fetch_market_index(day: date) -> dict[str, Field]:
    url = market_index_url(f"{day:%Y%m}")
    return market_index_fields(parse_market_index(fetch_json(url), url=url), day, url=url)


# ---------------------------------------------------------------- 個股收盤價

_PRICE_TABLE_HINT = "每日收盤行情"
_PRICE_CODE, _PRICE_CLOSE = 0, 8


def stock_prices_url(day: date) -> str:
    """全市場個股當日收盤行情（MI_INDEX 的其中一張表）。

    連續買賣超的張數在不同價位的股票之間不可比 —— 00919 賣超 79.8 萬張
    看起來驚人，換算成金額只有 259 億。要算約當金額就需要每一檔的價格。
    """
    return f"{BASE}/afterTrading/MI_INDEX?date={day:%Y%m%d}&type=ALLBUT0999&response=json"


def parse_stock_prices(payload: dict[str, Any], *, url: str = "") -> dict[str, float]:
    """回傳 {證券代號: 收盤價}。

    MI_INDEX 一次回傳十幾張表（各類指數、大盤統計、漲跌家數…），
    以標題挑出「每日收盤行情」那一張，不靠索引 —— 表的順序會隨改版變動。
    """
    out: dict[str, float] = {}
    for table in payload.get("tables") or []:
        if _PRICE_TABLE_HINT not in str(table.get("title", "")):
            continue
        for row in table.get("data") or []:
            if len(row) <= _PRICE_CLOSE:
                continue
            code = str(row[_PRICE_CODE]).strip()
            close = to_float(row[_PRICE_CLOSE])
            if code and close is not None:
                out[code] = close
        break
    return out


def fetch_stock_prices(day: date) -> tuple[dict[str, float], str]:
    url = stock_prices_url(day)
    return parse_stock_prices(fetch_json(url), url=url), url


# ---------------------------------------------------------------- 大盤開高低收

# MI_5MINS_HIST 的欄位：日期、開盤指數、最高指數、最低指數、收盤指數
_OHLC_DATE, _OHLC_OPEN, _OHLC_HIGH, _OHLC_LOW, _OHLC_CLOSE = 0, 1, 2, 3, 4


def taiex_ohlc_url(yyyymm: str) -> str:
    """大盤發行量加權股價指數的開高低收，一次回傳整月。

    FMTQIK 只有收盤指數。壓力／支撐用的是**盤中**高低
    （工作表的壓力2 = 期間最高盤中價，不是最高收盤），非得另外抓這一支。
    """
    return f"{BASE}/TAIEX/MI_5MINS_HIST?date={yyyymm}01&response=json"


def parse_taiex_ohlc(payload: dict[str, Any], *,
                     url: str = "") -> dict[date, dict[str, float | None]]:
    """回傳 {交易日: {open, high, low, close}}。"""
    out: dict[date, dict[str, float | None]] = {}
    for row in rows_of(payload, url=url):
        if len(row) <= _OHLC_CLOSE:
            continue
        try:
            day = roc_to_date(row[_OHLC_DATE])
        except (ValueError, AttributeError):
            continue                      # 月報表尾端偶有統計列
        out[day] = {
            "open": to_float(row[_OHLC_OPEN]),
            "high": to_float(row[_OHLC_HIGH]),
            "low": to_float(row[_OHLC_LOW]),
            "close": to_float(row[_OHLC_CLOSE]),
        }
    return out


def taiex_ohlc_fields(history: dict[date, dict[str, float | None]], day: date,
                      *, url: str) -> dict[str, Field]:
    src = "TWSE MI_5MINS_HIST"
    rec = history.get(day)
    names = ("taiex_open", "taiex_high", "taiex_low")
    if not rec:
        return {n: Field.missing(n, source=src, url=url,
                                 note="當日無大盤開高低收（休市或尚未發布）")
                for n in names}

    def mk(name: str, value: float | None) -> Field:
        if value is None:
            return Field.missing(name, source=src, url=url, note="該欄位為空")
        return Field(name=name, value=value, source=src, url=url,
                     as_of=day.isoformat(), status=Status.OK)

    return {"taiex_open": mk("taiex_open", rec["open"]),
            "taiex_high": mk("taiex_high", rec["high"]),
            "taiex_low": mk("taiex_low", rec["low"])}


def fetch_taiex_ohlc(day: date) -> dict[str, Field]:
    url = taiex_ohlc_url(f"{day:%Y%m}")
    return taiex_ohlc_fields(parse_taiex_ohlc(fetch_json(url), url=url), day, url=url)


# ---------------------------------------------------------------- 融資餘額

def margin_url(day: date) -> str:
    return f"{BASE}/marginTrading/MI_MARGN?date={day:%Y%m%d}&selectType=MS&response=json"


def parse_margin(payload: dict[str, Any], day: date, *, url: str = "") -> dict[str, Field]:
    """融資餘額。TWSE 以仟元計，模型用億元。

    注意這個端點回的是 tables 陣列而不是單一 data，而且第二個表可能是空殼
    （fields 為 null）—— 直接索引 tables[0] 以外的位置會炸。
    """
    src, u = "TWSE MI_MARGN", url or margin_url(day)
    today = prev = None

    tables = payload.get("tables") if isinstance(payload, dict) else None
    for t in tables or []:
        if not isinstance(t, dict):
            continue
        for row in t.get("data") or []:
            if len(row) < 6:
                continue
            # 只取金額列；「融資(交易單位)」是張數，不是金額
            if "融資金額" in str(row[0]):
                prev = to_float(row[4])
                today = to_float(row[5])
                break

    def mk(name: str, val_k: float | None, note: str = "") -> Field:
        if val_k is None:
            return Field.missing(name, source=src, url=u, note="當日尚未發布或休市")
        return Field(name=name, value=val_k * THOUSAND / BILLION, source=src, url=u,
                     as_of=day.isoformat(), status=Status.OK, note=note)

    out = {
        "margin_balance_100m": mk("margin_balance_100m", today,
                                  "融資餘額；散戶槓桿水位，續增而指數收黑為接刀訊號"),
        "margin_prev_100m": mk("margin_prev_100m", prev),
    }
    if today is not None and prev is not None:
        out["margin_change_100m"] = Field(
            name="margin_change_100m", value=(today - prev) * THOUSAND / BILLION,
            source=src, url=u, as_of=day.isoformat(), status=Status.OK)
    else:
        out["margin_change_100m"] = Field.missing("margin_change_100m", source=src, url=u)
    return out


def fetch_margin(day: date) -> dict[str, Field]:
    url = margin_url(day)
    return parse_margin(fetch_json(url), day, url=url)
