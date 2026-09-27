"""美股映射：把「前一個完整美股時段」對到「下一個台股交易日」。

對齊工作表〈美股映射〉分頁的標題（前一完整美股時段 → 下一個台股交易日）。

## 映射規則

台股 T 日用的是美股 **T−1** 的時段。這不是慣例問題，是**前視偏誤**問題：
美股 T 日的盤是在台股 T 日收盤之後才開的，拿它來解釋台股 T 日的漲跌，
等於用還沒發生的資訊。平台從一開始就照這條規則收資料
（見 sources/yahoo.us_session_cutoff），所以每個欄位的 as_of 就是
它實際所屬的美股時段日 —— 這裡不重新推算，直接讀那個 as_of。

遇到美股休市，as_of 會自動落在更早的一天，表上就會看到同一個美股時段
被兩個台股日共用。那是事實，不是錯誤，所以照實顯示。

## 命中率要怎麼讀

「費半漲 → 台股漲」的命中率是**事後統計**，不是預測力。
樣本只有三十幾天，連一個完整的季都不到，任何看起來漂亮的數字都可能是雜訊。
這裡照算，但一律附上樣本數，而且不做任何統計檢定 —— 檢定在這個樣本數下
只會給出虛假的信心。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any, Iterable

# 美股側要帶出來的欄位：(快照欄位名, 顯示名, 是否為百分比)
US_FIELDS = [
    ("sox_ret", "費半", True),
    ("ndx_ret", "Nasdaq", True),
    ("tsm_ret", "TSM ADR", True),
    ("nvda_ret", "NVDA", True),
    ("vix", "VIX", False),
    ("us10y", "US10Y", False),
]

# 台股側的結果欄位
TW_FIELDS = [
    ("taiex_change_pct", "加權指數", True),
    ("market_foreign_net_100m", "外資買賣超", False),
]

# 這一列的美股時段以費半為準（燈號 F6 的輸入，也是整個映射的主角）
ANCHOR_FIELD = "sox_ret"

# 判定方向時的無感帶：±0.1% 以內視為持平。
# 不設無感帶的話，+0.01% 會被當成「漲」，命中率就變成在賭四捨五入。
FLAT = 0.001


def _value(snap: dict[str, Any] | None, name: str) -> tuple[Any, str | None]:
    """取值與它的 as_of。status 不是 ok/stale 就當缺值。"""
    f = ((snap or {}).get("fields") or {}).get(name)
    if not f or f.get("status") not in ("ok", "stale") or f.get("value") is None:
        return None, None
    return f.get("value"), f.get("as_of")


def _dir(v: float | None, flat: float = FLAT) -> str | None:
    if v is None:
        return None
    if v > flat:
        return "up"
    if v < -flat:
        return "down"
    return "flat"


@dataclass(frozen=True)
class MapRow:
    tw_date: str
    us_date: str | None
    us: dict[str, Any]
    tw: dict[str, Any]
    us_as_of: dict[str, str | None]

    @property
    def off_session(self) -> list[str]:
        """as_of 與這一列的美股時段不同的欄位。

        實測台股 2026-09-08：費半／Nasdaq／TSM／NVDA 的 as_of 都是 09-04
        （美股勞動節休市，順延回前一個交易時段），VIX 卻是 09-07。
        兩者都沒有前視偏誤（都 <= T−1），但把它們並排在同一列而不說明，
        就是在同一格資料裡混了兩個時段。標出來，不要安靜地混。
        """
        return [k for k, v in self.us_as_of.items()
                if v and self.us_date and v != self.us_date]

    @property
    def sox_dir(self) -> str | None:
        return _dir(self.us.get("sox_ret"))

    @property
    def taiex_dir(self) -> str | None:
        return _dir(self.tw.get("taiex_change_pct"))

    @property
    def agree(self) -> bool | None:
        """費半與加權指數同向。任一邊持平或缺值就不算數。"""
        a, b = self.sox_dir, self.taiex_dir
        if a in (None, "flat") or b in (None, "flat"):
            return None
        return a == b

    def to_dict(self) -> dict[str, Any]:
        return {"tw_date": self.tw_date, "us_date": self.us_date,
                "us": self.us, "tw": self.tw, "us_as_of": self.us_as_of,
                "off_session": self.off_session,
                "sox_dir": self.sox_dir, "taiex_dir": self.taiex_dir,
                "agree": self.agree}


def row_for(tw_day: date, us_snap: dict[str, Any] | None,
            tw_snap: dict[str, Any] | None) -> MapRow:
    us: dict[str, Any] = {}
    us_as_of: dict[str, str | None] = {}
    for name, _label, _pct in US_FIELDS:
        us[name], us_as_of[name] = _value(us_snap, name)

    # 這一列的美股時段以費半為準：它是燈號 F6 的輸入，也是整個映射的主角。
    # 取「第一個有值的 as_of」會讓時段隨欄位順序而變，換個順序結果就不同。
    us_date = us_as_of.get(ANCHOR_FIELD)
    if us_date is None:
        us_date = next((v for v in us_as_of.values() if v), None)

    tw: dict[str, Any] = {}
    for name, _label, _pct in TW_FIELDS:
        tw[name], _ = _value(tw_snap, name)

    return MapRow(tw_date=tw_day.isoformat(), us_date=us_date, us=us, tw=tw,
                  us_as_of=us_as_of)


def stats(rows: Iterable[MapRow]) -> dict[str, Any]:
    """費半方向與台股方向的事後一致率。不是預測力，見模組說明。"""
    rows = list(rows)
    usable = [r for r in rows if r.agree is not None]
    agree = sum(1 for r in usable if r.agree)

    def avg(xs: list[float]) -> float | None:
        return sum(xs) / len(xs) if xs else None

    up = [r.tw["taiex_change_pct"] for r in rows
          if r.sox_dir == "up" and r.tw.get("taiex_change_pct") is not None]
    down = [r.tw["taiex_change_pct"] for r in rows
            if r.sox_dir == "down" and r.tw.get("taiex_change_pct") is not None]

    return {
        "days": len(rows),
        "usable": len(usable),
        "agree": agree,
        "agree_ratio": (agree / len(usable)) if usable else None,
        "taiex_avg_after_sox_up": avg(up),
        "taiex_avg_after_sox_down": avg(down),
        "sox_up_days": len(up),
        "sox_down_days": len(down),
        "flat_band": FLAT,
    }


def build_report(days: Iterable[date], loader, *, limit: int = 30) -> dict[str, Any]:
    """loader(day) -> (00881 快照, TWMARKET 快照)。由新到舊取最多 limit 天。"""
    rows: list[MapRow] = []
    for day in sorted(days, reverse=True):
        got = loader(day)
        if not got:
            continue
        us_snap, tw_snap = got
        row = row_for(day, us_snap, tw_snap)
        # 美股側整列都沒有值就不算一天：那是還沒收到，不是「美股沒動」
        if all(v is None for v in row.us.values()):
            continue
        rows.append(row)
        if len(rows) >= limit:
            break
    rows.reverse()

    # 同一個美股時段被兩個台股日共用 = 期間內美股休市過
    seen: dict[str, int] = {}
    for r in rows:
        if r.us_date:
            seen[r.us_date] = seen.get(r.us_date, 0) + 1
    shared = sorted(d for d, n in seen.items() if n > 1)

    off = sorted({k for r in rows for k in r.off_session})

    return {
        "as_of": rows[-1].tw_date if rows else None,
        "days": len(rows),
        "off_session_fields": off,
        "rule": "台股 T 日對應美股 T−1 時段（避免前視偏誤）",
        "shared_us_sessions": shared,
        "fields": {"us": [[n, l, p] for n, l, p in US_FIELDS],
                   "tw": [[n, l, p] for n, l, p in TW_FIELDS]},
        "stats": stats(rows),
        "rows": [r.to_dict() for r in rows],
    }
