"""連續買賣超的計分核心，對齊工作表〈連續買賣Top5〉。

分成獨立一個模組而不是塞回 stockflow：stockflow 負責「把 T86 存成逐日檔」，
這裡負責「從逐日檔算出榜單」。前者一天寫一次、後者每次調參數都要重算，
兩者的變動頻率差很多。

## 工作表的模型（左上角藍字可調區）

    連續門檻(日)            3
    天數權重                1
    持續中加分             10
    待確認加分              5
    兩類以上法人同向加分      3

    分數 = 天數 × 天數權重 + 狀態加分 + 兩類以上法人同向加分 + 天數/100

最後那一項工作表的規則②沒有寫出來，但七筆實例裡有六筆只有加上它才對得
起來（第一金 28.16、聯電 24.11、兆豐金 19.12、台新新光金 18.18、
中華電 18.13、華邦電 8.05；剩下一筆 00919 差 0.01）。
它是同分時的排序尾數，不影響名次以外的判讀。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any, Iterable

from sources.twse import INSTITUTIONS

SHARES_PER_LOT = 1000
DEFAULT_MIN_DAYS = 3
DEFAULT_TOP_N = 5

INSTITUTION_LABEL = {
    "foreign": "外資",
    "trust": "投信",
    "dealer": "自營商",
    "total": "三大法人合計",
}

STATUS_LABEL = {"running": "持續中", "pending": "待確認", "broken": "已中斷"}


def is_etf(code: str) -> bool:
    """以 0 開頭者為 ETF／ETN 等受益憑證，不是一般上市公司。

    判準是「開頭為 0」而不是「開頭為 00」：0050、0056 只有四碼。
    一般個股代號介於 1101–9958，不會以 0 開頭，這條規則不會誤傷個股。
    """
    return code.startswith("0")


@dataclass(frozen=True)
class ScoreParams:
    """模型參數，對應工作表左上角那塊藍字可調區。"""
    min_days: int = DEFAULT_MIN_DAYS
    day_weight: float = 1.0
    bonus_running: float = 10.0
    bonus_pending: float = 5.0
    bonus_multi_institution: float = 3.0
    top_n: int = DEFAULT_TOP_N

    @classmethod
    def from_config(cls, cfg: dict[str, Any] | None) -> "ScoreParams":
        c = cfg or {}
        return cls(min_days=int(c.get("min_days", DEFAULT_MIN_DAYS)),
                   day_weight=float(c.get("day_weight", 1.0)),
                   bonus_running=float(c.get("bonus_running", 10.0)),
                   bonus_pending=float(c.get("bonus_pending", 5.0)),
                   bonus_multi_institution=float(c.get("bonus_multi_institution", 3.0)),
                   top_n=int(c.get("top_n", DEFAULT_TOP_N)))

    def to_dict(self) -> dict[str, Any]:
        return {"min_days": self.min_days, "day_weight": self.day_weight,
                "bonus_running": self.bonus_running,
                "bonus_pending": self.bonus_pending,
                "bonus_multi_institution": self.bonus_multi_institution,
                "top_n": self.top_n}


@dataclass(frozen=True)
class Streak:
    code: str
    name: str
    institution: str
    direction: str            # buy / sell
    days: int
    shares: int               # 期間累計淨額（股），帶正負號
    start: date
    end: date
    truncated: bool           # 連到視窗最舊一天，真正天數可能更長
    status: str               # running / pending / broken
    peers: int                # 迄日當天同方向的法人類別數
    price: float | None = None

    @property
    def etf(self) -> bool:
        return is_etf(self.code)

    @property
    def lots(self) -> float:
        return self.shares / SHARES_PER_LOT

    @property
    def amount_100m(self) -> float | None:
        """約當金額（億元）＝ 張 × 1,000 股 × 參考價 / 1e8。

        實測工作表：聯電 100,630 張 × 141.50 元 = 142.39 億，完全相符。
        張數在不同價位的股票之間不可比 —— 00919 賣超 79.8 萬張看起來驚人，
        換算成金額只有 259 億。
        """
        if self.price is None:
            return None
        return self.lots * SHARES_PER_LOT * self.price / 1e8

    def score(self, p: ScoreParams) -> float:
        bonus = {"running": p.bonus_running,
                 "pending": p.bonus_pending}.get(self.status, 0.0)
        peer = p.bonus_multi_institution if self.peers >= 2 else 0.0
        return self.days * p.day_weight + bonus + peer + self.days / 100

    def to_dict(self, p: ScoreParams) -> dict[str, Any]:
        amt = self.amount_100m
        return {"code": self.code, "name": self.name,
                "institution": self.institution,
                "institution_label": INSTITUTION_LABEL.get(self.institution,
                                                           self.institution),
                "direction": self.direction, "days": self.days,
                "shares": self.shares, "lots": round(self.lots, 1),
                "price": self.price,
                "amount_100m": None if amt is None else round(amt, 2),
                "start": self.start.isoformat(), "end": self.end.isoformat(),
                "truncated": self.truncated, "status": self.status,
                "status_label": STATUS_LABEL.get(self.status, self.status),
                "peers": self.peers, "etf": self.etf,
                "score": round(self.score(p), 2)}


# ---------------------------------------------------------------- 逐日切段

def _net(row: list[int] | None, institution: str) -> int:
    if row is None:
        return 0
    if institution == "total":
        return sum(row)
    if institution == "foreign":
        # 外資＝外陸資＋外資自營商，與 TWSE「三大法人」的口徑一致
        return row[0] + row[1]
    try:
        return row[INSTITUTIONS.index(institution)]
    except ValueError as exc:
        raise KeyError(f"未知的法人別：{institution}") from exc


def _sign(v: int) -> int:
    return (v > 0) - (v < 0)


def _peers_on(nets: dict[str, list[int]], code: str, sign: int) -> int:
    """某一天該檔有幾類法人與指定方向同向。

    三大法人的口徑：外資（含外資自營商）、投信、自營商。
    外資自營商不單獨算一類，否則「外資買、外資自營商也買」會被誤判成兩類同向。
    """
    row = nets.get(code)
    if row is None:
        return 0
    cats = (row[0] + row[1], row[2], row[3])
    return sum(1 for v in cats if _sign(v) == sign)


def _runs(series: list[tuple[date, int]]) -> list[tuple[int, int, int]]:
    """切成同向的連續段，回傳 [(起index, 迄index, 累計)]。

    0 與缺值都中斷連續 —— 與 marketflow 的波段切分同一條規則。
    「今天沒買也沒賣」不是「今天繼續買」。
    """
    out: list[tuple[int, int, int]] = []
    i = 0
    while i < len(series):
        sign = _sign(series[i][1])
        if sign == 0:
            i += 1
            continue
        total = 0
        j = i
        while j < len(series) and _sign(series[j][1]) == sign:
            total += series[j][1]
            j += 1
        out.append((i, j - 1, total))
        i = j
    return out


def streaks(window: list[tuple[date, dict[str, list[int]]]], institution: str,
            *, min_days: int = DEFAULT_MIN_DAYS,
            names: dict[str, str] | None = None,
            prices: dict[str, float] | None = None) -> list[Streak]:
    """視窗內每一檔**最長**的一段同向連續，不論是否仍在進行中。

    原本只收「到最後一天仍在進行」的連續，那與工作表不符：
    工作表 2026-09-18 版的連續買超第一名是第一金，連買到 09-14 就中斷了，
    仍然排第一。一段剛結束的長連買本身就是資訊，狀態欄負責說明它還在不在，
    而不是直接把它從榜上刪掉。
    """
    if not window:
        return []
    names = names or {}
    prices = prices or {}
    last_date, last_nets = window[-1]
    dates = [d for d, _ in window]

    # 視窗內出現過的所有代號。只看最後一天會漏掉「連買一段之後就完全
    # 沒有法人動作」的檔 —— 那正是「待確認」要描述的情況。
    codes: set[str] = set()
    for _, nets in window:
        codes.update(nets)

    out: list[Streak] = []
    for code in codes:
        series = [(d, _net(nets.get(code), institution)) for d, nets in window]
        runs = _runs(series)
        if not runs:
            continue
        i, j, total = max(runs, key=lambda r: (r[1] - r[0] + 1, abs(r[2])))
        days = j - i + 1
        if days < min_days:
            continue

        end = dates[j]
        if end == last_date:
            status = "running"
        elif code not in last_nets:
            # 最新一天該檔完全沒有法人交易紀錄 -> 無從確認是否延續
            status = "pending"
        else:
            status = "broken"

        sign = _sign(total)
        out.append(Streak(
            code=code, name=names.get(code, code), institution=institution,
            direction="buy" if sign > 0 else "sell", days=days, shares=total,
            start=dates[i], end=end, truncated=(i == 0), status=status,
            peers=_peers_on(window[j][1], code, sign), price=prices.get(code),
        ))
    return out


# ---------------------------------------------------------------- 排名

def top(window: list[tuple[date, dict[str, list[int]]]], institution: str,
        *, params: ScoreParams | None = None, n: int | None = None,
        names: dict[str, str] | None = None,
        prices: dict[str, float] | None = None) -> dict[str, list[Streak]]:
    """單一法人別的買方／賣方排行，依工作表的分數排序。"""
    p = params or ScoreParams()
    limit = p.top_n if n is None else n
    rows = streaks(window, institution, min_days=p.min_days,
                   names=names, prices=prices)
    return _split(rows, p, limit)


def leading(window: list[tuple[date, dict[str, list[int]]]],
            *, params: ScoreParams | None = None, n: int | None = None,
            names: dict[str, str] | None = None,
            prices: dict[str, float] | None = None,
            institutions: Iterable[str] = ("foreign", "trust", "dealer", "total"),
            ) -> dict[str, list[Streak]]:
    """工作表的主表：每一檔只留分數最高的那一類法人，再跨檔排名。

    工作表的「主導法人」欄就是這個 —— 同一張榜上混著不同法人別：
    第一金是三大法人合計、聯電是投信、兆豐金是外資。
    """
    p = params or ScoreParams()
    limit = p.top_n if n is None else n
    best: dict[tuple[str, str], Streak] = {}
    for inst in institutions:
        for s in streaks(window, inst, min_days=p.min_days,
                         names=names, prices=prices):
            key = (s.code, s.direction)
            cur = best.get(key)
            if cur is None or s.score(p) > cur.score(p):
                best[key] = s
    return _split(list(best.values()), p, limit)


def _split(rows: list[Streak], p: ScoreParams, limit: int) -> dict[str, list[Streak]]:
    out: dict[str, list[Streak]] = {}
    for direction in ("buy", "sell"):
        side = [s for s in rows if s.direction == direction]
        # 分數相同時再比累計量：尾數只能分出天數，分不出規模
        side.sort(key=lambda s: (-s.score(p), -abs(s.shares)))
        out[direction] = side[:limit]
    return out
