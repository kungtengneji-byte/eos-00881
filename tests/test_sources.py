"""以真實 API 回應（tests/fixtures/）驗證所有 adapter。

斷言刻意寫死 2026-09-25 實際抓到的數值。這不是過度指定 —— 這些測試的
用途就是在來源改版時立刻失敗，而不是讓平台安靜地記錄錯誤資料。
來源真的改版時，要做的是重抓 fixture、確認差異合理、再更新斷言。
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from eos.models import Status
from sources import cathay, twse, yahoo
from sources.base import _looks_like_challenge, roc_to_date, to_float

FIXTURES = Path(__file__).parent / "fixtures"


def load(name: str):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


# ------------------------------------------------------------------ base

@pytest.mark.parametrize("raw,expected", [
    ("1,234.5", 1234.5), ("+1.47", 1.47), ("-0.38%", -0.38),
    ("--", None), ("X0.00", None), ("", None), (None, None),
    ("X1.20", 1.20),          # TWSE 用 X 前綴標記除權息當日價差
])
def test_to_float(raw, expected):
    assert to_float(raw) == expected


@pytest.mark.parametrize("raw", ["115/09/01", "115年09月01日"])
def test_roc_to_date(raw):
    assert roc_to_date(raw) == date(2026, 9, 1)


def test_bot_challenge_detection():
    """Stooq 與台銀實際回傳的驗證頁特徵，必須被識別出來。"""
    stooq = ('<!DOCTYPE html><html><head></head><body><noscript>This site requires '
             'JavaScript to verify your browser.</noscript><script>'
             'crypto.subtle.digest("SHA-256", ...)</script></body></html>')
    imperva = ('<!DOCTYPE html><html lang="en"><head><title>Challenge Validation</title>'
               '</head><body><iframe challenge="..."></iframe></body></html>')
    assert _looks_like_challenge(stooq)
    assert _looks_like_challenge(imperva)
    assert not _looks_like_challenge('{"stat":"OK","data":[]}')


# ------------------------------------------------------------------ TWSE 日線

def test_parse_stock_day():
    bars = twse.parse_stock_day(load("twse_stock_day_00881_202609.json"))
    assert len(bars) == 18
    assert bars[0].date == date(2026, 9, 1)
    assert bars[-1].date == date(2026, 9, 24)
    assert [b.date for b in bars] == sorted(b.date for b in bars)

    last = bars[-1]
    assert last.close == 52.50
    assert last.open == 52.40
    assert last.high == 52.60
    assert last.low == 52.25
    assert last.volume_shares == 10_441_856
    assert last.turnover == 547_101_772
    assert last.volume_lots == pytest.approx(10_441.856)


def test_stock_day_volume_is_twse_not_yahoo():
    """口徑固定在 TWSE：9/9 官方 9,972 張，Yahoo 當時顯示 9,755 張。

    既有工作表混用了兩者，導致量比出現 2.2% 的偏差。時間序列不可比是
    比數字略有差異更嚴重的問題，因此這裡鎖死官方值。
    """
    bars = twse.parse_stock_day(load("twse_stock_day_00881_202609.json"))
    sep9 = next(b for b in bars if b.date == date(2026, 9, 9))
    assert sep9.volume_shares == 9_972_222
    assert sep9.volume_lots == pytest.approx(9_972.222)


def test_parse_stock_day_handles_non_ok_stat():
    """上市前的月份 TWSE 會回非 OK，應視為無資料而不是錯誤。"""
    assert twse.parse_stock_day({"stat": "很抱歉，沒有符合條件的資料!"}) == []


# ------------------------------------------------------------------ TWSE 配息

def test_parse_dividends_filters_to_instrument():
    """TWT49U 的 stockNo 參數實測無效，必須在 adapter 內過濾。"""
    divs = twse.parse_dividends(load("twse_twt49u_2026.json"), "00881")
    assert divs == {date(2026, 1, 20): 2.65, date(2026, 8, 18): 4.60}


def test_parse_dividends_ignores_other_instruments():
    divs = twse.parse_dividends(load("twse_twt49u_2026.json"), "00881")
    assert all(d.year == 2026 for d in divs)
    assert len(divs) == 2      # 全市場 1,300+ 筆裡只有這兩筆屬於 00881


# ------------------------------------------------------------------ TWSE 法人

def test_parse_institutional():
    day = date(2026, 9, 24)
    fields = twse.parse_institutional(load("twse_bfi82u_20260924.json"), day)
    foreign = fields["foreign_net_100m"]
    total = fields["institutional_net_100m"]
    assert foreign.status is Status.OK
    assert foreign.value == pytest.approx(-329.65)      # 外資及陸資(不含外資自營商)
    assert total.value == pytest.approx(-444.49)        # 合計
    assert foreign.as_of == "2026-09-24"


def test_parse_stock_institutional():
    day = date(2026, 9, 24)
    fields = twse.parse_stock_institutional(load("twse_t86_20260924.json"), "00881", day)
    assert fields["stock_foreign_net_lots"].value == pytest.approx(-401.585)
    assert fields["stock_institutional_net_lots"].value == pytest.approx(644.061)


def test_stock_institutional_missing_instrument():
    day = date(2026, 9, 24)
    fields = twse.parse_stock_institutional(load("twse_t86_20260924.json"), "99999", day)
    assert fields["stock_foreign_net_lots"].status is Status.MISSING
    assert fields["stock_foreign_net_lots"].value is None


# ------------------------------------------------------------------ 國泰 NAV

def test_parse_nav_history():
    hist = cathay.parse_nav_history(load("cathay_nav30_CR.json"))
    assert len(hist) == 30
    assert min(hist) == date(2026, 8, 14)
    assert max(hist) == date(2026, 9, 24)

    sep24 = hist[date(2026, 9, 24)]
    assert sep24["close"] == 52.50
    assert sep24["nav"] == 52.70
    assert sep24["premium"] == pytest.approx(-0.0038)


def test_nav_history_covers_anchor_days():
    """校準用的 8 個錨點日必須全部涵蓋 —— 這是 A 構面能回填的依據。"""
    hist = cathay.parse_nav_history(load("cathay_nav30_CR.json"))
    anchors = [date(2026, 9, d) for d in (1, 2, 3, 9, 21, 22, 23, 24)]
    assert all(a in hist for a in anchors)
    assert hist[date(2026, 9, 3)]["premium"] == pytest.approx(0.0044)
    assert hist[date(2026, 9, 23)]["premium"] == pytest.approx(-0.0045)


def test_nav_fields_missing_day_does_not_borrow_neighbour():
    """NAV 未發布時必須是 MISSING，絕不以鄰近日期代算折溢價。"""
    hist = cathay.parse_nav_history(load("cathay_nav30_CR.json"))
    fields = cathay.nav_fields(hist, date(2026, 9, 25), url="x")
    assert fields["nav"].status is Status.MISSING
    assert fields["premium"].status is Status.MISSING
    assert fields["premium"].value is None
    assert "不以前一日 NAV 代算" in fields["premium"].note


def test_unwrap_returns_none_on_no_data():
    assert cathay._unwrap({"success": False, "returnMessage": "查無資料"}, url="x") is None


def test_fund_code_mapping():
    assert cathay.fund_code_for("00881") == "CR"
    with pytest.raises(Exception):
        cathay.fund_code_for("0050")


# ------------------------------------------------------------------ 國泰 成分股

def test_parse_weights():
    as_of, holdings = cathay.parse_weights(load("cathay_weights_CR.json"))
    assert as_of == date(2026, 9, 24)
    assert len(holdings) == 30
    assert holdings[0].code == "2330"
    assert holdings[0].name == "台積電"
    assert holdings[0].weight == pytest.approx(0.3797)
    # 已依權重降序
    assert [h.weight for h in holdings] == sorted((h.weight for h in holdings), reverse=True)


def test_top10_weights():
    _, holdings = cathay.parse_weights(load("cathay_weights_CR.json"))
    top10 = cathay.top_n(holdings, 10)
    assert [h.code for h in top10] == [
        "2330", "2454", "2308", "2317", "2303", "3037", "2059", "2383", "3017", "3008"
    ]
    assert sum(h.weight for h in top10) == pytest.approx(0.7722, abs=1e-4)


# ------------------------------------------------------------------ Yahoo

def test_parse_chart_skips_nulls_but_keeps_completed_last_bar():
    """^SOX 的 regular 時段指向下一場（9/25），9/24 已收盤，不可誤刪。"""
    series = yahoo.parse_chart(load("yahoo_chart_SOX.json"))
    dates = [d for d, _ in series]
    assert date(2026, 9, 22) not in dates          # close 為 null
    assert dates[-1] == date(2026, 9, 24)          # 已完成，必須保留
    assert len(series) == 9
    assert dict(series)[date(2026, 9, 24)] == pytest.approx(12492.54)


def test_parse_chart_drops_in_progress_session():
    """TWD=X 抓取當下正在盤中（9/25），該時段的兩筆都要剔除。"""
    series = yahoo.parse_chart(load("yahoo_chart_TWDX.json"))
    dates = [d for d, _ in series]
    assert date(2026, 9, 25) not in dates
    assert dates[-1] == date(2026, 9, 24)
    assert dict(series)[date(2026, 9, 24)] == pytest.approx(31.7895)


def test_us_session_cutoff_avoids_lookahead():
    """台股 T 日只能用美股 T-1 的時段，否則會用到台股收盤後才發生的資訊。"""
    assert yahoo.us_session_cutoff(date(2026, 9, 24)) == date(2026, 9, 23)


def test_change_uses_cutoff_not_latest():
    series = yahoo.parse_chart(load("yahoo_chart_SOX.json"))
    picked = yahoo.change_at(series, date(2026, 9, 23))
    assert picked is not None
    d, pct = picked
    assert d == date(2026, 9, 23)
    # 9/22 為 null，因此前一個可用時段是 9/21
    assert pct == pytest.approx(12534.2803 / 12433.1699 - 1, rel=1e-6)


def test_to_field_marks_stale_when_us_session_lags():
    series = yahoo.parse_chart(load("yahoo_chart_SOX.json"))
    # 目標日 9/24 -> cutoff 9/23，資料剛好是 9/23
    fresh = yahoo.to_field("sox_ret", "^SOX", series, as_change=True,
                           target_day=date(2026, 9, 24), url="x")
    assert fresh.status is Status.OK
    assert fresh.as_of == "2026-09-23"

    # 目標日 9/29 -> cutoff 9/28，但資料只到 9/24
    stale = yahoo.to_field("sox_ret", "^SOX", series, as_change=True,
                           target_day=date(2026, 9, 29), url="x")
    assert stale.status is Status.STALE
    assert stale.as_of == "2026-09-24"
    assert "休市或缺值" in stale.note


def test_level_field_for_vix_style_series():
    series = yahoo.parse_chart(load("yahoo_chart_TWDX.json"))
    f = yahoo.to_field("usdtwd", "TWD=X", series, as_change=False,
                       target_day=date(2026, 9, 24), url="x")
    assert f.status is Status.OK
    assert f.as_of == "2026-09-23"
    assert f.value == pytest.approx(31.6795)


def test_all_eos_symbols_are_mapped():
    expected = set(yahoo.CHANGE_FIELDS) | set(yahoo.LEVEL_FIELDS)
    assert expected == set(yahoo.SYMBOLS)
