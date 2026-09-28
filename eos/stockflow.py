"""逐檔法人買賣超的儲存與報表產出。

計分規則在 eos/streaks_core.py；這裡只負責「把 T86 存成逐日檔」與
「把榜單寫成前端要的 JSON」。兩者的變動頻率差很多：逐日檔一天寫一次，
計分參數每次調整都要整份重算。

## 為什麼要自己存一份逐日檔

TWSE 的 T86 只給「某一天」的快照，沒有任何連續天數的欄位。
要算連續幾天，只能自己把每天的結果留下來再回頭數。
每天一個檔（`data/stocks/YYYY-MM-DD.json`），壓縮後約 33KB，
一年約 8MB —— 而且逐日存才可稽核：任何一天的榜單都能從原始逐日檔
重新算出來，不是一個會隨時間漂掉的滾動狀態。

## 0 的處理

與 eos/marketflow.py 的波段切分一致：**0 不算方向**，會中斷連續。
「今天沒買也沒賣」不是「今天繼續買」。四項全為 0 的檔在存檔時就被略過，
因此讀不到該檔 = 當天無法人動作 = 中斷（或狀態為「待確認」）。
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any, Iterable

from eos.streaks_core import (DEFAULT_MIN_DAYS, DEFAULT_TOP_N, INSTITUTION_LABEL,
                              SHARES_PER_LOT, STATUS_LABEL, ScoreParams, Streak,
                              candidate_codes, detail_rows, is_etf,
                              leading, streaks, top)

__all__ = ["DEFAULT_MIN_DAYS", "DEFAULT_TOP_N", "DEFAULT_WINDOW", "CANDIDATE_FACTOR",
           "INSTITUTION_LABEL", "SHARES_PER_LOT", "STATUS_LABEL", "ScoreParams",
           "Streak", "is_etf", "leading", "streaks", "top",
           "DEFAULT_DETAIL_DAYS", "EXTEND_LIMIT", "load_before",
           "save_day", "load_day", "load_names", "available_days", "missing_days",
           "load_window", "save_prices", "load_price_day", "price_days",
           "latest_prices", "save_industries", "load_industries",
           "candidate_codes", "detail_rows", "build_detail", "write_detail",
           "industries_age_days", "build_report", "write_report"]

ROOT = Path(__file__).resolve().parent.parent
STOCKS = ROOT / "data" / "stocks"
NAMES_PATH = STOCKS / "names.json"
PRICES = STOCKS / "px"
INDUSTRIES_PATH = STOCKS / "industries.json"

DEFAULT_WINDOW = 60

# 〈個股法人明細〉表格預設列出的天數。與 DEFAULT_WINDOW 拆開：
# 連續天數要看得夠遠，表格要短到手機上滑得完。
DEFAULT_DETAIL_DAYS = 40

# 連續天數往視窗之前最多續數幾天。設上限是因為這只影響「天數」這個數字，
# 不影響排名，沒必要為了一個數字把全部歷史都載進記憶體。
EXTEND_LIMIT = 400
# 每一側實際存進報表的候補筆數＝top_n × 這個倍數（見 build_report）
CANDIDATE_FACTOR = 4


# ---------------------------------------------------------------- 儲存

def day_path(day: date) -> Path:
    return STOCKS / f"{day.isoformat()}.json"


def save_day(day: date, nets: dict[str, list[int]], names: dict[str, str]) -> Path:
    """寫入當日逐檔淨額，並把名稱併進共用的對照表。

    名稱單獨一個檔而不是每天重複存 1,300 筆中文名 —— 名稱幾乎不變，
    每天存一次等於每天多 25KB 的重複資料。
    """
    p = day_path(day)
    p.parent.mkdir(parents=True, exist_ok=True)
    # separators 去掉空白：這個檔是機器讀的，可讀性由 scripts 提供
    p.write_text(json.dumps(nets, separators=(",", ":"), sort_keys=True),
                 encoding="utf-8")

    merged = load_names()
    merged.update({k: v for k, v in names.items() if v})
    NAMES_PATH.write_text(
        json.dumps(merged, ensure_ascii=False, separators=(",", ":"), sort_keys=True),
        encoding="utf-8")
    return p


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def load_names() -> dict[str, str]:
    return _read_json(NAMES_PATH)


def save_prices(day: date, prices: dict[str, tuple[float, float | None]]) -> Path:
    """個股當日收盤與漲跌幅，一天一個檔（約 26KB）。

    產業資金輪動要做「價格同向」確認，需要每一天每一檔的漲跌，
    不是只有最新一天 —— 原本只留一份最新的，回補舊日子時還會把它蓋掉。
    一天一檔就沒有這個問題：最新是哪一天由檔名決定，不是由寫入順序決定。
    """
    PRICES.mkdir(parents=True, exist_ok=True)
    flat = {c: [v[0], v[1]] for c, v in prices.items()}
    (PRICES / f"{day.isoformat()}.json").write_text(
        json.dumps(flat, separators=(",", ":"), sort_keys=True), encoding="utf-8")
    return PRICES / f"{day.isoformat()}.json"


def price_days() -> list[date]:
    if not PRICES.exists():
        return []
    days = []
    for p in PRICES.glob("*.json"):
        try:
            days.append(date.fromisoformat(p.stem))
        except ValueError:
            continue
    return sorted(days)


def load_price_day(day: date) -> dict[str, list[float | None]] | None:
    p = PRICES / f"{day.isoformat()}.json"
    if not p.exists():
        return None
    return json.loads(p.read_text(encoding="utf-8"))


def latest_prices(on_or_before: date | None = None) -> tuple[str | None, dict[str, float]]:
    """給 Top5 用的參考價：指定日（或整體最新）那天的收盤。"""
    days = [d for d in price_days() if on_or_before is None or d <= on_or_before]
    if not days:
        return None, {}
    day = days[-1]
    raw = load_price_day(day) or {}
    return day.isoformat(), {c: v[0] for c, v in raw.items() if v and v[0] is not None}


def save_industries(day: date, industries: dict[str, str]) -> Path:
    """上市公司的產業別對照。幾乎不變，所以存一份、記下更新日即可。"""
    STOCKS.mkdir(parents=True, exist_ok=True)
    INDUSTRIES_PATH.write_text(
        json.dumps({"as_of": day.isoformat(), "industry": industries},
                   ensure_ascii=False, separators=(",", ":"), sort_keys=True),
        encoding="utf-8")
    return INDUSTRIES_PATH


def load_industries() -> dict[str, str]:
    return (_read_json(INDUSTRIES_PATH) or {}).get("industry") or {}


def industries_age_days(today: date) -> int | None:
    """對照表幾天沒更新了。None 代表根本還沒有。"""
    raw = _read_json(INDUSTRIES_PATH) or {}
    as_of = raw.get("as_of")
    if not as_of:
        return None
    try:
        return (today - date.fromisoformat(as_of)).days
    except ValueError:
        return None


def load_day(day: date) -> dict[str, list[int]] | None:
    p = day_path(day)
    if not p.exists():
        return None
    return json.loads(p.read_text(encoding="utf-8"))


def available_days() -> list[date]:
    """已存在的逐檔快照日期，由舊到新。"""
    if not STOCKS.exists():
        return []
    days = []
    for p in STOCKS.glob("*.json"):
        try:
            days.append(date.fromisoformat(p.stem))
        except ValueError:
            continue                      # names.json / prices.json 之類的非日期檔
    return sorted(days)


def missing_days(wanted: Iterable[date]) -> list[date]:
    have = set(available_days())
    return [d for d in sorted(wanted) if d not in have]


def load_before(start: date, *, limit: int = EXTEND_LIMIT
                ) -> list[tuple[date, dict[str, list[int]]]]:
    """start 之前最多 limit 個已存在的交易日，由舊到新。

    只給 streaks 續數連續天數用，不參與排名 —— 見 streaks_core._extend_back。
    """
    days = [d for d in available_days() if d < start][-limit:]
    out = []
    for d in days:
        nets = load_day(d)
        if nets is not None:
            out.append((d, nets))
    return out


def load_window(end: date, lookback: int = DEFAULT_WINDOW
                ) -> list[tuple[date, dict[str, list[int]]]]:
    """end 當日（含）往前最多 lookback 個已存在的交易日，由舊到新。"""
    days = [d for d in available_days() if d <= end][-lookback:]
    out = []
    for d in days:
        nets = load_day(d)
        if nets is not None:
            out.append((d, nets))
    return out


# ---------------------------------------------------------------- 報表

def build_report(end: date, *, lookback: int = DEFAULT_WINDOW,
                 params: ScoreParams | None = None,
                 institutions: Iterable[str] = ("foreign", "trust", "dealer", "total"),
                 ) -> dict[str, Any]:
    p = params or ScoreParams()
    window = load_window(end, lookback)
    # 排名只看 window，但連續天數頂到視窗第一天時要往更早的資料續數。
    # 兩者分開：排名要看近期，天數要看真相。
    earlier = load_before(window[0][0], limit=EXTEND_LIMIT) if window else []
    names = load_names()
    price_date, prices = latest_prices(end)

    payload: dict[str, Any] = {
        "as_of": end.isoformat(),
        "window_days": len(window),
        "window_start": window[0][0].isoformat() if window else None,
        # 排名視窗之外還能往回數幾天（天數續數用，不參與排名）
        "extend_days": len(earlier),
        "extend_start": earlier[0][0].isoformat() if earlier else None,
        "params": p.to_dict(),
        "min_days": p.min_days,
        "top_n": p.top_n,
        # 參考價不是報表當日的收盤時要看得出來，不讓「估」變成「假裝是當日」
        "price_as_of": price_date,
        "source": "TWSE T86",
        "institutions": {},
    }

    # 多存一些候補：前端可以切換「排除 ETF」，濾掉之後還要湊得滿 top_n 名。
    # 濾好再存兩份清單會讓同一件事有兩個真相來源，濾的規則一改就要重跑收集。
    spare = p.top_n * CANDIDATE_FACTOR

    lead = leading(window, params=p, n=spare, names=names, prices=prices,
                   earlier=earlier, institutions=institutions)
    payload["institutions"]["leading"] = {
        "label": "主導法人",
        "buy": [s.to_dict(p) for s in lead["buy"]],
        "sell": [s.to_dict(p) for s in lead["sell"]],
    }
    for inst in institutions:
        picked = top(window, inst, params=p, n=spare, names=names, prices=prices,
                     earlier=earlier)
        payload["institutions"][inst] = {
            "label": INSTITUTION_LABEL.get(inst, inst),
            "buy": [s.to_dict(p) for s in picked["buy"]],
            "sell": [s.to_dict(p) for s in picked["sell"]],
        }
    return payload


def write_report(end: date, **kw: Any) -> Path:
    out = ROOT / "data" / "top5_streaks.json"
    out.write_text(json.dumps(build_report(end, **kw), ensure_ascii=False, indent=1),
                   encoding="utf-8")
    return out


def build_detail(end: date, report: dict[str, Any], *,
                 lookback: int = DEFAULT_WINDOW,
                 detail_days: int | None = DEFAULT_DETAIL_DAYS) -> dict[str, Any]:
    """候選標的的逐日三大法人明細，對齊工作表〈個股法人明細〉。

    **連續天數用完整視窗算，只有顯示才截尾。** 兩者拆開是必要的：
    連買天數算到一百多天很正常（玉山金投信在補半年之前就已經頂到視窗上緣），
    但手機上沒有人要滑一張一百多列的表。如果改成只拿最近 N 天去算連續，
    截斷處的天數會從 1 重新起算，這一頁就會跟〈連續買賣 Top5〉互相矛盾。
    """
    window = load_window(end, lookback)
    names = load_names()
    codes = candidate_codes(report)
    shown = len(window) if detail_days is None else min(detail_days, len(window))

    def rows_for(code: str) -> list[dict[str, Any]]:
        full = detail_rows(window, code, name=names.get(code, code))
        return full[-shown:] if shown else full

    return {
        "as_of": end.isoformat(),
        # window_* 是連續天數的計算範圍；shown_* 是這張表實際列出來的範圍
        "window_start": window[0][0].isoformat() if window else None,
        "window_days": len(window),
        "shown_days": shown,
        "truncated": shown < len(window),
        "source": "TWSE T86",
        "stocks": [{"code": c, "name": names.get(c, c),
                    "etf": is_etf(c),
                    "rows": rows_for(c)}
                   for c in codes],
    }


def write_detail(end: date, report: dict[str, Any], **kw: Any) -> Path:
    out = ROOT / "data" / "stock_detail.json"
    out.write_text(json.dumps(build_detail(end, report, **kw),
                              ensure_ascii=False, indent=1), encoding="utf-8")
    return out
