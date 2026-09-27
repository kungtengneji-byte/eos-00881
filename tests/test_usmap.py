"""美股映射的測試。

核心是**前視偏誤**：台股 T 日只能用美股 T−1 的時段。
其餘是缺值與休市的處理，以及一致率不可以把持平算成命中。
"""

from __future__ import annotations

from datetime import date

import pytest

from eos import usmap


def _snap(day: str, **fields):
    return {"trade_date": day,
            "fields": {k: {"value": v, "status": "ok", "as_of": a}
                       for k, (v, a) in fields.items()}}


def _us(day: str, us_session: str, sox: float, **kw):
    f = {"sox_ret": (sox, us_session)}
    for k, v in kw.items():
        f[k] = (v, us_session)
    return _snap(day, **f)


def _tw(day: str, taiex_pct: float | None = None, foreign: float | None = None):
    f = {}
    if taiex_pct is not None:
        f["taiex_change_pct"] = (taiex_pct, day)
    if foreign is not None:
        f["market_foreign_net_100m"] = (foreign, day)
    return _snap(day, **f)


# ---------------------------------------------------------------- 映射

def test_us_session_date_comes_from_the_field_as_of():
    """收資料時就是照 T−1 抓的，as_of 就是實際所屬的美股時段日。

    在這裡重推一次日期只會多一個會走歪的地方，而且兩邊一旦不一致，
    表上顯示的時段與實際用來計分的時段就會對不起來。
    """
    r = usmap.row_for(date(2026, 9, 24),
                      _us("2026-09-24", "2026-09-23", -0.0123),
                      _tw("2026-09-24", -0.0028))
    assert r.tw_date == "2026-09-24"
    assert r.us_date == "2026-09-23", "台股 9/24 用的是美股 9/23"


def test_a_us_holiday_shows_up_as_a_shared_session():
    """美股休市時 as_of 落回更早一天，兩個台股日共用同一個美股時段。

    實測 2026-09-07 是美國勞動節：台股 9/7 與 9/8 都對到美股 9/4。
    這是事實不是錯誤，要標出來而不是藏起來。
    """
    days = [date(2026, 9, 7), date(2026, 9, 8)]
    snaps = {
        date(2026, 9, 7): (_us("2026-09-07", "2026-09-04", 0.01), _tw("2026-09-07", 0.01)),
        date(2026, 9, 8): (_us("2026-09-08", "2026-09-04", 0.01), _tw("2026-09-08", -0.01)),
    }
    rep = usmap.build_report(days, snaps.get)
    assert rep["shared_us_sessions"] == ["2026-09-04"]


def test_report_is_oldest_first_and_capped():
    days = [date(2026, 9, d) for d in (18, 21, 22, 23, 24)]
    rep = usmap.build_report(
        days, lambda d: (_us(d.isoformat(), "2026-09-01", 0.01), _tw(d.isoformat(), 0.01)),
        limit=3)
    assert rep["days"] == 3
    assert [r["tw_date"] for r in rep["rows"]] == \
        ["2026-09-22", "2026-09-23", "2026-09-24"]


def test_day_without_any_us_data_is_skipped():
    """整列美股都沒有值 = 還沒收到，不是「美股沒動」。"""
    days = [date(2026, 9, 23), date(2026, 9, 24)]
    def loader(d):
        if d.day == 23:
            return (_snap("2026-09-23"), _tw("2026-09-23", 0.01))
        return (_us("2026-09-24", "2026-09-23", 0.01), _tw("2026-09-24", 0.01))
    rep = usmap.build_report(days, loader)
    assert [r["tw_date"] for r in rep["rows"]] == ["2026-09-24"]


def test_unusable_status_counts_as_missing():
    snap = {"trade_date": "2026-09-24", "fields": {
        "sox_ret": {"value": 0.01, "status": "unavailable", "as_of": "2026-09-23"}}}
    r = usmap.row_for(date(2026, 9, 24), snap, _tw("2026-09-24", 0.01))
    assert r.us["sox_ret"] is None
    assert r.sox_dir is None


# ---------------------------------------------------------------- 方向與一致率

@pytest.mark.parametrize("v,expected", [
    (0.02, "up"), (-0.02, "down"), (0.0, "flat"),
    (0.0005, "flat"), (-0.0005, "flat"),     # 無感帶內
    (0.0011, "up"), (-0.0011, "down"),
])
def test_direction_has_a_flat_band(v, expected):
    """不設無感帶的話 +0.01% 會被當成「漲」，一致率就變成在賭四捨五入。"""
    assert usmap._dir(v) == expected


def test_agree_needs_both_sides_to_have_a_direction():
    up_flat = usmap.row_for(date(2026, 9, 24),
                            _us("2026-09-24", "2026-09-23", 0.02),
                            _tw("2026-09-24", 0.0002))
    assert up_flat.agree is None, "台股持平不算命中也不算落空"

    missing = usmap.row_for(date(2026, 9, 24),
                            _us("2026-09-24", "2026-09-23", 0.02),
                            _tw("2026-09-24"))
    assert missing.agree is None


def test_agree_is_true_only_when_directions_match():
    same = usmap.row_for(date(2026, 9, 24),
                         _us("2026-09-24", "2026-09-23", -0.02),
                         _tw("2026-09-24", -0.01))
    opposite = usmap.row_for(date(2026, 9, 24),
                             _us("2026-09-24", "2026-09-23", 0.02),
                             _tw("2026-09-24", -0.01))
    assert same.agree is True
    assert opposite.agree is False


def test_stats_exclude_flat_days_from_the_ratio():
    rows = [
        usmap.row_for(date(2026, 9, 21), _us("a", "u1", 0.02), _tw("a", 0.01)),   # ✓
        usmap.row_for(date(2026, 9, 22), _us("b", "u2", 0.02), _tw("b", -0.01)),  # ✗
        usmap.row_for(date(2026, 9, 23), _us("c", "u3", 0.02), _tw("c", 0.0001)), # 持平
    ]
    st = usmap.stats(rows)
    assert st["days"] == 3
    assert st["usable"] == 2, "持平那天不計入分母"
    assert st["agree"] == 1
    assert st["agree_ratio"] == pytest.approx(0.5)


def test_stats_are_none_when_nothing_is_usable():
    rows = [usmap.row_for(date(2026, 9, 24), _us("a", "u", 0.0001), _tw("a", 0.0001))]
    st = usmap.stats(rows)
    assert st["usable"] == 0
    assert st["agree_ratio"] is None


def test_averages_are_split_by_the_us_direction():
    rows = [
        usmap.row_for(date(2026, 9, 21), _us("a", "u1", 0.02), _tw("a", 0.02)),
        usmap.row_for(date(2026, 9, 22), _us("b", "u2", 0.02), _tw("b", 0.04)),
        usmap.row_for(date(2026, 9, 23), _us("c", "u3", -0.02), _tw("c", -0.01)),
    ]
    st = usmap.stats(rows)
    assert st["sox_up_days"] == 2
    assert st["taiex_avg_after_sox_up"] == pytest.approx(0.03)
    assert st["taiex_avg_after_sox_down"] == pytest.approx(-0.01)


# ---------------------------------------------------------------- 跨時段

def test_row_session_is_anchored_to_sox_not_field_order():
    """時段以費半為準。取「第一個有值的 as_of」會讓時段隨欄位順序而變。"""
    snap = {"trade_date": "2026-09-08", "fields": {
        "vix": {"value": 15.3, "status": "ok", "as_of": "2026-09-07"},
        "sox_ret": {"value": 0.0337, "status": "stale", "as_of": "2026-09-04"},
    }}
    r = usmap.row_for(date(2026, 9, 8), snap, _tw("2026-09-08", -0.0047))
    assert r.us_date == "2026-09-04"


def test_off_session_fields_are_flagged_not_hidden():
    """實測台股 2026-09-08：費半等順延回 09-04（美股勞動節休市），VIX 卻是 09-07。

    兩者都沒有前視偏誤（都 <= T−1），但並排在同一列而不說明，
    就是在同一格資料裡混了兩個時段。
    """
    snap = {"trade_date": "2026-09-08", "fields": {
        "sox_ret": {"value": 0.0337, "status": "stale", "as_of": "2026-09-04"},
        "ndx_ret": {"value": -0.0029, "status": "stale", "as_of": "2026-09-04"},
        "vix": {"value": 15.3, "status": "ok", "as_of": "2026-09-07"},
    }}
    r = usmap.row_for(date(2026, 9, 8), snap, _tw("2026-09-08", -0.0047))
    assert r.off_session == ["vix"]
    assert r.us_as_of["vix"] == "2026-09-07"
    assert r.us["vix"] == 15.3, "標出來但不刪掉：那個值本身是對的"


def test_no_flag_when_every_field_shares_the_session():
    r = usmap.row_for(date(2026, 9, 24),
                      _us("2026-09-24", "2026-09-23", -0.0123, vix=15.18),
                      _tw("2026-09-24", -0.0028))
    assert r.off_session == []


def test_report_collects_which_fields_ever_went_off_session():
    days = [date(2026, 9, 8)]
    snap = {"trade_date": "2026-09-08", "fields": {
        "sox_ret": {"value": 0.03, "status": "stale", "as_of": "2026-09-04"},
        "vix": {"value": 15.3, "status": "ok", "as_of": "2026-09-07"},
    }}
    rep = usmap.build_report(days, lambda d: (snap, _tw("2026-09-08", 0.01)))
    assert rep["off_session_fields"] == ["vix"]
