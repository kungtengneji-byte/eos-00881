"""今日結論的測試。

重點不是文字好不好看，而是**它只說資料支持的話**：
不可比就不比、缺值要點出來、非當日權重要揭露。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from eos import engine, summary
from eos.rubric import Rubric

FIXTURES = Path(__file__).parent / "fixtures"
META = {"ex_dividend": {"date": "2026-08-18", "cash": 4.6,
                        "reference_price": 50.55, "full_recovery": 55.15}}


@pytest.fixture(scope="module")
def rubric() -> Rubric:
    return Rubric.load()


@pytest.fixture(scope="module")
def anchors() -> list[dict]:
    return json.loads((FIXTURES / "anchors.json").read_text(encoding="utf-8"))


def result_for(rubric, anchors, day):
    a = next(x for x in anchors if x["date"] == day)
    return engine.compute(rubric, a["inputs"])


def as_fields(anchors, day, **overrides):
    """把錨點輸入包成快照欄位格式。"""
    a = next(x for x in anchors if x["date"] == day)
    out = {k: {"value": v, "status": "ok", "as_of": day, "source": "t", "note": ""}
           for k, v in a["inputs"].items()}
    out.update(overrides)
    return out


def test_headline_states_score_and_rating(rubric, anchors):
    r = result_for(rubric, anchors, "2026-09-24")
    s = summary.build(rubric, r, None, as_fields(anchors, "2026-09-24"), META)
    assert "EOS 60/100" in s["headline"]
    assert "中性等待" in s["headline"]


def test_reports_delta_against_previous(rubric, anchors):
    cur = result_for(rubric, anchors, "2026-09-24")
    prev = result_for(rubric, anchors, "2026-09-03")
    s = summary.build(rubric, cur, prev, as_fields(anchors, "2026-09-24"),
                      META, prev_date="2026-09-03")
    assert "較 09-03" in s["headline"]        # 手機上用月日，不是完整日期
    assert "上升 4 分" in s["headline"]


def test_refuses_to_compare_when_not_comparable(rubric, anchors):
    """9/1 覆蓋率不足未出分，不可拿來當比較基準。"""
    cur = result_for(rubric, anchors, "2026-09-24")
    withheld = result_for(rubric, anchors, "2026-09-01")
    s = summary.build(rubric, cur, withheld, as_fields(anchors, "2026-09-24"), META)
    assert "無已發布分數" in s["headline"] or "無法與前一交易日比較" in s["headline"]
    assert s["drivers"] == [] and s["drags"] == []


def test_withheld_score_says_so_instead_of_inventing_one(rubric, anchors):
    r = result_for(rubric, anchors, "2026-09-01")
    s = summary.build(rubric, r, None, as_fields(anchors, "2026-09-01"), META)
    assert "不計算 EOS" in s["headline"]
    assert "61/100" in s["headline"]


def test_quality_flags_missing_and_stale(rubric, anchors):
    fields = as_fields(anchors, "2026-09-24")
    fields["wcr"]["status"] = "stale"
    fields["top_holdings"] = {"value": [], "status": "stale", "as_of": "2026-09-24",
                              "source": "t", "note": ""}
    r = result_for(rubric, anchors, "2026-09-24")
    s = summary.build(rubric, r, None, fields, META)
    joined = "".join(s["quality"])
    assert "較舊時點" in joined
    assert "非當日權重" in joined


def test_watch_includes_next_rating_band_and_recovery_gap(rubric, anchors):
    fields = as_fields(anchors, "2026-09-24")
    fields["close"] = {"value": 52.50, "status": "ok", "as_of": "2026-09-24",
                       "source": "t", "note": ""}
    r = result_for(rubric, anchors, "2026-09-24")
    s = summary.build(rubric, r, None, fields, META)
    joined = "".join(s["watch"])
    assert "偏有利" in joined and "5 分" in joined     # 60 -> 65
    assert "5.05%" in joined                           # 55.15 / 52.50 - 1


def test_watch_flags_missing_nav(rubric, anchors):
    fields = as_fields(anchors, "2026-09-24")
    fields["nav"] = {"value": None, "status": "missing", "as_of": None,
                     "source": "t", "note": ""}
    r = result_for(rubric, anchors, "2026-09-24")
    s = summary.build(rubric, r, None, fields, META)
    assert any("NAV 尚未發布" in w for w in s["watch"])


def test_holdings_extremes_name_the_actual_stocks(rubric, anchors):
    """只給 WCR 看不出是誰造成的 —— 結論必須點名。"""
    fields = as_fields(anchors, "2026-09-24")
    fields["top_holdings"] = {
        "value": [
            {"code": "2330", "name": "台積電", "weight": 0.3797,
             "close": 2475, "prev_close": 2500, "ret": -0.01, "contrib": -0.0038},
            {"code": "2454", "name": "聯發科", "weight": 0.1215,
             "close": 5285, "prev_close": 5185, "ret": 0.0193, "contrib": 0.00234},
        ],
        "status": "ok", "as_of": "2026-09-24", "source": "t", "note": "",
    }
    r = result_for(rubric, anchors, "2026-09-24")
    s = summary.build(rubric, r, None, fields, META)
    ev = "".join(s["evidence"])
    assert "台積電" in ev and "聯發科" in ev


def test_text_is_assembled_from_the_parts(rubric, anchors):
    cur = result_for(rubric, anchors, "2026-09-24")
    prev = result_for(rubric, anchors, "2026-09-03")
    s = summary.build(rubric, cur, prev, as_fields(anchors, "2026-09-24"),
                      META, prev_date="2026-09-03")
    assert s["headline"] in s["text"]
    for part in s["evidence"]:
        assert part in s["text"]
    assert json.dumps(s, ensure_ascii=False)     # 必須可序列化進快照
