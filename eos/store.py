"""每日快照的讀寫與合併。

一天一個檔：data/daily/{instrument}/{YYYY-MM-DD}.json
四個收集時間窗會先後寫入同一個檔，因此合併規則是這個模組的核心。

**永不降級**：已取得的值不會被後續失敗的抓取覆蓋。
W4 回補窗會重抓所有 missing 欄位；如果那次抓取失敗（來源暫時掛掉、
改版、擋機器人），舊的成功值必須留著，不能被 MISSING 蓋掉。
沒有這條規則，一次網路抖動就會把昨天辛苦收到的資料洗掉。
"""

from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterable

from eos.models import Field, Status

ROOT = Path(__file__).resolve().parent.parent
DAILY = ROOT / "data" / "daily"

# 狀態優劣排序，數字越大越可信
_RANK = {
    Status.OK: 3,
    Status.STALE: 2,
    Status.CONFLICT: 1,
    Status.MISSING: 0,
    Status.UNAVAILABLE: 0,
}


def snapshot_path(instrument: str, day: date) -> Path:
    return DAILY / instrument / f"{day.isoformat()}.json"


def load(instrument: str, day: date) -> dict[str, Any] | None:
    p = snapshot_path(instrument, day)
    if not p.exists():
        return None
    return json.loads(p.read_text(encoding="utf-8"))


def _rank(status_value: str) -> int:
    try:
        return _RANK[Status(status_value)]
    except ValueError:
        return 0


def merge_fields(existing: dict[str, Any], incoming: dict[str, Field]) -> tuple[dict[str, Any], list[str]]:
    """把新抓到的欄位併入既有快照，回傳 (合併後, 實際更新的欄位名)。"""
    merged = dict(existing)
    changed: list[str] = []
    for name, field in incoming.items():
        new = field.to_dict()
        old = merged.get(name)
        if old is None or _rank(new["status"]) >= _rank(old["status"]):
            if old != new:
                changed.append(name)
            merged[name] = new
    return merged, changed


def missing_fields(snapshot: dict[str, Any], expected: Iterable[str]) -> list[str]:
    """尚未取得（或取得後不可用）的欄位，供 W4 回補窗決定要重抓什麼。"""
    fields = snapshot.get("fields", {})
    out = []
    for name in expected:
        f = fields.get(name)
        if f is None or _rank(f["status"]) == 0 or f.get("value") is None:
            out.append(name)
    return sorted(out)


def save(instrument: str, day: date, *, fields: dict[str, Any],
         eos: dict[str, Any] | None, windows: list[str]) -> Path:
    p = snapshot_path(instrument, day)
    p.parent.mkdir(parents=True, exist_ok=True)
    existing = load(instrument, day) or {}
    payload = {
        "instrument": instrument,
        "trade_date": day.isoformat(),
        "first_collected_at": existing.get("first_collected_at") or datetime.now().isoformat(timespec="seconds"),
        "updated_at": datetime.now().isoformat(timespec="seconds"),
        "windows": sorted(set(existing.get("windows", [])) | set(windows)),
        "eos": eos,
        "fields": fields,
    }
    p.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    return p


def recent_days(instrument: str, limit: int) -> list[date]:
    """已存在的快照日期，由新到舊。"""
    d = DAILY / instrument
    if not d.exists():
        return []
    days = []
    for p in d.glob("*.json"):
        try:
            days.append(date.fromisoformat(p.stem))
        except ValueError:
            continue
    return sorted(days, reverse=True)[:limit]


def write_series_index(instrument: str) -> Path:
    """把所有每日快照壓成一份給前端用的精簡時間序列。

    PWA 只需要分數與少數指標，不需要每天的完整來源資訊，
    因此另存一份小檔，避免手機載入上百個 JSON。
    """
    d = DAILY / instrument
    rows = []
    for p in sorted(d.glob("*.json")) if d.exists() else []:
        snap = json.loads(p.read_text(encoding="utf-8"))
        eos = snap.get("eos") or {}
        row = {
            "date": snap["trade_date"],
            "eos": eos.get("eos"),
            "rating": eos.get("rating"),
            "coverage": eos.get("available"),
            "status": eos.get("coverage_status"),
        }
        for k, dim in (eos.get("dimensions") or {}).items():
            row[k] = dim.get("earned")
        for name in ("close", "premium", "rsi14", "volume_ratio"):
            f = (snap.get("fields") or {}).get(name)
            if f:
                row[name] = f.get("value")
        rows.append(row)

    out = ROOT / "data" / f"eos_history_{instrument}.json"
    out.write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
    return out
