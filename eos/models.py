"""資料模型：每個欄位都帶來源、時間戳與狀態。

設計原則直接沿用既有工作表 Sources 分頁的紀律：
每個數字都必須說得出「哪裡來的、什麼時候的、可不可信」。
缺值是一等公民（status=MISSING），絕不以 0 或推估值冒充。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from enum import Enum
from typing import Any


class Status(str, Enum):
    OK = "ok"
    MISSING = "missing"        # 來源沒有這筆資料（例：當日 NAV 尚未發布）
    STALE = "stale"            # 取到了，但不是目標日期的資料（例：美股休市沿用前一時段）
    CONFLICT = "conflict"      # 多來源互相矛盾，需人工裁決
    UNAVAILABLE = "unavailable"  # 來源本身不可用（連線失敗、改版、擋機器人）


@dataclass(frozen=True)
class Field:
    """一個帶完整溯源資訊的數值。"""

    name: str
    value: Any = None
    source: str = ""
    url: str = ""
    as_of: str | None = None      # 資料本身的日期/時間，不是抓取時間
    status: Status = Status.MISSING
    note: str = ""

    @property
    def ok(self) -> bool:
        return self.status is Status.OK and self.value is not None

    @property
    def usable(self) -> bool:
        """可用於計分：STALE 仍可計分但要在 UI 標示。"""
        return self.status in (Status.OK, Status.STALE) and self.value is not None

    @classmethod
    def missing(cls, name: str, source: str = "", url: str = "", note: str = "") -> "Field":
        return cls(name=name, source=source, url=url, status=Status.MISSING, note=note)

    @classmethod
    def unavailable(cls, name: str, source: str, url: str, note: str) -> "Field":
        return cls(name=name, source=source, url=url, status=Status.UNAVAILABLE, note=note)

    def to_dict(self) -> dict[str, Any]:
        return {
            "value": self.value,
            "source": self.source,
            "url": self.url,
            "as_of": self.as_of,
            "status": self.status.value,
            "note": self.note,
        }


@dataclass
class Snapshot:
    """某一交易日的完整資料快照。"""

    instrument: str               # "00881"
    trade_date: date
    collected_at: datetime
    fields: dict[str, Field] = field(default_factory=dict)

    def put(self, f: Field) -> None:
        self.fields[f.name] = f

    def get(self, name: str) -> Field:
        return self.fields.get(name, Field.missing(name))

    def value(self, name: str) -> Any:
        f = self.fields.get(name)
        return f.value if f is not None and f.usable else None

    def to_dict(self) -> dict[str, Any]:
        return {
            "instrument": self.instrument,
            "trade_date": self.trade_date.isoformat(),
            "collected_at": self.collected_at.isoformat(),
            "fields": {k: v.to_dict() for k, v in sorted(self.fields.items())},
        }

    def coverage_report(self) -> dict[str, list[str]]:
        out: dict[str, list[str]] = {}
        for name, f in sorted(self.fields.items()):
            out.setdefault(f.status.value, []).append(name)
        return out


@dataclass(frozen=True)
class Bar:
    """單日 OHLCV。成交量單位一律為『股』，換算成『張』由呼叫端負責。"""

    date: date
    open: float | None
    high: float | None
    low: float | None
    close: float | None
    volume_shares: float | None
    turnover: float | None = None
    trades: float | None = None

    @property
    def volume_lots(self) -> float | None:
        return None if self.volume_shares is None else self.volume_shares / 1000.0


@dataclass(frozen=True)
class Holding:
    code: str
    name: str
    weight: float      # 小數，非百分比：37.97% -> 0.3797
