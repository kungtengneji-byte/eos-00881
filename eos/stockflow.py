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
                              is_etf, leading, streaks, top)

__all__ = ["DEFAULT_MIN_DAYS", "DEFAULT_TOP_N", "DEFAULT_WINDOW", "CANDIDATE_FACTOR",
           "INSTITUTION_LABEL", "SHARES_PER_LOT", "STATUS_LABEL", "ScoreParams",
           "Streak", "is_etf", "leading", "streaks", "top",
           "save_day", "load_day", "load_names", "available_days", "missing_days",
           "load_window", "save_prices", "load_prices", "build_report", "write_report"]

ROOT = Path(__file__).resolve().parent.parent
STOCKS = ROOT / "data" / "stocks"
NAMES_PATH = STOCKS / "names.json"
PRICES_PATH = STOCKS / "prices.json"

DEFAULT_WINDOW = 60
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


def save_prices(day: date, prices: dict[str, float]) -> Path:
    """個股參考價。只留最新一份 —— 約當金額是「估」，用當日收盤即可；
    存整段歷史只為了算一個乘數，不值得每天多 30KB。"""
    STOCKS.mkdir(parents=True, exist_ok=True)
    PRICES_PATH.write_text(
        json.dumps({"as_of": day.isoformat(), "close": prices},
                   ensure_ascii=False, separators=(",", ":"), sort_keys=True),
        encoding="utf-8")
    return PRICES_PATH


def load_prices() -> tuple[str | None, dict[str, float]]:
    raw = _read_json(PRICES_PATH)
    return raw.get("as_of"), raw.get("close") or {}


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
    names = load_names()
    price_date, prices = load_prices()

    payload: dict[str, Any] = {
        "as_of": end.isoformat(),
        "window_days": len(window),
        "window_start": window[0][0].isoformat() if window else None,
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
                   institutions=institutions)
    payload["institutions"]["leading"] = {
        "label": "主導法人",
        "buy": [s.to_dict(p) for s in lead["buy"]],
        "sell": [s.to_dict(p) for s in lead["sell"]],
    }
    for inst in institutions:
        picked = top(window, inst, params=p, n=spare, names=names, prices=prices)
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
