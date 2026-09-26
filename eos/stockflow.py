"""逐檔法人買賣超的儲存與「連續同向」判定。

對齊工作表〈連續買賣Top5〉分頁：找出同一類法人連續同方向買（或賣）
天數最長的個股。工作表的選股規則第一條是「同一法人連續同向 ≥ 門檻日數」——
門檻與其餘條件寫在 config/streaks.yaml，不寫死在程式裡。

## 為什麼要自己存一份逐日檔

TWSE 的 T86 只給「某一天」的快照，沒有任何連續天數的欄位。
要算連續幾天，只能自己把每天的結果留下來再回頭數。
每天一個檔（`data/stocks/YYYY-MM-DD.json`），壓縮後約 33KB，
一年約 8MB —— 可以接受，而且逐日存才可稽核：
任何一天的 Top5 都能從原始逐日檔重新算出來，不是一個滾動狀態。

## 0 的處理

與 eos/marketflow.py 的波段切分一致：**0 不算方向**，會中斷連續。
「今天沒買也沒賣」不是「今天繼續買」，把它併進買波會把兩段黏成一段。
四項全為 0 的檔在存檔時就被略過，因此讀不到該檔 = 當天無動作 = 中斷。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Iterable

from sources.twse import INSTITUTIONS

ROOT = Path(__file__).resolve().parent.parent
STOCKS = ROOT / "data" / "stocks"
NAMES_PATH = STOCKS / "names.json"

# 顯示用名稱。合計是四項相加，不另外存（見 twse.INSTITUTION_COLUMNS）
INSTITUTION_LABEL = {
    "foreign": "外資",
    "foreign_dealer": "外資自營商",
    "trust": "投信",
    "dealer": "自營商",
    "total": "三大法人",
}

DEFAULT_MIN_DAYS = 3
DEFAULT_TOP_N = 5
DEFAULT_WINDOW = 60

# 每一側實際存進報表的候補筆數＝top_n × 這個倍數（見 build_report）
CANDIDATE_FACTOR = 4

SHARES_PER_LOT = 1000


def is_etf(code: str) -> bool:
    """以 0 開頭者為 ETF／ETN 等受益憑證，不是一般上市公司。

    判準是「開頭為 0」而不是「開頭為 00」：0050、0056 只有四碼，
    用 00 開頭當條件會把最老的那幾檔 ETF 漏掉。一般個股的代號
    介於 1101–9958，不會以 0 開頭，所以這條規則不會誤傷個股。

    為什麼要標出來：自營商的連續買賣超榜幾乎全是 ETF，那是造市與
    申購贖回避險的必然結果，不是「自營商看好這檔」。不直接濾掉是因為
    工作表的完整選股規則（②③）沒能讀回來，寧可標記讓人自己選，
    也不要替使用者做一個沒有依據的刪除。
    """
    return code.startswith("0")


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


def load_names() -> dict[str, str]:
    if not NAMES_PATH.exists():
        return {}
    try:
        return json.loads(NAMES_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


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
            continue                      # names.json 之類的非日期檔
    return sorted(days)


def missing_days(wanted: Iterable[date]) -> list[date]:
    have = set(available_days())
    return [d for d in sorted(wanted) if d not in have]


def load_window(end: date, lookback: int = DEFAULT_WINDOW) -> list[tuple[date, dict[str, list[int]]]]:
    """end 當日（含）往前最多 lookback 個已存在的交易日，由舊到新。"""
    days = [d for d in available_days() if d <= end][-lookback:]
    out = []
    for d in days:
        nets = load_day(d)
        if nets is not None:
            out.append((d, nets))
    return out


# ---------------------------------------------------------------- 連續判定

@dataclass(frozen=True)
class Streak:
    code: str
    name: str
    institution: str
    direction: str            # "buy" / "sell"
    days: int
    shares: int               # 期間累計淨額（股），帶正負號
    start: date
    truncated: bool           # 連到視窗最舊一天，真正天數可能更長

    @property
    def etf(self) -> bool:
        return is_etf(self.code)

    @property
    def lots(self) -> float:
        return self.shares / SHARES_PER_LOT

    def to_dict(self) -> dict[str, Any]:
        return {"code": self.code, "name": self.name,
                "institution": self.institution, "direction": self.direction,
                "days": self.days, "shares": self.shares,
                "lots": round(self.lots, 1), "start": self.start.isoformat(),
                "truncated": self.truncated, "etf": self.etf}


def _net(row: list[int] | None, institution: str) -> int:
    if row is None:
        return 0
    if institution == "total":
        return sum(row)
    try:
        return row[INSTITUTIONS.index(institution)]
    except ValueError as exc:                        # noqa: PERF203
        raise KeyError(f"未知的法人別：{institution}") from exc


def streaks(window: list[tuple[date, dict[str, list[int]]]], institution: str,
            *, min_days: int = DEFAULT_MIN_DAYS) -> list[Streak]:
    """視窗內「到最後一天為止仍在進行中」的連續同向紀錄。

    只看**目前仍延續**的連續，不找歷史上曾經出現過的最長連續 ——
    工作表要的是「現在誰還在被連續買」，一段兩週前就結束的連買
    對今天的決策沒有意義。
    """
    if not window:
        return []
    names = load_names()
    last_date, last_nets = window[-1]

    out: list[Streak] = []
    for code, row in last_nets.items():
        net = _net(row, institution)
        if net == 0:
            continue                      # 0 沒有方向，不成立連續
        sign = 1 if net > 0 else -1

        days = 0
        total = 0
        start = last_date
        for d, nets in reversed(window):
            v = _net(nets.get(code), institution)
            if v == 0 or (1 if v > 0 else -1) != sign:
                break
            days += 1
            total += v
            start = d

        if days < min_days:
            continue
        out.append(Streak(
            code=code, name=names.get(code, code), institution=institution,
            direction="buy" if sign > 0 else "sell", days=days, shares=total,
            start=start, truncated=(days == len(window)),
        ))
    return out


def top(window: list[tuple[date, dict[str, list[int]]]], institution: str,
        *, min_days: int = DEFAULT_MIN_DAYS,
        n: int = DEFAULT_TOP_N) -> dict[str, list[Streak]]:
    """買方與賣方各取前 n 名：先比連續天數，同天數再比累計量。"""
    all_ = streaks(window, institution, min_days=min_days)
    out: dict[str, list[Streak]] = {}
    for direction in ("buy", "sell"):
        rows = [s for s in all_ if s.direction == direction]
        rows.sort(key=lambda s: (-s.days, -abs(s.shares)))
        out[direction] = rows[:n]
    return out


def build_report(end: date, *, lookback: int = DEFAULT_WINDOW,
                 min_days: int = DEFAULT_MIN_DAYS, n: int = DEFAULT_TOP_N,
                 institutions: Iterable[str] = ("foreign", "trust", "dealer", "total"),
                 ) -> dict[str, Any]:
    window = load_window(end, lookback)
    payload: dict[str, Any] = {
        "as_of": end.isoformat(),
        "window_days": len(window),
        "window_start": window[0][0].isoformat() if window else None,
        "min_days": min_days,
        "top_n": n,
        "source": "TWSE T86",
        "institutions": {},
    }
    # 多存一些候補：前端可以切換「排除 ETF」，濾掉之後還要湊得滿 n 名。
    # 濾好再存兩份清單會讓同一件事有兩個真相來源，濾的規則一改就要重跑收集。
    for inst in institutions:
        picked = top(window, inst, min_days=min_days, n=n * CANDIDATE_FACTOR)
        payload["institutions"][inst] = {
            "label": INSTITUTION_LABEL.get(inst, inst),
            "buy": [s.to_dict() for s in picked["buy"]],
            "sell": [s.to_dict() for s in picked["sell"]],
        }
    return payload


def write_report(end: date, **kw: Any) -> Path:
    out = ROOT / "data" / "top5_streaks.json"
    out.write_text(json.dumps(build_report(end, **kw), ensure_ascii=False, indent=1),
                   encoding="utf-8")
    return out
