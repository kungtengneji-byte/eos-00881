"""產業資金輪動的測試。

重點：金額而不是張數（不同價位的股票不可比）、ETF 不混進產業、
價格同向的算法，以及判讀只寫資料支持的話。
"""

from __future__ import annotations

from datetime import date

import pytest

from eos import sectorflow as sf

# [外陸資, 外資自營商, 投信, 自營商]
IND = {"2330": "24", "2454": "24", "2409": "26", "2882": "17"}
NAMES = {"2330": "台積電", "2454": "聯發科", "2409": "友達",
         "2882": "國泰金", "00940": "元大台灣價值高息"}


def _px(**kw):
    return {c: list(v) for c, v in kw.items()}


def test_amount_uses_price_not_lots():
    """1,000 張台積電（2,475）與 1,000 張友達（18）不是同一回事。"""
    nets = {"2330": [1_000_000, 0, 0, 0], "2409": [1_000_000, 0, 0, 0]}
    out = sf.compute(nets, _px(**{"2330": (2475.0, 0.01), "2409": (18.0, 0.01)}),
                     IND, NAMES)
    by = {s.label: s for s in out}
    assert by["半導體"].amount_100m == pytest.approx(24.75)
    assert by["光電"].amount_100m == pytest.approx(0.18)
    assert by["半導體"].amount_100m > by["光電"].amount_100m * 100


def test_etf_is_kept_out_of_industry_totals():
    """自營商的 ETF 避險部位混進產業合計，會變成造市行為的雜訊。"""
    nets = {"00940": [0, 0, 0, 10_000_000], "2330": [100_000, 0, 0, 0]}
    out = sf.compute(nets, _px(**{"00940": (13.1, 0.0), "2330": (2475.0, 0.01)}),
                     IND, NAMES)
    labels = {s.label for s in out}
    assert sf.ETF_LABEL in labels
    assert "半導體" in labels
    etf = next(s for s in out if s.label == sf.ETF_LABEL)
    assert [t.code for t in etf.top] == ["00940"]


def test_unknown_code_falls_into_its_own_bucket_not_a_real_industry():
    nets = {"9999": [1000, 0, 0, 0]}
    out = sf.compute(nets, _px(**{"9999": (10.0, 0.01)}), IND, NAMES)
    assert [s.label for s in out] == [sf.UNKNOWN_LABEL]


def test_stock_without_a_price_is_skipped_not_counted_as_zero():
    """沒有價格就算不出金額。用張數混充會讓低價股的權重爆掉。"""
    nets = {"2330": [100_000, 0, 0, 0], "2454": [100_000, 0, 0, 0]}
    out = sf.compute(nets, _px(**{"2330": (2475.0, 0.01)}), IND, NAMES)
    assert len(out) == 1
    assert out[0].stocks == 1


def test_confirm_ratio_is_the_share_of_money_moving_with_price():
    """買超 + 上漲才算同向；買超卻下跌代表是被動承接。"""
    nets = {"2330": [100_000, 0, 0, 0], "2454": [100_000, 0, 0, 0]}
    # 台積電金額大且上漲、聯發科金額小且下跌
    out = sf.compute(nets, _px(**{"2330": (2000.0, 0.02), "2454": (1000.0, -0.01)}),
                     IND, NAMES)
    s = out[0]
    assert s.confirm_ratio == pytest.approx(2000 / 3000)


def test_confirm_ratio_ignores_positions_against_the_sector_direction():
    """產業整體買超時，個別賣超的那幾檔不屬於這股資金流。"""
    nets = {"2330": [100_000, 0, 0, 0], "2454": [-1000, 0, 0, 0]}
    out = sf.compute(nets, _px(**{"2330": (2000.0, 0.02), "2454": (1000.0, 0.05)}),
                     IND, NAMES)
    assert out[0].confirm_ratio == pytest.approx(1.0)


def test_confirm_ratio_is_none_without_price_moves():
    nets = {"2330": [100_000, 0, 0, 0]}
    out = sf.compute(nets, {"2330": [2000.0, None]}, IND, NAMES)
    assert out[0].confirm_ratio is None


def test_sectors_are_sorted_inflow_first():
    nets = {"2330": [100_000, 0, 0, 0], "2409": [-100_000, 0, 0, 0]}
    out = sf.compute(nets, _px(**{"2330": (2000.0, 0.01), "2409": (18.0, -0.01)}),
                     IND, NAMES)
    assert out[0].amount_100m > 0 > out[-1].amount_100m


def test_institution_selects_the_right_column():
    nets = {"2330": [10, 0, 500, 0]}
    px = _px(**{"2330": (1000.0, 0.01)})
    assert sf.compute(nets, px, IND, NAMES, institution="foreign")[0].lots == \
        pytest.approx(10 / 1000)
    assert sf.compute(nets, px, IND, NAMES, institution="trust")[0].lots == \
        pytest.approx(0.5)
    assert sf.compute(nets, px, IND, NAMES, institution="total")[0].lots == \
        pytest.approx(0.51)


def test_leading_institution_is_the_largest_by_money():
    nets = {"2330": [100, 0, 100_000, 0]}
    inst, _ = sf.leading_institution(nets, _px(**{"2330": (1000.0, 0.0)}))
    assert inst == "trust"


# ---------------------------------------------------------------- 判讀

def test_read_only_states_what_the_data_supports():
    """工作表的判讀會交叉新聞（「資金躲入金融防禦」那類），平台沒有新聞面。"""
    nets = {"2330": [100_000, 0, 0, 0], "2409": [-100_000, 0, 0, 0]}
    out = sf.compute(nets, _px(**{"2330": (2000.0, 0.02), "2409": (18.0, -0.01)}),
                     IND, NAMES)
    text = sf.read([out[0]], [out[-1]], "foreign")
    assert "半導體" in text and "光電" in text
    assert "外資" in text
    assert "%" in text, "同向比例要寫出來"
    # 不得出現任何推測性的措辭
    for word in ("可能", "預期", "看好", "避險情緒", "資金躲"):
        assert word not in text


def test_read_says_so_when_nothing_reaches_the_threshold():
    assert "無達門檻" in sf.read([], [], "foreign")


def test_day_row_filters_out_noise_sized_sectors():
    """幾千萬的淨額在 1,300 檔的市場裡是雜訊，不該上流入／流出榜。"""
    nets = {"2330": [100_000, 0, 0, 0], "2882": [10, 0, 0, 0]}
    px = _px(**{"2330": (2000.0, 0.01), "2882": (50.0, 0.01)})
    row = sf.day_row(date(2026, 9, 24), nets, px, IND, NAMES, min_amount=1.0)
    labels = [s["label"] for s in row["inflow"]]
    assert "半導體" in labels
    assert "金融保險" not in labels


def test_build_report_is_oldest_first_and_capped():
    days = [date(2026, 9, d) for d in (18, 21, 22, 23, 24)]
    nets = {"2330": [100_000, 0, 0, 0]}
    px = _px(**{"2330": (2000.0, 0.01)})
    rep = sf.build_report(days, lambda d: (nets, px, IND, NAMES), limit=3)
    assert rep["days"] == 3
    assert [r["date"] for r in rep["rows"]] == ["2026-09-22", "2026-09-23", "2026-09-24"]
    assert rep["as_of"] == "2026-09-24"


def test_build_report_skips_days_without_data():
    days = [date(2026, 9, 23), date(2026, 9, 24)]
    nets = {"2330": [100_000, 0, 0, 0]}
    px = _px(**{"2330": (2000.0, 0.01)})
    rep = sf.build_report(
        days, lambda d: None if d.day == 23 else (nets, px, IND, NAMES))
    assert [r["date"] for r in rep["rows"]] == ["2026-09-24"]
