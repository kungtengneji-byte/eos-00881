"""CSV 備份輸出的測試。

重點只有三件事：Excel 打得開（BOM）、結構化欄位不會變成 [object Object]、
以及索引檔與實際產生的檔案一致。
"""

from __future__ import annotations

import csv
import io
import json
from pathlib import Path

import pytest

from eos import export


@pytest.fixture
def fake_root(tmp_path, monkeypatch):
    monkeypatch.setattr(export, "ROOT", tmp_path)
    monkeypatch.setattr(export, "EXPORTS", tmp_path / "exports")
    return tmp_path


def test_csv_has_a_bom_so_excel_reads_chinese(tmp_path):
    """沒有 BOM 的 UTF-8 CSV，Excel 會用系統 ANSI 解讀，中文全部變亂碼。"""
    p = export.write_csv(tmp_path / "a.csv", ["代號", "名稱"], [["2330", "台積電"]])
    raw = p.read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf")
    # 用 utf-8-sig 讀回來 BOM 會被吃掉，欄名不該帶著它
    rows = list(csv.reader(io.StringIO(p.read_text(encoding="utf-8-sig"))))
    assert rows[0] == ["代號", "名稱"]
    assert rows[1] == ["2330", "台積電"]


def test_none_becomes_empty_not_the_string_none(tmp_path):
    """缺值要留白。寫成 'None' 之後在 Excel 裡會被當成一個字串值。"""
    p = export.write_csv(tmp_path / "b.csv", ["a", "b"], [[None, 1]])
    assert p.read_text(encoding="utf-8-sig").splitlines()[1] == ",1"


def test_structured_fields_are_kept_as_json(fake_root):
    """top_holdings 這種陣列欄位要保留內容，不能變成 [object Object] 那類垃圾。"""
    daily = fake_root / "data" / "daily" / "X"
    daily.mkdir(parents=True)
    (daily / "2026-09-24.json").write_text(json.dumps({
        "trade_date": "2026-09-24",
        "fields": {"top_holdings": {"value": [{"name": "台積電", "ret": 0.01}],
                                    "status": "ok", "source": "t"}},
    }, ensure_ascii=False), encoding="utf-8")

    p = export.export_fields("X")
    body = p.read_text(encoding="utf-8-sig")
    assert "台積電" in body
    assert "[object" not in body and "None" not in body


def test_export_all_writes_an_index_matching_the_files(fake_root):
    data = fake_root / "data"
    data.mkdir()
    (data / "eos_history_00881.json").write_text(json.dumps(
        [{"date": "2026-09-24", "eos": 58, "rating": "中性等待",
          "coverage": 100, "status": "confirmed", "A": 13.0, "close": 51.2}]),
        encoding="utf-8")

    made = export.export_all()
    assert made, "有歷史資料就該產生檔案"
    idx = json.loads((export.EXPORTS / "index.json").read_text(encoding="utf-8"))
    assert {f["file"] for f in idx["files"]} == {p.name for p in made}
    for f in idx["files"]:
        assert f["bytes"] > 0
        assert f["rows"] >= 1, "rows 不含表頭，至少要有一列資料"


def test_export_all_is_silent_when_there_is_nothing_to_export(fake_root):
    """全新的 repo 還沒收集過，不該爆掉，也不該產生空殼檔。"""
    assert export.export_all() == []
    idx = json.loads((export.EXPORTS / "index.json").read_text(encoding="utf-8"))
    assert idx["files"] == []


def test_levels_export_inserts_the_latest_close_in_price_order(fake_root):
    daily = fake_root / "data" / "daily" / "TWMARKET"
    daily.mkdir(parents=True)
    (daily / "2026-09-24.json").write_text(json.dumps({
        "trade_date": "2026-09-24",
        "eos": {"levels": {"as_of": "2026-09-24", "latest_close": 100.0, "rows": [
            {"label": "壓力", "kind": "區間", "value": 110.0, "gap_pct": 0.1,
             "date": "2026-09-22", "basis": "x"},
            {"label": "支撐", "kind": "K線", "value": 90.0, "gap_pct": -0.1,
             "date": "2026-09-21", "basis": "y"},
        ]}},
    }, ensure_ascii=False), encoding="utf-8")

    p = export.export_levels("TWMARKET")
    rows = list(csv.reader(io.StringIO(p.read_text(encoding="utf-8-sig"))))[1:]
    assert [r[1] for r in rows] == ["壓力", "最新收盤", "支撐"]
