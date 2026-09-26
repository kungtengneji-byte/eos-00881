"""外資續買燈號 rubric 的校準測試。

基準是既有工作表 2026-09-18 的實際計算結果（總分 60.20916863）。
工作表本身列出了每個因子的正規化值與加權得分，因此這裡可以逐項比對，
不只是對總分 —— 總分對了但因子錯兩個互相抵銷的情況必須擋掉。
"""

from __future__ import annotations

import pytest

from eos import engine
from eos.rubric import Rubric, RubricError, apply_linear

RUBRIC_PATH = "market_rubric_v1.0.yaml"

# 2026-09-18 的實際輸入（工作表〈外資波段與壓力點〉B26–B38、D53–D60）
SEP18 = {
    "wave_days": 2,
    "max_buy_wave_days": 6,
    "wave_cumulative_100m": 991.79,
    "max_buy_wave_cumulative_100m": 2515,
    "foreign_futures_oi_change_5d": 8957,
    "gap_to_resistance_pct": 0.008424834281,
    "margin_change_2d_100m": 78.7,
    "sox_prev_session_ret": 0.0278,
    "us10y": 5.006,
}

# 工作表列出的加權得分（G54:G60）
EXPECTED_WEIGHTED = {
    "F1": 10.0,
    "F2": 9.084751491,
    "F3": 18.957,
    "F4": 4.21241714,
    "F5": 1.065,
    "F6": 14.45,
    "F7": 2.44,
}
EXPECTED_TOTAL = 60.20916863


@pytest.fixture(scope="module")
def rubric() -> Rubric:
    return Rubric.load(RUBRIC_PATH)


def test_rubric_loads_and_weights_sum_to_100(rubric: Rubric):
    dim = rubric.dimensions["LIGHT"]
    assert dim.max == 100
    assert sum(i.max for i in dim.items) == 100
    assert [i.id for i in dim.items] == ["F1", "F2", "F3", "F4", "F5", "F6", "F7"]


@pytest.mark.parametrize("fid,expected", sorted(EXPECTED_WEIGHTED.items()))
def test_each_factor_matches_workbook(rubric: Rubric, fid, expected):
    """逐項比對。總分對但因子互相抵銷的錯誤必須擋下來。"""
    item = next(i for i in rubric.dimensions["LIGHT"].items if i.id == fid)
    earned, available, _ = item.score(SEP18)
    assert earned == pytest.approx(expected, rel=1e-6), fid
    assert available == item.max


def test_total_matches_workbook(rubric: Rubric):
    """工作表 Dashboard 顯示 60.2；內部值 60.20916863。"""
    res = engine.compute(rubric, SEP18)
    assert res.available == 100
    assert res.raw_eos == pytest.approx(EXPECTED_TOTAL, rel=1e-6)
    assert res.eos == 60


def test_rating_band_matches_workbook(rubric: Rubric):
    """工作表判定：可續買但進入賣壓測試區。"""
    res = engine.compute(rubric, SEP18)
    assert res.rating == "可續買但進入賣壓測試區"
    assert res.tier.status == "confirmed"


@pytest.mark.parametrize("score,label", [
    (100, "續買動能明確"), (65, "續買動能明確"),
    (64, "可續買但進入賣壓測試區"), (45, "可續買但進入賣壓測試區"),
    (44, "賣壓風險升高"), (0, "賣壓風險升高"),
])
def test_rating_band_edges(rubric: Rubric, score, label):
    assert rubric.rating_for(score) == label


# ------------------------------------------------------------ 線性轉換

def test_transforms_are_clamped_to_unit_interval():
    """〈模型與定義〉對 F1 明文要求夾在 0–1；不夾的話單一極端值會讓總分爆表。"""
    assert apply_linear(12, "one_minus_ratio", 6, None) == 0.0      # 本波比最長波還長
    assert apply_linear(-5, "one_minus_ratio", 6, None) == 1.0
    assert apply_linear(0.30, "ratio", 0.03, None) == 1.0           # 距前高 30%
    assert apply_linear(999999, "centered", 10000, None) == 1.0
    assert apply_linear(999999, "centered_inv", 100, None) == 0.0


def test_centered_transforms_are_neutral_at_zero():
    assert apply_linear(0, "centered", 10000, None) == 0.5
    assert apply_linear(0, "centered_inv", 100, None) == 0.5
    assert apply_linear(4.75, "centered_offset_inv", 4.75, 0.5) == 0.5


def test_unknown_transform_is_rejected():
    with pytest.raises(RubricError, match="未知的線性轉換"):
        apply_linear(1, "magic", 1, None)


def test_zero_parameter_is_rejected():
    """參數為 0 會除以零；寧可啟動即失敗，也不要算出 inf。"""
    with pytest.raises(RubricError, match="參數1"):
        apply_linear(1, "ratio", 0, None)


# ------------------------------------------------------------ 缺值

def test_missing_factor_is_excluded_not_zero(rubric: Rubric):
    """期貨資料晚發布不該讓燈號憑空掉 20 分。"""
    inputs = dict(SEP18)
    del inputs["foreign_futures_oi_change_5d"]
    res = engine.compute(rubric, inputs)
    assert res.available == 80
    assert "F3" in res.missing_summary()
    assert res.tier.status == "provisional"
    # 若缺值當 0 分，總分會是 41.25；標準化後仍反映其餘因子的水準
    assert res.eos > 45


def test_missing_wave_parameter_excludes_the_factor(rubric: Rubric):
    """F1 的參數來自波段統計；統計不足時該因子不可計分，而不是除以零。"""
    inputs = dict(SEP18)
    inputs["max_buy_wave_days"] = None
    res = engine.compute(rubric, inputs)
    assert res.dimensions["LIGHT"].items[0].earned is None
    assert res.available == 85


def test_coverage_gate_withholds_when_too_little(rubric: Rubric):
    res = engine.compute(rubric, {"us10y": 5.006, "margin_change_2d_100m": 78.7})
    assert res.available == 20
    assert res.eos is None
    assert res.tier.status == "insufficient"
