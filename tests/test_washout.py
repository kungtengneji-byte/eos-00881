"""洗盤假說 rubric 的校準測試。

基準是 Drive 上的每日工作簿〈動能與洗盤〉分頁列出的逐構面得分。
可用的完整錨點只有兩天 —— 9/03（19/100）與 9/23（57/100）——
因為工作表的構面定義在三週內改過至少五次，只有這兩天用的是同一組
（見 washout_rubric_v0.2.yaml 檔頭的版本沿革）。

逐構面比對而不是只對總分：總分對了但兩個構面互相抵銷的情況必須擋掉。
"""

from __future__ import annotations

from datetime import date

import pytest

from eos import engine, washout
from eos.rubric import Rubric

RUBRIC_PATH = "washout_rubric_v0.2.yaml"


@pytest.fixture(scope="module")
def rubric() -> Rubric:
    return Rubric.load(RUBRIC_PATH)


# ------------------------------------------------------------------ 錨點

# 2026-09-03 的實際輸入。工作表當天 20:05 建檔時 TWSE 還沒發布融資，
# 所以它的 W4 是拿散戶期貨代理給的 4 分；平台有定版的 -69.36 億，
# 兩者不可比，W4 不列入逐構面比對（見 test_sep03_w4_is_not_comparable）。
SEP03 = {
    "institutional_net_3d_100m": -1227.67,   # 9/1 +566.38、9/2 -1152.45、9/3 -637.11 的實際定版
    "institutions_buying_3d": 1,             # 三日累計只有投信為正
    "institutions_buying": 1,                # 當日只有投信買超 +11.42
    "turnover_change_pct": 1.72,             # 量增價跌
    "taiex_direction": "down",
    "advance_ratio_pct": 21.22,              # 官方股票口徑 209 漲 / 776 跌
    "taiex_gt_ma5": False,                   # 跌破 5 日線 46,286
    "taiex_gt_ma10": True,                   # 高於 10 日線約 18 點
    "taiex_gt_ma20": True,
    "close_position": 0.027,                 # 收盤距當日低點僅 18.30 點
    "foreign_futures_oi_change_1d": 3754,    # 外資台指期由 -85,329 回補到 -81,575
    "txo_pc_oi_pct": 77.16,
    "recovered_days_3": 0,
    "breadth_positive": False,
}

SEP03_EXPECTED = {"W1": 3, "W2": 0, "W3": 3, "W5": 7, "W6": 3, "W7": 0}
SEP03_WORKBOOK = {"W1": 3, "W2": 0, "W3": 2, "W5": 7, "W6": 3, "W7": 0}

# 2026-09-23。兩處與工作表不同，都是資料而不是模型的差異：
#   * 投信：工作表記 -42.16 億（9/24 08:15 抓的早版），TWSE 後來定版為 -47.59。
#     這讓投信的三日累計由 +0.14 翻成 -5.29，W1b 由 3 家變 2 家。
#   * 融資：工作表記 9/23 未發布、沿用 9/22 的 +18.24；平台有 9/23 的 +15.06。
#     兩者落在同一段，W4 都是 5 分。
SEP23 = {
    "institutional_net_3d_100m": 1469.93,
    "institutions_buying_3d": 2,
    "institutions_buying": 2,                # 外資買、自營買、投信賣
    "turnover_change_pct": -17.07,
    "taiex_direction": "up",
    "advance_ratio_pct": 40.84,              # 官方 388 漲 / 562 跌
    "taiex_gt_ma5": True,
    "taiex_gt_ma10": True,
    "taiex_gt_ma20": True,
    "close_position": 0.588,
    "margin_change_100m": 15.06,
    "foreign_futures_oi_change_1d": -516,    # 再加空
    "txo_pc_oi_pct": 79.83,
    "recovered_days_3": 3,                   # 收盤高於前三日收盤
    "breadth_positive": False,               # 但漲家少於跌家
}

SEP23_EXPECTED = {"W1": 13, "W2": 9, "W3": 11, "W4": 5, "W5": 4, "W6": 5, "W7": 8}
SEP23_WORKBOOK = {"W1": 15, "W2": 9, "W3": 11, "W4": 5, "W5": 4, "W6": 5, "W7": 8}


def _earned(rubric: Rubric, inputs: dict) -> dict[str, float]:
    res = engine.compute(rubric, inputs)
    return {k: (None if d.earned is None else round(d.earned, 2))
            for k, d in res.dimensions.items()}


@pytest.mark.parametrize("key,want", sorted(SEP03_EXPECTED.items()))
def test_sep03_dimensions(rubric: Rubric, key: str, want: float) -> None:
    assert _earned(rubric, SEP03)[key] == pytest.approx(want)


@pytest.mark.parametrize("key,want", sorted(SEP23_EXPECTED.items()))
def test_sep23_dimensions(rubric: Rubric, key: str, want: float) -> None:
    assert _earned(rubric, SEP23)[key] == pytest.approx(want)


def test_sep03_w4_is_not_comparable(rubric: Rubric) -> None:
    """9/03 工作表沒有融資，平台有 —— 這一項本來就對不起來，不要假裝對得起來。

    平台的 -69.36 億是 TWSE 定版值（9/02 +75.60 與 9/07 +63.77 都與工作表
    逐筆吻合，證明這條管線沒問題），工作表只是建檔當下還沒發布。
    """
    got = _earned(rubric, {**SEP03, "margin_change_100m": -69.36})
    assert got["W4"] == pytest.approx(15)        # 大幅去槓桿 -> 滿分
    assert _earned(rubric, SEP03)["W4"] is None  # 沒有融資就不計分，不是給 0


def test_sep23_trust_revision_moves_w1(rubric: Rubric) -> None:
    """投信被 TWSE 事後修正，把 W1b 由 3 家打成 2 家 —— 模型沒錯，是資料變了。

    工作表用的早版讓投信三日累計為 +0.14 億（勉強為正），定版後是 -5.29 億。
    這是刀鋒上的個案，工作表自己也在反證欄註明「投信3日僅+0.14億」。
    """
    assert _earned(rubric, SEP23)["W1"] == pytest.approx(13)
    early = {**SEP23, "institutions_buying_3d": 3}
    assert _earned(rubric, early)["W1"] == pytest.approx(SEP23_WORKBOOK["W1"])


def test_totals_stay_in_band(rubric: Rubric) -> None:
    """兩個錨點的分類要與工作表一致（分數容許小幅偏差，分類不容許）。"""
    r03 = engine.compute(rubric, SEP03)
    r23 = engine.compute(rubric, SEP23)
    assert r03.rating == "偏派發／風險釋放"
    assert r23.rating == "中性待確認"


def test_residuals_are_documented(rubric: Rubric) -> None:
    """已知殘差就這兩處，其餘構面必須逐項相符 —— 新的偏差要讓測試紅掉。"""
    for inputs, expected, workbook in ((SEP03, SEP03_EXPECTED, SEP03_WORKBOOK),
                                       (SEP23, SEP23_EXPECTED, SEP23_WORKBOOK)):
        got = _earned(rubric, inputs)
        for key, want in expected.items():
            assert got[key] == pytest.approx(want), key
            if want != workbook[key]:
                # 只有 9/03 的 W3（+1）與 9/23 的 W1（-2）允許不同
                assert (key, want - workbook[key]) in {("W3", 1), ("W1", -2)}


# ------------------------------------------------------------------ 方向判定

def test_weak_close_counts_as_pullback() -> None:
    """9/22 收盤 +0.17% 卻收在全日最低，量增 24.5% 不可以拿推升日的滿分。

    盤中衝到 48,601.53 創新高、收 47,800.17 正好是全日最低，
    上影線約 800 點。只看收盤漲跌號會把它判成「推升日放量」，
    但工作表當天的結論是「高檔換手偏派發」。
    """
    assert washout._direction(0.0017, 0.0) == "down"
    assert washout._direction(0.0075, 0.588) == "up"
    assert washout._direction(-0.0067, 0.027) == "down"
    assert washout._direction(None, 0.5) is None


def test_direction_needs_no_close_position() -> None:
    """盤中高低缺值時仍要能判方向，只是少了收弱的那一條規則。"""
    assert washout._direction(-0.01, None) == "down"
    assert washout._direction(0.01, None) == "up"


# ------------------------------------------------------------------ 推導

def _rows(*triples: tuple[str, float, float | None]) -> list[dict]:
    return [{"date": d, "close": c, "institutional_net_100m": n,
             "foreign_net_100m": n, "trust_net_100m": n, "dealer_net_100m": n}
            for d, c, n in triples]


def test_moving_average_refuses_short_history() -> None:
    """資料不足就回 None —— 用 8 天算「20 日線」比沒有更糟。"""
    rows = _rows(("2026-09-01", 100.0, 1.0), ("2026-09-02", 102.0, 1.0))
    assert washout.moving_average(rows, 2) == pytest.approx(101.0)
    assert washout.moving_average(rows, 20) is None


def test_cumulative_refuses_partial_window() -> None:
    """三日累計少一天就不算，不以兩天冒充。"""
    rows = _rows(("2026-09-01", 1.0, 10.0), ("2026-09-02", 1.0, 20.0))
    assert washout.cumulative(rows, "institutional_net_100m", 3) is None
    rows.append({"date": "2026-09-03", "close": 1.0, "institutional_net_100m": 30.0})
    assert washout.cumulative(rows, "institutional_net_100m", 3) == pytest.approx(60.0)


def test_buying_count_returns_none_when_an_institution_is_missing() -> None:
    """「兩家在買」和「兩家在買、一家不知道」是不同的事。"""
    rows = [{"date": "2026-09-03", "foreign_net_100m": 10.0,
             "trust_net_100m": 5.0, "dealer_net_100m": None}]
    assert washout.buying_count(rows, 1) is None
    rows[0]["dealer_net_100m"] = -3.0
    assert washout.buying_count(rows, 1) == 2


def test_recovered_days_looks_backward() -> None:
    """W7 是回看近三日有沒有站回來，不是等 T+3 才回填。"""
    rows = _rows(("2026-09-18", 47180.75, 1.0), ("2026-09-21", 47718.84, 1.0),
                 ("2026-09-22", 47800.17, 1.0), ("2026-09-23", 48157.29, 1.0))
    assert washout.recovered_days(rows) == 3
    rows[-1]["close"] = 47000.0
    assert washout.recovered_days(rows) == 0
