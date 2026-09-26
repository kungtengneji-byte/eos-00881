"""序列計算與快照儲存的測試。

序列部分用 repo 內的原始 TWSE 回應重算，斷言鎖在 Phase 1 已驗證過的數值
（那些數值本身是與既有工作表 Raw_History 逐欄比對通過的）。
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from eos import series as series_mod
from eos import store
from eos.models import Field, Status
from sources import twse

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "raw" / "twse" / "00881"


@pytest.fixture(scope="module")
def rows() -> list[dict]:
    bars = []
    for f in sorted(RAW.glob("STOCK_DAY_*.json")):
        bars.extend(twse.parse_stock_day(json.loads(f.read_text(encoding="utf-8"))))
    divs = twse.parse_dividends(
        json.loads((RAW / "EXRIGHT.json").read_text(encoding="utf-8")), "00881")
    return series_mod.build(bars, divs)


def row_for(rows: list[dict], day: str) -> dict:
    return next(r for r in rows if r["date"] == day)


# ------------------------------------------------------------------ 序列

def test_series_spans_full_history(rows):
    assert len(rows) == 1407
    assert rows[0]["date"] == "2020-12-10"      # 00881 上市首日
    assert rows[-1]["date"] == "2026-09-24"


def test_all_dividends_applied(rows):
    paid = {r["date"]: r["dividend"] for r in rows if r["dividend"]}
    assert len(paid) == 11
    assert paid["2026-08-18"] == 4.60
    assert paid["2026-01-20"] == 2.65


def test_indicators_match_validated_values(rows):
    """Phase 1 已與工作表 Raw_History 比對通過的數值，不得漂移。"""
    r = row_for(rows, "2026-09-24")
    assert r["close"] == 52.50
    assert r["ret5"] == pytest.approx(0.0540052198, rel=1e-6)
    assert r["ret20"] == pytest.approx(0.0582543842, rel=1e-6)
    assert r["drawdown20"] == pytest.approx(-0.00190114, rel=1e-5)
    assert r["rv20"] == pytest.approx(0.207835, rel=1e-5)
    assert r["rsi14"] == pytest.approx(65.4442, rel=1e-5)
    assert r["volume_ratio"] == pytest.approx(0.572307, rel=1e-5)


def test_long_moving_averages_available_after_backfill(rows):
    """Phase 1 回填的目的：B1 的四個條件都要算得出來。"""
    r = row_for(rows, "2026-09-24")
    for key in ("ma20", "ma60", "ma120", "ret60", "drawdown60"):
        assert r[key] is not None, key
    assert r["close_adj_gt_ma20"] is True
    assert r["ma20_gt_ma60"] is True
    assert r["close_adj_gt_ma120"] is True
    assert r["ret60_positive"] is True


def test_dividend_adjusted_price_is_used_not_raw_close(rows):
    """除息日當天收盤價大跌，但含息調整價不該跟著跳水。"""
    ex = row_for(rows, "2026-08-18")
    prev = rows[rows.index(ex) - 1]
    assert ex["close"] / prev["close"] - 1 < -0.08        # 未調整：暴跌
    assert ex["ret"] == pytest.approx(-0.0194, abs=1e-3)  # 含息後只小跌


def test_warmup_periods_are_null_not_zero(rows):
    first = rows[0]
    assert first["ret"] is None and first["ma20"] is None and first["rsi14"] is None
    assert rows[5]["ma20"] is None       # 不足 20 根
    assert rows[25]["ma20"] is not None


# ------------------------------------------------------------------ 浮點平手

def test_strictly_greater_treats_near_ties_as_not_greater():
    """2026-03-04 實測：close_adj 與 ma20 僅差 7.1e-15。

    裸 > 比較會讓 B1 的 3 分條件因累加順序而翻面，EOS 差約 3 分。
    """
    assert series_mod.strictly_greater(49.816903252798654, 49.816903252798646) is False
    assert series_mod.strictly_greater(1.0, 0.999) is True
    assert series_mod.strictly_greater(0.999, 1.0) is False
    assert series_mod.strictly_greater(None, 1.0) is None
    assert series_mod.strictly_greater(1.0, None) is None


def test_the_known_tie_day_resolves_deterministically(rows):
    assert row_for(rows, "2026-03-04")["close_adj_gt_ma20"] is False


# ------------------------------------------------------------------ 合併

def ok(name: str, value) -> Field:
    return Field(name=name, value=value, source="s", url="u",
                 as_of="2026-09-24", status=Status.OK)


def test_merge_accepts_new_values(rows):
    merged, changed = store.merge_fields({}, {"nav": ok("nav", 52.7)})
    assert merged["nav"]["value"] == 52.7
    assert changed == ["nav"]


def test_merge_upgrades_missing_to_ok():
    existing = {"nav": Field.missing("nav", note="尚未發布").to_dict()}
    merged, changed = store.merge_fields(existing, {"nav": ok("nav", 52.7)})
    assert merged["nav"]["status"] == "ok"
    assert merged["nav"]["value"] == 52.7
    assert changed == ["nav"]


def test_merge_never_downgrades_a_good_value():
    """回補窗重抓失敗時，昨天成功收到的值必須留著。

    沒有這條規則，一次網路抖動就會把已取得的資料洗掉。
    """
    existing = {"nav": ok("nav", 52.7).to_dict()}
    merged, changed = store.merge_fields(
        existing, {"nav": Field.unavailable("nav", "s", "u", "來源暫時不可用")})
    assert merged["nav"]["status"] == "ok"
    assert merged["nav"]["value"] == 52.7
    assert changed == []


def test_merge_keeps_ok_over_stale():
    existing = {"vix": ok("vix", 15.18).to_dict()}
    stale = Field(name="vix", value=14.0, source="s", url="u",
                  as_of="2026-09-20", status=Status.STALE)
    merged, _ = store.merge_fields(existing, {"vix": stale})
    assert merged["vix"]["value"] == 15.18


def test_merge_accepts_stale_when_nothing_better_exists():
    stale = Field(name="vix", value=14.0, source="s", url="u",
                  as_of="2026-09-20", status=Status.STALE)
    merged, changed = store.merge_fields({}, {"vix": stale})
    assert merged["vix"]["status"] == "stale"
    assert changed == ["vix"]


# ------------------------------------------------------------------ 缺值盤點

def test_missing_fields_reports_unusable_entries():
    snap = {"fields": {
        "nav": ok("nav", 52.7).to_dict(),
        "premium": Field.missing("premium").to_dict(),
        "vix": Field.unavailable("vix", "s", "u", "擋機器人").to_dict(),
    }}
    out = store.missing_fields(snap, ["nav", "premium", "vix", "rsi14"])
    assert out == ["premium", "rsi14", "vix"]      # nav 已取得，rsi14 從未出現


def test_missing_fields_treats_null_value_as_missing():
    snap = {"fields": {"wcr": Field(name="wcr", value=None, source="s", url="u",
                                    status=Status.OK).to_dict()}}
    assert store.missing_fields(snap, ["wcr"]) == ["wcr"]


# ------------------------------------------------------------------ 實際快照

def test_collected_snapshot_has_no_lookahead():
    """海外欄位必須是台股日 T-1 的美股時段，不能是 T 日。"""
    p = ROOT / "data" / "daily" / "00881" / "2026-09-24.json"
    if not p.exists():
        pytest.skip("尚未產生 2026-09-24 快照")
    fields = json.loads(p.read_text(encoding="utf-8"))["fields"]
    for name in ("sox_ret", "ndx_ret", "tsm_ret", "nvda_ret", "vix", "us10y", "twd_change"):
        f = fields.get(name)
        if f and f["value"] is not None:
            assert f["as_of"] == "2026-09-23", f"{name} 取到了台股收盤後才發生的資料"


def test_collected_snapshot_domestic_fields_are_same_day():
    p = ROOT / "data" / "daily" / "00881" / "2026-09-24.json"
    if not p.exists():
        pytest.skip("尚未產生 2026-09-24 快照")
    fields = json.loads(p.read_text(encoding="utf-8"))["fields"]
    for name in ("close", "premium", "wcr", "breadth_count", "institutional_net"):
        f = fields.get(name)
        if f and f["value"] is not None:
            assert f["as_of"] == "2026-09-24", name


# ---------------------------------------------------------------- 日期查詢

def _fake_snapshots(tmp_path, monkeypatch, days):
    d = tmp_path / "daily" / "X"
    d.mkdir(parents=True)
    for day in days:
        (d / f"{day}.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(store, "DAILY", tmp_path / "daily")


DAYS = ["2026-09-01", "2026-09-02", "2026-09-03", "2026-09-21",
        "2026-09-22", "2026-09-23", "2026-09-24"]


def test_recent_days_is_newest_first(tmp_path, monkeypatch):
    _fake_snapshots(tmp_path, monkeypatch, DAYS)
    assert store.recent_days("X", 3) == [date(2026, 9, 24), date(2026, 9, 23),
                                         date(2026, 9, 22)]


def test_days_before_looks_back_from_the_target_not_from_today(tmp_path, monkeypatch):
    """重算 9/02 時要看到 9/01。

    用 recent_days(10) 再篩掉 >= 目標日的做法在歷史一長就失效：
    最新的 N 天全在目標日之後，篩完一天不剩，結論會誤報
    「前一交易日無已發布分數」而不是與前一日比較。
    """
    _fake_snapshots(tmp_path, monkeypatch, DAYS)
    assert store.days_before("X", date(2026, 9, 2), 10) == [date(2026, 9, 1)]


def test_days_before_excludes_the_day_itself(tmp_path, monkeypatch):
    _fake_snapshots(tmp_path, monkeypatch, DAYS)
    got = store.days_before("X", date(2026, 9, 22), 10)
    assert date(2026, 9, 22) not in got
    assert got[0] == date(2026, 9, 21)


def test_days_before_returns_empty_for_the_earliest_day(tmp_path, monkeypatch):
    _fake_snapshots(tmp_path, monkeypatch, DAYS)
    assert store.days_before("X", date(2026, 9, 1), 10) == []
