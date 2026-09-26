"""外資波段切分與壓力點計算的測試。

基準是既有工作表〈外資波段與壓力點〉分頁的波段表與統計，
由同一批逐日買賣超（〈每日時序〉）重算而得。
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from eos import marketflow as mf

FIXTURE = Path(__file__).parent / "fixtures" / "market_daily_20260807_20260918.json"
SEP18 = date(2026, 9, 18)


@pytest.fixture(scope="module")
def rows() -> list[dict]:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def waves(rows):
    return mf.segment_waves(rows)


# 工作表〈外資波段與壓力點〉A 區的波段表（B1–B6 / S1–S5）
WORKBOOK_WAVES = [
    ("sell", "2026-08-07", "2026-08-07", 1, -407.00),      # 資料起點，工作表未列
    ("buy",  "2026-08-10", "2026-08-17", 6, 2515.00),      # B1
    ("sell", "2026-08-18", "2026-08-19", 2, -536.00),      # S1
    ("buy",  "2026-08-20", "2026-08-21", 2, 356.20),       # B2
    ("sell", "2026-08-24", "2026-08-25", 2, -164.87),      # S2
    ("buy",  "2026-08-26", "2026-08-28", 3, 1275.00),      # B3
    ("sell", "2026-08-31", "2026-08-31", 1, -143.92),      # S3
    ("buy",  "2026-09-01", "2026-09-01", 1, 267.08),       # B4
    ("sell", "2026-09-02", "2026-09-03", 2, -1395.01),     # S4
    ("buy",  "2026-09-04", "2026-09-09", 4, 1787.13),      # B5
    ("sell", "2026-09-10", "2026-09-16", 5, -2452.47),     # S5
    ("buy",  "2026-09-17", "2026-09-18", 2, 991.79),       # B6 進行中
]


def test_wave_count(waves):
    assert len(waves) == len(WORKBOOK_WAVES)


@pytest.mark.parametrize("i,expected", list(enumerate(WORKBOOK_WAVES)))
def test_each_wave_matches_workbook(waves, i, expected):
    direction, start, end, days, cum = expected
    w = waves[i]
    assert w.direction == direction
    assert w.start.isoformat() == start
    assert w.end.isoformat() == end
    assert w.days == days
    assert w.cumulative == pytest.approx(cum, abs=0.01)


def test_leading_wave_is_truncated_and_excluded(waves):
    """資料起點之前發生什麼不可知，第一段的天數與累計都可能被切掉。

    工作表的波段表也是從 B1（8/10）開始，沒有列 8/7 那天。
    """
    first = waves[0]
    assert first.truncated is True
    assert first.usable is False
    assert all(not w.truncated for w in waves[1:])


def test_last_wave_is_in_progress(waves):
    assert waves[-1].completed is False
    assert waves[-1].usable is False          # 未完成者不納入統計


def test_stats_match_workbook(waves):
    """工作表〈外資波段與壓力點〉B 區的波段統計。"""
    s = mf.wave_stats(waves)
    assert s["completed_buy_waves"] == 5
    assert s["avg_buy_wave_days"] == pytest.approx(3.2)
    assert s["max_buy_wave_days"] == 6
    assert s["avg_buy_wave_cumulative_100m"] == pytest.approx(1240.082, abs=0.001)
    assert s["max_buy_wave_cumulative_100m"] == pytest.approx(2515.0)
    assert s["avg_sell_wave_days"] == pytest.approx(2.4)
    assert s["avg_sell_wave_cumulative_100m"] == pytest.approx(-938.454, abs=0.001)


def test_current_wave_matches_workbook(waves):
    s = mf.wave_stats(waves)
    assert s["wave_direction"] == "buy"
    assert s["wave_days"] == 2
    assert s["wave_cumulative_100m"] == pytest.approx(991.79)
    assert s["wave_start"] == "2026-09-17"


# ------------------------------------------------------------ 切分邊界

def test_missing_days_do_not_merge_waves():
    """缺值不可當 0 —— 0 沒有方向，強行歸邊會把兩段黏成一段。"""
    rows = [
        {"date": "2026-01-02", "foreign_net_100m": 100},
        {"date": "2026-01-03", "foreign_net_100m": None},
        {"date": "2026-01-06", "foreign_net_100m": 50},
    ]
    waves = mf.segment_waves(rows)
    assert len(waves) == 1
    assert waves[0].days == 2                 # 缺值日跳過，不計入天數
    assert waves[0].cumulative == 150


def test_flat_day_is_skipped():
    rows = [
        {"date": "2026-01-02", "foreign_net_100m": 100},
        {"date": "2026-01-03", "foreign_net_100m": 0},
        {"date": "2026-01-06", "foreign_net_100m": -50},
    ]
    waves = mf.segment_waves(rows)
    assert [w.direction for w in waves] == ["buy", "sell"]
    assert waves[0].days == 1


def test_single_day_reversal_splits():
    rows = [{"date": f"2026-01-{d:02d}", "foreign_net_100m": v}
            for d, v in ((2, 10), (3, -5), (6, 20))]
    assert [w.days for w in mf.segment_waves(rows)] == [1, 1, 1]


# ------------------------------------------------------------ 滾動視窗

def test_window_limits_to_lookback(rows):
    win = mf.window(rows, SEP18, 10)
    assert len(win) == 10
    assert win[-1]["date"] == "2026-09-18"
    assert win[0]["date"] == "2026-09-07"


def test_window_excludes_future_days(rows):
    win = mf.window(rows, date(2026, 9, 3), 60)
    assert win[-1]["date"] == "2026-09-03"


def test_shorter_window_changes_the_denominator(rows):
    """滾動視窗是刻意的設計：分母會隨視窗長度變動。

    這正是與工作表「一次性期間」的差異，必須是可預期的行為而非意外。
    """
    full = mf.rubric_inputs(rows, SEP18, lookback=60)
    assert full["max_buy_wave_days"] == 6            # 含 8 月的 B1
    assert full["window_days"] == 31                 # 資料僅 31 天，未滿 60

    short = mf.rubric_inputs(rows, SEP18, lookback=10)
    assert short["window_days"] == 10


def test_short_window_can_leave_no_usable_buy_wave(rows):
    """視窗太短時，唯一的買波起點被切在視窗外而標為截斷，統計無可用樣本。

    10 日視窗（09-07 起）內：領頭的買波 09-07~09-09 起點不可知 -> 截斷，
    09-10~09-16 是賣波，09-17~09-18 仍進行中。已完成且完整的買波為零。
    F1/F2 因此無分母可用，rubric 會把這兩個因子排除而非除以零。
    """
    short = mf.rubric_inputs(rows, SEP18, lookback=10)
    assert short["completed_buy_waves"] == 0
    assert short["max_buy_wave_days"] is None
    assert short["max_buy_wave_cumulative_100m"] is None
    assert short["wave_days"] == 2                   # 本波仍算得出來


# ------------------------------------------------------------ 壓力點

def test_resistance_uses_highest_close_not_intraday(rows):
    """工作表用 09-08 盤中高 47,578.24；改用收盤後是 09-07 的 47,326.27。

    收盤是唯一有官方定版、可重現的價格。
    """
    d, v = mf.highest_close(mf.window(rows, SEP18, 60))
    assert d == date(2026, 9, 7)
    assert v == pytest.approx(47326.27)


def test_gap_to_resistance_matches_workbook_pressure_band(rows):
    """工作表 C 區「壓力區下緣 47326.27」列的距離為 0.003084308749。"""
    inp = mf.rubric_inputs(rows, SEP18)
    assert inp["gap_to_resistance_pct"] == pytest.approx(0.003084308749, abs=1e-11)


# ------------------------------------------------------------ N 日差分

def test_futures_oi_5d_change_matches_workbook(rows):
    """工作表 B36：5 日淨OI變化 8,957 口（09-18 −76,110 減 09-11 −85,067）。"""
    inp = mf.rubric_inputs(rows, SEP18)
    assert inp["foreign_futures_oi_change_5d"] == pytest.approx(8957)


def test_margin_2d_change_matches_workbook(rows):
    """工作表 B38：上市融資 2 日增 78.7 億（09-18 減 09-16）。"""
    inp = mf.rubric_inputs(rows, SEP18)
    assert inp["margin_change_2d_100m"] == pytest.approx(78.70, abs=0.01)


def test_lookback_delta_refuses_to_substitute_neighbours(rows):
    """回看的那一端缺值就回 None，不以鄰日頂替。"""
    trimmed = [dict(r) for r in mf.window(rows, SEP18, 6)]
    trimmed[0]["foreign_futures_net_oi"] = None
    assert mf._lookback_delta(trimmed, "foreign_futures_net_oi", 5) is None


def test_empty_input_returns_empty():
    assert mf.rubric_inputs([], SEP18) == {}
