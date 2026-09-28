"""連續買賣超判定的測試。

重點在邊界：0 要中斷連續、方向改變要中斷、連到視窗盡頭要標成截斷。
這些規則與 eos/marketflow.py 的波段切分刻意一致 ——
同一份資料用兩套「什麼叫連續」的定義，兩頁的數字就會互相矛盾。
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

import pytest

from eos import stockflow
from sources import twse

FIXTURES = Path(__file__).parent / "fixtures"


# ---------------------------------------------------------------- T86 解析

def test_parse_splits_all_four_institutions():
    payload = {
        "fields": ["證券代號"] * 19,
        "data": [
            ["2330", "台積電 ", "1,000", "400", "600", "0", "0", "0",
             "500", "200", "300", "-100", "0", "0", "-100", "0", "0", "0", "800"],
        ],
    }
    nets, names = twse.parse_stock_institutional_all(payload)
    # 外陸資 600、外資自營商 0、投信 300、自營商 -100
    assert nets["2330"] == [600, 0, 300, -100]
    assert names["2330"] == "台積電", "名稱要去掉 TWSE 補的尾端空白"
    # 三大法人合計必須等於四項之和，否則不能省略不存
    assert sum(nets["2330"]) == 800


def test_parse_drops_rows_with_no_institutional_activity():
    payload = {
        "fields": ["證券代號"] * 19,
        "data": [
            ["1111", "無動作", "0", "0", "0", "0", "0", "0",
             "0", "0", "0", "0", "0", "0", "0", "0", "0", "0", "0"],
            ["2222", "有動作", "0", "0", "5", "0", "0", "0",
             "0", "0", "0", "0", "0", "0", "0", "0", "0", "0", "5"],
        ],
    }
    nets, _ = twse.parse_stock_institutional_all(payload)
    assert "1111" not in nets
    assert "2222" in nets


# ---------------------------------------------------------------- 連續判定

D0 = date(2026, 9, 1)


def _window(series: dict[str, list[int | None]]):
    """把「代號 -> 每日外資淨額」展開成 stockflow 需要的視窗結構。

    None 代表當天完全沒有法人動作（存檔時就被略過），與 0 不同：
    0 是「有紀錄但淨額為零」。兩者都會中斷連續，但來源不一樣。
    """
    n = max(len(v) for v in series.values())
    out = []
    for i in range(n):
        nets = {}
        for code, vals in series.items():
            v = vals[i] if i < len(vals) else None
            if v is None:
                continue
            nets[code] = [v, 0, 0, 0]
        out.append((D0 + timedelta(days=i), nets))
    return out


@pytest.fixture(autouse=True)
def _no_names(monkeypatch, tmp_path):
    monkeypatch.setattr(stockflow, "NAMES_PATH", tmp_path / "names.json")


def test_counts_consecutive_same_direction_days():
    w = _window({"A": [100, 200, 300]})
    s = stockflow.streaks(w, "foreign", min_days=3)
    assert len(s) == 1
    assert s[0].days == 3
    assert s[0].shares == 600
    assert s[0].direction == "buy"
    assert s[0].start == D0


def test_a_zero_day_breaks_the_streak():
    """0 沒有方向。把它算成「繼續買」會把兩段黏成一段。"""
    w = _window({"A": [100, 0, 200, 300]})
    s = stockflow.streaks(w, "foreign", min_days=1)
    assert s[0].days == 2, "只應數到 0 之後的兩天"
    assert s[0].shares == 500


def test_a_missing_day_breaks_the_streak():
    w = _window({"A": [100, None, 200, 300]})
    s = stockflow.streaks(w, "foreign", min_days=1)
    assert s[0].days == 2


def test_direction_change_breaks_the_streak():
    """兩段同長度時取累計量大的那一段（|−110| > |200|？不，買方 200 較大）。"""
    w = _window({"A": [100, 100, -50, -60]})
    s = stockflow.streaks(w, "foreign", min_days=1)
    assert s[0].days == 2, "四天被切成兩段各兩天"
    assert s[0].direction == "buy"
    assert s[0].shares == 200
    assert s[0].status == "broken", "最新日是賣超，買波已中斷"


def test_a_broken_streak_still_counts_and_is_labelled_broken():
    """工作表 2026-09-18 的連買第一名（第一金）早在 09-14 就中斷，仍排第一。

    原本這裡只收「仍在進行」的連續，把剛結束的長連買整個丟掉 ——
    一段剛結束的 25 日連買本身就是資訊，該由狀態欄說明，不是刪掉。
    """
    w = _window({"A": [100, 100, 100, 100, -5]})
    s = stockflow.streaks(w, "foreign", min_days=1)
    assert len(s) == 1
    assert s[0].days == 4 and s[0].direction == "buy"
    assert s[0].status == "broken"


def test_missing_on_the_last_day_is_pending_not_broken():
    """最新日該檔完全沒有法人紀錄 -> 無從確認是否延續，記「待確認」。"""
    w = _window({"A": [100, 100, 100, None], "B": [1, 1, 1, 1]})
    a = next(x for x in stockflow.streaks(w, "foreign", min_days=1) if x.code == "A")
    assert a.status == "pending"


def test_running_streak_is_labelled_running():
    w = _window({"A": [100, 100, 100]})
    assert stockflow.streaks(w, "foreign", min_days=1)[0].status == "running"


# ---------------------------------------------------------------- 計分

P = stockflow.ScoreParams()


def _streak(**kw):
    base = dict(code="2330", name="台積電", institution="foreign", direction="buy",
                days=10, shares=1000, start=D0, end=D0, truncated=False,
                status="broken", peers=1, price=None)
    base.update(kw)
    return stockflow.Streak(**base)


@pytest.mark.parametrize("days,status,peers,expected", [
    # 工作表 2026-09-18 版的七筆實例（天數／狀態／兩類以上同向／分數）
    (25, "broken", 2, 28.25),
    (11, "running", 2, 24.11),
    (19, "broken", 1, 19.19),
    (18, "broken", 1, 18.18),
    (13, "pending", 1, 18.13),
    (5, "broken", 2, 8.05),
])
def test_score_matches_workbook_formula(days, status, peers, expected):
    """分數 = 天數 × 權重 + 狀態加分 + 兩類同向加分 + 天數/100。

    最後那一項工作表的規則②沒寫出來，但七筆實例裡六筆只有加上它才對得起來。
    """
    assert _streak(days=days, status=status, peers=peers).score(P) == pytest.approx(expected)


def test_status_bonuses_come_from_the_sheet_parameters():
    base = _streak(days=10, peers=0)
    assert base.score(P) == pytest.approx(10.10)
    assert _streak(days=10, peers=0, status="running").score(P) == pytest.approx(20.10)
    assert _streak(days=10, peers=0, status="pending").score(P) == pytest.approx(15.10)


def test_peer_bonus_needs_two_or_more_institutions():
    assert _streak(peers=1).score(P) == pytest.approx(10.10)
    assert _streak(peers=2).score(P) == pytest.approx(13.10)
    assert _streak(peers=3).score(P) == pytest.approx(13.10), "三類同向不再加倍"


def test_params_come_from_config_not_hardcoded():
    p = stockflow.ScoreParams.from_config(
        {"min_days": 5, "day_weight": 2, "bonus_running": 1,
         "bonus_pending": 0, "bonus_multi_institution": 7, "top_n": 3})
    assert p.min_days == 5 and p.top_n == 3
    assert _streak(days=10, status="running", peers=2).score(p) == pytest.approx(
        10 * 2 + 1 + 7 + 0.10)


def test_amount_uses_price_and_matches_workbook():
    """工作表：聯電 100,630 張 × 141.50 元 = 142.39 億。"""
    s = _streak(shares=100_630 * 1000, price=141.50)
    assert s.lots == pytest.approx(100_630)
    assert s.amount_100m == pytest.approx(142.39, abs=0.01)


def test_amount_is_none_without_a_price():
    """沒有參考價就不報金額，不拿別天的價格頂替。"""
    assert _streak(price=None).amount_100m is None


def test_foreign_includes_the_foreign_dealer_leg():
    """外資＝外陸資＋外資自營商，與 TWSE 三大法人的口徑一致。"""
    w = [(D0, {"A": [10, 5, 0, 0]}), (D0 + timedelta(days=1), {"A": [10, 5, 0, 0]})]
    assert stockflow.streaks(w, "foreign", min_days=1)[0].shares == 30


def test_peers_does_not_double_count_the_foreign_dealer_leg():
    """外資買、外資自營商也買，只算一類同向。"""
    w = [(D0, {"A": [10, 5, 0, 0]}), (D0 + timedelta(days=1), {"A": [10, 5, 0, 0]})]
    assert stockflow.streaks(w, "foreign", min_days=1)[0].peers == 1


def test_streak_reaching_the_window_edge_is_marked_truncated():
    w = _window({"A": [100, 100, 100]})
    s = stockflow.streaks(w, "foreign", min_days=1)
    assert s[0].truncated, "連到視窗最舊一天，真正天數可能更長"


def test_streak_inside_the_window_is_not_truncated():
    w = _window({"A": [-10, 100, 100]})
    s = stockflow.streaks(w, "foreign", min_days=1)
    assert s[0].days == 2
    assert not s[0].truncated


def test_min_days_filters_short_streaks():
    w = _window({"A": [100, 100], "B": [100, 100, 100]})
    codes = {s.code for s in stockflow.streaks(w, "foreign", min_days=3)}
    assert codes == {"B"}


def test_total_sums_all_four_institutions():
    w = [(D0, {"A": [10, 1, 2, 3]}), (D0 + timedelta(days=1), {"A": [10, 1, 2, 3]})]
    s = stockflow.streaks(w, "total", min_days=1)
    assert s[0].shares == 32


def test_total_can_differ_in_sign_from_foreign_alone():
    """外資買、投信與自營賣更多時，合計是賣超 —— 兩個榜本來就會不一樣。"""
    row = [100, 0, -80, -60]
    w = [(D0, {"A": row}), (D0 + timedelta(days=1), {"A": row})]
    assert stockflow.streaks(w, "foreign", min_days=1)[0].direction == "buy"
    assert stockflow.streaks(w, "total", min_days=1)[0].direction == "sell"


def test_unknown_institution_is_rejected_loudly():
    w = [(D0, {"A": [1, 0, 0, 0]})]
    with pytest.raises(KeyError):
        stockflow.streaks(w, "銀行", min_days=1)


# ---------------------------------------------------------------- 排序

def test_top_ranks_by_score_then_by_size():
    w = _window({
        "LONG":  [10, 10, 10, 10],
        "BIG":   [None, 900, 900, 900],
        "SMALL": [None, 1, 1, 1],
    })
    picked = stockflow.top(w, "foreign", n=5)
    assert [s.code for s in picked["buy"]] == ["LONG", "BIG", "SMALL"]
    assert picked["sell"] == []


def test_top_limits_each_side_to_n():
    w = _window({c: [10, 10, 10] for c in "ABCDEFG"})
    assert len(stockflow.top(w, "foreign", n=5)["buy"]) == 5


def test_leading_picks_the_best_institution_per_stock():
    """工作表的主表混著不同法人別：每一檔只留分數最高的那一類。"""
    # A：投信連 4 天，外資只有 3 天 -> 主導法人應為投信
    w = [(D0 + timedelta(days=i),
          {"A": [(5 if i >= 1 else 0), 0, 7, 0]}) for i in range(4)]
    lead = stockflow.leading(w, institutions=("foreign", "trust"))
    assert [s.code for s in lead["buy"]] == ["A"]
    assert lead["buy"][0].institution == "trust"
    assert lead["buy"][0].days == 4


def test_lots_are_shares_divided_by_one_thousand():
    w = _window({"A": [12_345, 1_000]})
    s = stockflow.streaks(w, "foreign", min_days=2)[0]
    assert s.shares == 13_345
    assert s.lots == pytest.approx(13.345)


# ---------------------------------------------------------------- 儲存

def test_saved_day_round_trips(tmp_path, monkeypatch):
    monkeypatch.setattr(stockflow, "STOCKS", tmp_path)
    monkeypatch.setattr(stockflow, "NAMES_PATH", tmp_path / "names.json")
    stockflow.save_day(D0, {"2330": [1, 2, 3, 4]}, {"2330": "台積電"})
    assert stockflow.load_day(D0) == {"2330": [1, 2, 3, 4]}
    assert stockflow.load_names()["2330"] == "台積電"
    assert stockflow.available_days() == [D0]


def test_names_accumulate_across_days(tmp_path, monkeypatch):
    """名稱單獨存一份共用檔，不隨每日檔重複 1,300 筆中文名。"""
    monkeypatch.setattr(stockflow, "STOCKS", tmp_path)
    monkeypatch.setattr(stockflow, "NAMES_PATH", tmp_path / "names.json")
    stockflow.save_day(D0, {"A": [1, 0, 0, 0]}, {"A": "甲"})
    stockflow.save_day(D0 + timedelta(days=1), {"B": [1, 0, 0, 0]}, {"B": "乙"})
    assert stockflow.load_names() == {"A": "甲", "B": "乙"}


def test_available_days_ignores_the_names_file(tmp_path, monkeypatch):
    monkeypatch.setattr(stockflow, "STOCKS", tmp_path)
    monkeypatch.setattr(stockflow, "NAMES_PATH", tmp_path / "names.json")
    stockflow.save_day(D0, {"A": [1, 0, 0, 0]}, {"A": "甲"})
    assert stockflow.available_days() == [D0]


def test_missing_days_reports_gaps(tmp_path, monkeypatch):
    monkeypatch.setattr(stockflow, "STOCKS", tmp_path)
    monkeypatch.setattr(stockflow, "NAMES_PATH", tmp_path / "names.json")
    stockflow.save_day(D0, {"A": [1, 0, 0, 0]}, {})
    wanted = [D0, D0 + timedelta(days=1), D0 + timedelta(days=2)]
    assert stockflow.missing_days(wanted) == wanted[1:]


def test_window_is_oldest_first_and_capped(tmp_path, monkeypatch):
    monkeypatch.setattr(stockflow, "STOCKS", tmp_path)
    monkeypatch.setattr(stockflow, "NAMES_PATH", tmp_path / "names.json")
    for i in range(5):
        stockflow.save_day(D0 + timedelta(days=i), {"A": [i + 1, 0, 0, 0]}, {})
    w = stockflow.load_window(D0 + timedelta(days=4), lookback=3)
    assert [d for d, _ in w] == [D0 + timedelta(days=i) for i in (2, 3, 4)]


def test_window_excludes_days_after_the_target(tmp_path, monkeypatch):
    monkeypatch.setattr(stockflow, "STOCKS", tmp_path)
    monkeypatch.setattr(stockflow, "NAMES_PATH", tmp_path / "names.json")
    for i in range(3):
        stockflow.save_day(D0 + timedelta(days=i), {"A": [1, 0, 0, 0]}, {})
    w = stockflow.load_window(D0 + timedelta(days=1), lookback=10)
    assert [d for d, _ in w] == [D0, D0 + timedelta(days=1)]


def test_report_has_both_sides_for_every_institution(tmp_path, monkeypatch):
    monkeypatch.setattr(stockflow, "STOCKS", tmp_path)
    monkeypatch.setattr(stockflow, "NAMES_PATH", tmp_path / "names.json")
    for i in range(4):
        stockflow.save_day(D0 + timedelta(days=i),
                           {"UP": [10, 0, 0, 0], "DN": [-10, 0, 0, 0]},
                           {"UP": "漲", "DN": "跌"})
    rep = stockflow.build_report(D0 + timedelta(days=3), lookback=10)
    assert rep["window_days"] == 4
    assert rep["institutions"]["foreign"]["buy"][0]["code"] == "UP"
    assert rep["institutions"]["foreign"]["sell"][0]["code"] == "DN"
    # 投信全為 0，不該有任何連續
    assert rep["institutions"]["trust"]["buy"] == []
    assert json.dumps(rep, ensure_ascii=False), "必須可序列化成 JSON 給前端"


# ---------------------------------------------------------------- ETF 標記

@pytest.mark.parametrize("code,expected", [
    ("00881", True), ("00940", True), ("00693U", True), ("00666R", True),
    ("2330", False), ("2883B", False), ("1101", False), ("0050", True),
])
def test_etf_codes_are_flagged(code, expected):
    assert stockflow.is_etf(code) is expected


def test_report_marks_etf_entries():
    """自營商榜幾乎全是 ETF 避險部位，前端要能把它們分出來。"""
    w = [(D0 + timedelta(days=i),
          {"00940": [0, 0, 0, 500], "2330": [0, 0, 0, 500]}) for i in range(3)]
    picked = stockflow.top(w, "dealer", n=5)
    flags = {s.code: s.etf for s in picked["buy"]}
    assert flags == {"00940": True, "2330": False}


def test_report_keeps_spare_candidates_for_client_side_filtering():
    """前端可以「排除 ETF」，濾掉之後還要湊得滿 top_n 名。"""
    codes = {f"00{i:03d}": [10, 0, 0, 0] for i in range(9)}
    codes["2330"] = [10, 0, 0, 0]
    w = [(D0 + timedelta(days=i), codes) for i in range(3)]
    rep = stockflow.build_report(D0 + timedelta(days=2), lookback=10)
    rows = rep["institutions"]["foreign"]["buy"]
    assert rep["top_n"] == 5
    assert len(rows) > 5, "只存 5 筆的話，濾掉 ETF 就湊不滿"
    assert any(not r["etf"] for r in rows)


def test_prices_are_kept_per_day_so_a_backfill_cannot_roll_them_back(tmp_path, monkeypatch):
    """回補舊日子不該讓 Top5 的參考價倒退。

    原本只存一份最新的，回補 09-18 就會把 09-24 的價格蓋掉。
    改成一天一個檔之後，「最新是哪一天」由檔名決定，不是由寫入順序決定。
    """
    monkeypatch.setattr(stockflow, "STOCKS", tmp_path)
    monkeypatch.setattr(stockflow, "PRICES", tmp_path / "px")
    stockflow.save_prices(date(2026, 9, 24), {"2330": (1000.0, 0.01)})
    stockflow.save_prices(date(2026, 9, 18), {"2330": (900.0, -0.02)})

    as_of, px = stockflow.latest_prices()
    assert as_of == "2026-09-24" and px["2330"] == 1000.0
    # 兩天的資料都在，指定日期可以取到當天的
    assert stockflow.latest_prices(date(2026, 9, 18))[0] == "2026-09-18"
    assert stockflow.price_days() == [date(2026, 9, 18), date(2026, 9, 24)]


def test_price_day_keeps_the_daily_change(tmp_path, monkeypatch):
    """產業資金輪動要用每一天每一檔的漲跌做價格同向確認。"""
    monkeypatch.setattr(stockflow, "STOCKS", tmp_path)
    monkeypatch.setattr(stockflow, "PRICES", tmp_path / "px")
    stockflow.save_prices(date(2026, 9, 24), {"2330": (1000.0, -0.01)})
    assert stockflow.load_price_day(date(2026, 9, 24)) == {"2330": [1000.0, -0.01]}
    assert stockflow.load_price_day(date(2026, 9, 23)) is None


# ---------------------------------------------------------------- 逐日明細

def _detail_window(series):
    """{代號: [(外資, 投信, 自營), ...]} -> 明細用的視窗。None 代表當日無紀錄。"""
    n = max(len(v) for v in series.values())
    out = []
    for i in range(n):
        nets = {}
        for code, vals in series.items():
            v = vals[i] if i < len(vals) else None
            if v is None:
                continue
            nets[code] = [v[0], 0, v[1], v[2]]
        out.append((D0 + timedelta(days=i), nets))
    return out


def test_detail_streak_counters_accumulate_and_reset():
    """工作表的定義：同方向累加（+買／−賣），方向改變即重置。

    實測第一金 09-04 外資連續 +10，09-07 轉賣變成 -1，09-08 再轉買變成 +1。
    """
    w = _detail_window({"A": [(10, 0, 0), (20, 0, 0), (-5, 0, 0), (7, 0, 0)]})
    rows = stockflow.detail_rows(w, "A", name="甲")
    assert [r["run_foreign"] for r in rows] == [1, 2, -1, 1]


def test_detail_zero_breaks_the_streak_and_is_left_blank():
    """0 不填也不算一天 —— 填 0 看起來像「連續 0 天」，但那是「這天不算數」。"""
    w = _detail_window({"A": [(10, 0, 0), (0, 0, 0), (10, 0, 0)]})
    rows = stockflow.detail_rows(w, "A")
    assert [r["run_foreign"] for r in rows] == [1, None, 1]


def test_detail_missing_day_resets_every_counter():
    w = _detail_window({"A": [(10, 0, 0), None, (10, 0, 0)], "B": [(1, 0, 0)] * 3})
    rows = stockflow.detail_rows(w, "A")
    assert rows[1]["missing"] is True
    assert "foreign" not in rows[1], "缺值列留白，不放數字"
    assert rows[2]["run_foreign"] == 1


def test_detail_total_comes_from_shares_not_rounded_lots():
    """工作表「檢核差」那欄的 ±1 就是把四捨五入後的張數相加造成的。

    實測第一金 2026-08-26：各法人取整後相加是 9,965，由股直接加總是 9,966。
    平台全程用股的整數，合計欄才是準的。
    """
    # 第一金 2026-08-26 的實際股數（TWSE T86）
    w = [(D0, {"A": [11_221_436, 0, -1_669_154, 413_307]})]
    r = stockflow.detail_rows(w, "A")[0]
    assert r["total"] == pytest.approx(9965.6)   # 存檔時取到小數一位
    assert r["sum_lots"] == 11221 - 1669 + 413 == 9965   # 工作表的「公式」欄
    assert round(r["total"]) == 9966                     # 工作表的「合計(來源)」欄
    assert r["check_diff"] == -1                         # 工作表的「檢核差」欄


def test_detail_check_diff_is_zero_when_rounding_agrees():
    w = [(D0, {"A": [1_000_000, 0, 2_000_000, 3_000_000]})]
    r = stockflow.detail_rows(w, "A")[0]
    assert r["check_diff"] == 0


def test_detail_foreign_includes_the_foreign_dealer_leg():
    w = [(D0, {"A": [10_000, 5_000, 0, 0]})]
    r = stockflow.detail_rows(w, "A")[0]
    assert r["foreign"] == pytest.approx(15.0)
    assert r["total"] == pytest.approx(15.0)


def test_candidate_codes_takes_only_the_listed_top_n():
    """候補是為了讓前端濾掉 ETF 之後湊得滿五名而存的，不是使用者看得到的名單。"""
    report = {"top_n": 2, "institutions": {
        "leading": {"buy": [{"code": "A"}, {"code": "B"}, {"code": "SPARE"}],
                    "sell": [{"code": "C"}]},
        "foreign": {"buy": [{"code": "A"}, {"code": "D"}], "sell": []},
    }}
    assert stockflow.candidate_codes(report) == ["A", "B", "C", "D"]


# ------------------------------------------------ 明細顯示天數與計算視窗拆開

def _seed_days(tmp_path, monkeypatch, n: int, net: int = 1000) -> date:
    """寫 n 個交易日的逐日檔，同一檔股票每天同方向買超。"""
    monkeypatch.setattr(stockflow, "STOCKS", tmp_path)
    monkeypatch.setattr(stockflow, "NAMES_PATH", tmp_path / "names.json")
    for i in range(n):
        stockflow.save_day(D0 + timedelta(days=i), {"2884": [net, 0, 0, 0]},
                           {"2884": "玉山金"})
    return D0 + timedelta(days=n - 1)


REPORT = {"top_n": 5, "institutions": {"leading": {"buy": [{"code": "2884"}],
                                                   "sell": []}}}


def test_detail_streaks_use_the_full_window_not_the_shown_rows(tmp_path, monkeypatch):
    """連買 100 天、表上只列 40 列 —— 最後一列的連續天數必須仍是 100。

    這是把兩個參數拆開的全部理由。若改成只拿最近 40 天去算，
    截斷處會從 1 重新起算，這一頁就會跟〈連續買賣 Top5〉互相矛盾。
    """
    end = _seed_days(tmp_path, monkeypatch, 100)
    d = stockflow.build_detail(end, REPORT, lookback=128, detail_days=40)

    assert d["window_days"] == 100          # 計算範圍
    assert d["shown_days"] == 40            # 顯示範圍
    assert d["truncated"] is True

    rows = d["stocks"][0]["rows"]
    assert len(rows) == 40
    assert rows[-1]["run_foreign"] == 100   # 最新一天：完整視窗的天數
    assert rows[0]["run_foreign"] == 61     # 表上最舊一列也承接了前面的累計


def test_detail_not_marked_truncated_when_everything_fits(tmp_path, monkeypatch):
    """視窗比顯示上限短時不標截斷，說明文字才不會講一句多餘的話。"""
    end = _seed_days(tmp_path, monkeypatch, 12)
    d = stockflow.build_detail(end, REPORT, lookback=128, detail_days=40)
    assert d["window_days"] == d["shown_days"] == 12
    assert d["truncated"] is False
    assert len(d["stocks"][0]["rows"]) == 12


def test_detail_days_none_shows_everything(tmp_path, monkeypatch):
    """detail_days 留空＝不截尾，維持拆開之前的行為。"""
    end = _seed_days(tmp_path, monkeypatch, 50)
    d = stockflow.build_detail(end, REPORT, lookback=128, detail_days=None)
    assert d["shown_days"] == d["window_days"] == 50
    assert d["truncated"] is False


# ------------------------------------------------ 連續天數往視窗之前續數

def test_streak_extends_past_the_window_edge():
    """頂到視窗第一天的連續段，要用視窗之前的資料把天數續完。

    排名視窗故意不拉長：分數是天數乘權重，一段去年結束的長連買會壓過
    今天還在跑的短連買，看板就被舊資料洗版。排名看近期，天數看真相。
    """
    window = _window({"A": [100] * 10})          # 視窗內連買 10 天，頂到第一天
    earlier = [(D0 - timedelta(days=i), {"A": [100, 0, 0, 0]})
               for i in range(6, 0, -1)]         # 更早還有 6 天同向

    plain = stockflow.streaks(window, "foreign", min_days=3)[0]
    assert plain.days == 10 and plain.truncated is True

    ext = stockflow.streaks(window, "foreign", min_days=3, earlier=earlier)[0]
    assert ext.days == 16                        # 10 + 6
    assert ext.start == earlier[0][0]
    assert ext.truncated is True                 # 續完仍吃到 earlier 最舊一天


def test_extension_stops_at_the_first_opposite_day():
    """更早那段方向一轉就停，而且不再標截斷 —— 這時天數是確定的。"""
    window = _window({"A": [100] * 10})
    earlier = [(D0 - timedelta(days=4), {"A": [100, 0, 0, 0]}),
               (D0 - timedelta(days=3), {"A": [-50, 0, 0, 0]}),   # 反向，到此為止
               (D0 - timedelta(days=2), {"A": [100, 0, 0, 0]}),
               (D0 - timedelta(days=1), {"A": [100, 0, 0, 0]})]

    ext = stockflow.streaks(window, "foreign", min_days=3, earlier=earlier)[0]
    assert ext.days == 12                        # 10 + 最後兩天
    assert ext.truncated is False                # 看得到頭了
    assert ext.start == earlier[2][0]


def test_extension_does_not_touch_runs_inside_the_window():
    """沒頂到邊緣的連續段不受影響 —— 續數只處理「看不到頭」那一種。"""
    window = _window({"A": [0, 0, 100, 100, 100, 100]})
    earlier = [(D0 - timedelta(days=1), {"A": [100, 0, 0, 0]})]
    ext = stockflow.streaks(window, "foreign", min_days=3, earlier=earlier)[0]
    assert ext.days == 4 and ext.truncated is False


def test_extension_stops_at_a_day_with_no_record():
    """更早那天完全沒有法人紀錄＝中斷，與 0 同樣處理。"""
    window = _window({"A": [100] * 10})
    earlier = [(D0 - timedelta(days=3), {"A": [100, 0, 0, 0]}),
               (D0 - timedelta(days=2), {"B": [100, 0, 0, 0]}),   # A 當天沒紀錄
               (D0 - timedelta(days=1), {"A": [100, 0, 0, 0]})]
    ext = stockflow.streaks(window, "foreign", min_days=3, earlier=earlier)[0]
    assert ext.days == 11 and ext.truncated is False
