"""大盤資金流向的資料源測試。

斷言鎖定 2026-09-18 的實際值，並且刻意與既有工作表
「台股資金流向_期間彙整分析」的數字對照 —— 那份是人工整理的，
兩邊對上才代表 adapter 取到的是同一個東西。
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from eos.models import Status
from sources import taifex, twse

FIXTURES = Path(__file__).parent / "fixtures"
SEP18 = date(2026, 9, 18)


def load_json(name: str):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


# ------------------------------------------------------------ 加權指數

@pytest.fixture(scope="module")
def index_history():
    return twse.parse_market_index(load_json("twse_fmtqik_202609.json"))


def test_index_matches_workbook(index_history):
    """工作表：加權指數 09-18 收 47,180.75，+892.75 點 / +1.93%。"""
    rec = index_history[SEP18]
    assert rec["index"] == 47180.75
    assert rec["change"] == 892.75


def test_index_change_pct_is_derived_not_assumed(index_history):
    """TWSE 不提供漲跌幅欄位，必須由指數與漲跌點數回推。"""
    f = twse.market_index_fields(index_history, SEP18, url="x")
    assert f["taiex_change_pct"].value == pytest.approx(0.0193, abs=5e-5)
    assert f["taiex"].value == 47180.75
    assert f["taiex"].status is Status.OK


def test_index_turnover_converted_to_100m(index_history):
    f = twse.market_index_fields(index_history, SEP18, url="x")
    # 1,142,321,910,407 元 = 11,423.22 億元
    assert f["market_turnover_100m"].value == pytest.approx(11423.22, abs=0.01)


def test_index_missing_day_is_missing_not_zero(index_history):
    f = twse.market_index_fields(index_history, date(2026, 9, 19), url="x")   # 週六
    assert f["taiex"].status is Status.MISSING
    assert f["taiex"].value is None


# ------------------------------------------------------------ 融資餘額

def test_margin_matches_workbook():
    """工作表：上市融資餘額 5,940.39 億。"""
    f = twse.parse_margin(load_json("twse_mi_margn_20260918.json"), SEP18)
    assert f["margin_balance_100m"].value == pytest.approx(5940.39, abs=0.01)
    assert f["margin_balance_100m"].status is Status.OK


def test_margin_daily_change_is_computed():
    f = twse.parse_margin(load_json("twse_mi_margn_20260918.json"), SEP18)
    # 594,039,480 - 589,397,267 仟元 = 46.42 億
    assert f["margin_change_100m"].value == pytest.approx(46.42, abs=0.01)


def test_margin_ignores_unit_rows_and_empty_tables():
    """只能取「融資金額」列；「融資(交易單位)」是張數，取錯會差好幾個數量級。

    而且 TWSE 回的 tables 第二個是空殼（fields 為 null），直接索引會炸。
    """
    payload = load_json("twse_mi_margn_20260918.json")
    assert any(t.get("fields") is None for t in payload["tables"]), "fixture 應含空殼表"
    f = twse.parse_margin(payload, SEP18)
    assert f["margin_balance_100m"].value < 10000        # 億元量級，不是張數


def test_margin_missing_payload():
    f = twse.parse_margin({"stat": "OK", "tables": []}, SEP18)
    assert f["margin_balance_100m"].status is Status.MISSING
    assert f["margin_change_100m"].status is Status.MISSING


# ------------------------------------------------------------ 期交所

@pytest.fixture(scope="module")
def taifex_csv() -> str:
    return (FIXTURES / "taifex_futcontracts_20260918.csv").read_bytes().decode("big5")


def test_taifex_fixture_is_big5_not_utf8():
    """期交所回的是 Big5。用 utf-8 解會整片亂碼，這是實際踩過的坑。"""
    raw = (FIXTURES / "taifex_futcontracts_20260918.csv").read_bytes()
    assert "臺股期貨" in raw.decode("big5")
    assert "臺股期貨" not in raw.decode("utf-8", errors="replace")


def test_foreign_futures_net_oi_matches_workbook(taifex_csv):
    """工作表：外資台指期淨未平倉 (76,110) 口。"""
    parsed = taifex.parse_futures_oi(taifex_csv, SEP18)
    assert parsed["foreign"]["net_oi"] == -76110


def test_all_three_identities_parsed(taifex_csv):
    parsed = taifex.parse_futures_oi(taifex_csv, SEP18)
    assert set(parsed) == {"dealer", "trust", "foreign"}
    assert parsed["dealer"]["net_oi"] == -3192
    assert parsed["trust"]["net_oi"] == 75110


def test_net_oi_equals_long_minus_short(taifex_csv):
    """淨額欄位與多空口數必須自洽，否則代表欄位位置抓錯。"""
    parsed = taifex.parse_futures_oi(taifex_csv, SEP18)
    for key, rec in parsed.items():
        assert rec["net_oi"] == rec["long_oi"] - rec["short_oi"], key


def test_taifex_fields_carry_source_and_date(taifex_csv):
    parsed = taifex.parse_futures_oi(taifex_csv, SEP18)
    f = taifex.oi_fields(parsed, SEP18, url="x")
    assert f["foreign_futures_net_oi"].value == -76110
    assert f["foreign_futures_net_oi"].as_of == "2026-09-18"
    assert "淨空" in f["foreign_futures_net_oi"].note


def test_taifex_other_day_returns_empty(taifex_csv):
    """CSV 只含 9/18；查別天應為空而不是誤取。"""
    assert taifex.parse_futures_oi(taifex_csv, date(2026, 9, 17)) == {}


def test_taifex_missing_becomes_missing_field():
    f = taifex.oi_fields({}, SEP18, url="x")
    assert f["foreign_futures_net_oi"].status is Status.MISSING
    assert f["foreign_futures_net_oi"].value is None


# ---------------------------------------------------------------- 大盤開高低

@pytest.fixture(scope="module")
def ohlc_history():
    url = twse.taiex_ohlc_url("202609")
    payload = json.loads((FIXTURES / "twse_taiex_ohlc_202609.json").read_text(encoding="utf-8"))
    return twse.parse_taiex_ohlc(payload, url=url)


def test_ohlc_matches_workbook_resistance_and_support(ohlc_history):
    """工作表〈外資波段與壓力點〉那幾個點位就是這幾根 K 的高低收。"""
    assert ohlc_history[date(2026, 9, 8)]["high"] == pytest.approx(47578.24)   # 壓力2
    assert ohlc_history[date(2026, 9, 9)]["high"] == pytest.approx(47548.26)   # 壓力1
    assert ohlc_history[date(2026, 9, 17)]["high"] == pytest.approx(46874.84)  # 工作表支撐1
    assert ohlc_history[date(2026, 9, 17)]["close"] == pytest.approx(46288.00)  # 支撐2
    assert ohlc_history[date(2026, 9, 15)]["close"] == pytest.approx(45511.49)  # 支撐3


def test_ohlc_close_agrees_with_fmtqik(ohlc_history, index_history):
    """兩支端點的收盤必須一致，否則不能混用。

    實測 2026-08/09 共 31 個交易日完全相同。任何一天對不起來都代表
    其中一支改了口徑（例如含不含盤後定價），那時混用會算出錯誤的壓力點。
    """
    shared = set(ohlc_history) & set(index_history)
    assert len(shared) >= 18
    for d in shared:
        assert ohlc_history[d]["close"] == pytest.approx(index_history[d]["index"]), d


def test_ohlc_high_is_never_below_close(ohlc_history):
    """盤中高 >= 收盤是恆等關係 —— 這也是舊版 F4 系統性偏低的原因。"""
    for d, rec in ohlc_history.items():
        assert rec["high"] >= rec["close"], d
        assert rec["low"] <= rec["close"], d


def test_ohlc_fields_are_missing_not_zero_on_a_non_trading_day(ohlc_history):
    url = twse.taiex_ohlc_url("202609")
    fields = twse.taiex_ohlc_fields(ohlc_history, date(2026, 9, 26), url=url)
    assert set(fields) == {"taiex_open", "taiex_high", "taiex_low"}
    for f in fields.values():
        assert f.value is None
        assert f.status is Status.MISSING
