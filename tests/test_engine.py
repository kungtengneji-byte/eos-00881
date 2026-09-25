"""rubric 載入與 EOS 計分引擎的測試。

核心是錨點日回歸：8 個有人工判讀可對照的交易日。
改動 eos_rubric_v1.1.yaml 的任何門檻，這裡就會失敗 —— 這是刻意的。
rubric 是模型的規格，不該被無聲修改。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from eos import engine
from eos.rubric import Band, Rubric, RubricError, pick_band

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(scope="module")
def rubric() -> Rubric:
    return Rubric.load()


@pytest.fixture(scope="module")
def anchors() -> list[dict]:
    return json.loads((FIXTURES / "anchors.json").read_text(encoding="utf-8"))


def by_date(anchors: list[dict], day: str) -> dict:
    return next(a for a in anchors if a["date"] == day)


# ------------------------------------------------------------------ rubric

def test_rubric_loads(rubric: Rubric):
    assert rubric.version == "1.1-draft"
    assert set(rubric.dimensions) == set("ABCDEF")
    assert rubric.scale == 100


def test_dimension_weights_match_the_model_document(rubric: Rubric):
    """EOS = 0.15A + 0.25B + 0.20C + 0.15D + 0.15E + 0.10F（v1.0 文件第 1 節）。"""
    assert {k: d.max for k, d in rubric.dimensions.items()} == {
        "A": 15, "B": 25, "C": 20, "D": 15, "E": 15, "F": 10
    }
    assert sum(d.max for d in rubric.dimensions.values()) == 100


def test_loader_rejects_inconsistent_totals(tmp_path: Path):
    """子項滿分與構面滿分不符時必須啟動即失敗，不可算出安靜錯誤的分數。"""
    bad = tmp_path / "bad.yaml"
    bad.write_text(
        "version: x\nscale: 100\n"
        "coverage_gate: {tiers: [{min_available: 0, status: s, publish_eos: true}]}\n"
        "rating_bands: []\n"
        "dimensions:\n"
        "  A:\n    name: a\n    max: 100\n    items:\n"
        "      - {id: A1, name: a1, max: 40, input: x, bands: [{lt: null, score: 1}]}\n",
        encoding="utf-8")
    with pytest.raises(RubricError, match="滿分合計"):
        Rubric.load(bad)


def test_required_inputs_covers_every_dimension(rubric: Rubric):
    names = rubric.required_inputs()
    for expected in ("premium", "rsi14", "wcr", "sox_ret", "vix",
                     "volume_ratio", "day_direction", "close_adj_gt_ma120"):
        assert expected in names


# ------------------------------------------------------------------ 查表

def test_pick_band_upper_bound_is_exclusive():
    bands = [Band(lt=10, score=1), Band(lt=20, score=2), Band(lt=None, score=3)]
    assert pick_band(9.99, bands) == 1
    assert pick_band(10.0, bands) == 2      # 邊界值落到下一段
    assert pick_band(19.99, bands) == 2
    assert pick_band(20.0, bands) == 3
    assert pick_band(1e9, bands) == 3


def test_premium_bands_match_the_document(rubric: Rubric):
    """v1.0 文件 3.1 的折溢價查表，是唯一有明文門檻的構面，不可偏離。"""
    a1 = rubric.dimensions["A"].items[0]
    cases = {-0.01: 15, -0.005: 13, -0.003: 13, -0.001: 10,
             0.0: 10, 0.003: 6, 0.005: 6, 0.008: 2, 0.02: 2}
    for premium, expected in cases.items():
        earned, available, _ = a1.score({"premium": premium})
        assert earned == expected, f"premium={premium}"
        assert available == 15


# ------------------------------------------------------------------ 缺值

def test_missing_input_excludes_item_instead_of_scoring_zero(rubric: Rubric):
    """缺值不得當 0 分 —— 否則 NAV 晚發布會看起來像市況惡化。"""
    res = engine.compute(rubric, {"premium": None})
    assert res.dimensions["A"].earned is None
    assert res.dimensions["A"].available == 0
    assert "A1" in res.missing_summary()


def test_normalisation_does_not_penalise_late_nav(rubric: Rubric, anchors: list[dict]):
    """9/24 拿掉 NAV：覆蓋率降到 85、轉為暫定，但分數不會崩掉。"""
    inputs = dict(by_date(anchors, "2026-09-24")["inputs"])
    full = engine.compute(rubric, inputs)
    inputs["premium"] = None
    partial = engine.compute(rubric, inputs)

    assert full.available == 100 and full.tier.status == "confirmed"
    assert partial.available == 85 and partial.tier.status == "provisional"
    assert partial.eos is not None
    # 若缺值當 0 分，EOS 會掉到 47；標準化後仍在合理範圍
    assert partial.eos > 47


# ------------------------------------------------------------------ 覆蓋率閘門

@pytest.mark.parametrize("available,status,publishes", [
    (100, "confirmed", True), (90, "confirmed", True),
    (89.9, "provisional", True), (70, "provisional", True),
    (69.9, "insufficient", False), (0, "insufficient", False),
])
def test_coverage_tiers(rubric: Rubric, available, status, publishes):
    tier = rubric.tier_for(available)
    assert tier.status == status
    assert tier.publish_eos is publishes


def test_insufficient_coverage_withholds_the_score(rubric: Rubric, anchors: list[dict]):
    """9/1 覆蓋率僅 61（C、D 全缺），依規則不出分。"""
    res = engine.compute(rubric, by_date(anchors, "2026-09-01")["inputs"])
    assert res.available == 61
    assert res.tier.status == "insufficient"
    assert res.eos is None and res.rating is None
    assert res.published is False
    # 但已取得的構面仍要能顯示
    assert res.dimensions["A"].earned == 13
    assert res.dimensions["B"].earned == 20


# ------------------------------------------------------------------ 錨點日回歸

# (日期, 覆蓋率, EOS, 覆蓋率分級)
ANCHOR_EXPECTATIONS = [
    ("2026-09-01",  61, None, "insufficient"),
    ("2026-09-02",  83,   63, "provisional"),
    ("2026-09-03", 100,   56, "confirmed"),
    ("2026-09-09",  96,   71, "confirmed"),
    ("2026-09-21",  76,   84, "provisional"),
    ("2026-09-22",  80,   81, "provisional"),
    ("2026-09-23",  75,   71, "provisional"),
    ("2026-09-24", 100,   60, "confirmed"),
]


@pytest.mark.parametrize("day,available,eos,status", ANCHOR_EXPECTATIONS)
def test_anchor_day_regression(rubric: Rubric, anchors: list[dict],
                               day, available, eos, status):
    res = engine.compute(rubric, by_date(anchors, day)["inputs"])
    assert res.available == available, f"{day} 覆蓋率"
    assert res.eos == eos, f"{day} EOS"
    assert res.tier.status == status, f"{day} 分級"


def test_full_coverage_days_match_human_judgement(rubric: Rubric, anchors: list[dict]):
    """覆蓋率 100 的兩天是 rubric 品質的關鍵指標：必須落在 ±5。

    這是整份 rubric 存在的理由 —— 資料齊全時要能重現人工判讀。
    """
    for day in ("2026-09-03", "2026-09-24"):
        a = by_date(anchors, day)
        res = engine.compute(rubric, a["inputs"])
        assert res.available == 100
        assert abs(res.eos - a["human_eos"]) <= 5, f"{day} 偏離人工判讀過多"


def test_2026_09_24_reproduces_human_score_exactly(rubric: Rubric, anchors: list[dict]):
    a = by_date(anchors, "2026-09-24")
    res = engine.compute(rubric, a["inputs"])
    assert res.eos == a["human_eos"] == 60
    assert res.rating == "中性等待"


# ------------------------------------------------------------------ 個別構面

def test_trend_checklist_prorates_over_available_checks(rubric: Rubric):
    b1 = rubric.dimensions["B"].items[0]
    full = {"close_adj_gt_ma20": True, "ma20_gt_ma60": True,
            "close_adj_gt_ma120": True, "ret60_positive": True}
    earned, available, _ = b1.score(full)
    assert (earned, available) == (10, 10)

    # 只有 MA20 可得且成立 -> prorate 後仍為滿分（Phase 1 前的狀態）
    earned, available, _ = b1.score({"close_adj_gt_ma20": True})
    assert (earned, available) == (10, 10)

    # 四項全缺 -> 不計分
    earned, available, _ = b1.score({})
    assert earned is None and available == 0

    # 一半成立
    earned, _, _ = b1.score({"close_adj_gt_ma20": True, "close_adj_gt_ma120": False})
    assert earned == pytest.approx(10 * 3 / 6)


def test_drawdown_uses_absolute_value(rubric: Rubric):
    """回檔輸入為負值，查表前取絕對值；『越跌越高分』不是本模型邏輯。"""
    b2 = rubric.dimensions["B"].items[1]
    assert b2.score({"drawdown20": -0.05})[0] == 7      # 理想區間 3%-8%
    assert b2.score({"drawdown20": -0.001})[0] == 4     # 貼在高點
    assert b2.score({"drawdown20": -0.30})[0] == 2      # 趨勢可能反轉
    assert b2.score({"drawdown20": -0.05})[0] == b2.score({"drawdown20": 0.05})[0]


def test_volume_ratio_is_conditional_on_direction(rubric: Rubric):
    """同樣的量比，漲日與跌日意義相反 —— 放量上漲是確認，放量下跌是負向。"""
    f1 = rubric.dimensions["F"].items[0]
    assert f1.score({"volume_ratio": 1.5, "day_direction": "up"})[0] == 5
    assert f1.score({"volume_ratio": 1.5, "day_direction": "down"})[0] == 0
    assert f1.score({"volume_ratio": 0.3, "day_direction": "up"})[0] == 1
    assert f1.score({"volume_ratio": 0.3, "day_direction": "down"})[0] == 4
    assert f1.score({"volume_ratio": 1.5})[0] is None   # 缺方向即不計分


def test_us_factors_use_shared_percentage_bands(rubric: Rubric):
    """D 的四個變數共用同一組門檻，只有滿分權重不同（SOX 5 / TSM 4 / 其餘 3）。"""
    d = rubric.dimensions["D"]
    maxes = {i.input: i.max for i in d.items}
    assert maxes == {"sox_ret": 5, "tsm_ret": 4, "ndx_ret": 3, "nvda_ret": 3}
    for item in d.items:
        assert item.score({item.input: 0.02})[0] == item.max          # 大漲 -> 滿分
        assert item.score({item.input: -0.02})[0] == 0                # 大跌 -> 0
        assert item.score({item.input: 0.0})[0] == pytest.approx(item.max * 0.5)


# ------------------------------------------------------------------ 比較

def test_compare_refuses_mixed_confidence(rubric: Rubric, anchors: list[dict]):
    """暫定分數不可拿來跟確定分數相減 —— 那會產生資料回補造成的假訊號。"""
    confirmed = engine.compute(rubric, by_date(anchors, "2026-09-24")["inputs"])
    withheld = engine.compute(rubric, by_date(anchors, "2026-09-01")["inputs"])
    out = engine.compare(confirmed, withheld)
    assert out["comparable"] is False


def test_compare_reports_deltas(rubric: Rubric, anchors: list[dict]):
    cur = engine.compute(rubric, by_date(anchors, "2026-09-24")["inputs"])
    prev = engine.compute(rubric, by_date(anchors, "2026-09-03")["inputs"])
    out = engine.compare(cur, prev)
    assert out["comparable"] is True
    assert out["eos_delta"] == 60 - 56
    # A 兩天覆蓋率相同，可直接相減；13 - 6 = 7
    assert out["dimension_delta"]["A"] == pytest.approx(7)


def test_compare_skips_dimensions_with_different_coverage(rubric: Rubric, anchors: list[dict]):
    cur = engine.compute(rubric, by_date(anchors, "2026-09-24")["inputs"])
    prev = engine.compute(rubric, by_date(anchors, "2026-09-09")["inputs"])
    out = engine.compare(cur, prev)
    assert out["dimension_delta"]["E"] is None   # 9/9 的 E 缺匯率，覆蓋率不同


# ------------------------------------------------------------------ 輸出

def test_rating_bands(rubric: Rubric):
    assert rubric.rating_for(85) == "高機會區"
    assert rubric.rating_for(80) == "高機會區"
    assert rubric.rating_for(79) == "偏有利"
    assert rubric.rating_for(64) == "中性等待"
    assert rubric.rating_for(35) == "偏不利"
    assert rubric.rating_for(10) == "高風險區"


def test_result_serialises(rubric: Rubric, anchors: list[dict]):
    res = engine.compute(rubric, by_date(anchors, "2026-09-24")["inputs"])
    d = res.to_dict()
    assert d["eos"] == 60
    assert d["coverage_status"] == "confirmed"
    assert set(d["dimensions"]) == set("ABCDEF")
    json.dumps(d, ensure_ascii=False)          # 必須可序列化，否則寫不進每日快照


def test_explain_is_readable(rubric: Rubric, anchors: list[dict]):
    res = engine.compute(rubric, by_date(anchors, "2026-09-24")["inputs"])
    text = res.explain()
    assert "EOS 60" in text and "中性等待" in text
    assert text.count("\n") == 6               # 標題 + 六構面
