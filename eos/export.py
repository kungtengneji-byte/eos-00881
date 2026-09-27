"""把每次收集的結果另外輸出成 CSV，供下載與離線保存。

## 為什麼是 CSV 而不是 xlsx

這個專案的執行期相依只有 PyYAML，圖表是手寫 SVG、HTTP 用 stdlib。
為了產一個 .xlsx 去裝 openpyxl，等於為了「能用 Excel 開」而在收集器上
多掛一個相依 —— CSV 本來就能用 Excel 開，不值得。

## BOM 是必要的，不是裝飾

Excel 開 CSV 時若沒有 UTF-8 BOM，會用系統 ANSI 編碼解讀，中文全部變亂碼。
因此一律用 utf-8-sig 寫出。用程式讀的話 Python 的 csv 模組同樣以
utf-8-sig 開啟即可，BOM 會被自動吃掉。

## 備份的真正來源是 git

每次收集都會 commit 回 repo，所以**每一個歷史版本都留著**，
這裡輸出的 CSV 只是「現在這一份的可讀格式」。要回到某一天的狀態，
看那一天的 commit 即可，不需要另外保存一堆檔案。
"""

from __future__ import annotations

import csv
import io
import json
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterable, Sequence

ROOT = Path(__file__).resolve().parent.parent
EXPORTS = ROOT / "exports"

# Excel 不認 UTF-8 無 BOM 的 CSV，中文會整片變亂碼
ENCODING = "utf-8-sig"


def write_csv(path: Path, header: Sequence[str],
              rows: Iterable[Sequence[Any]]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    buf = io.StringIO(newline="")
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(header)
    for r in rows:
        w.writerow(["" if v is None else v for v in r])
    path.write_text(buf.getvalue(), encoding=ENCODING)
    return path


def _load(path: Path) -> Any:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


# ---------------------------------------------------------------- 各分頁

def export_history(instrument: str, dims: Sequence[tuple[str, str]],
                   extra: Sequence[tuple[str, str]], label: str) -> Path | None:
    """每日分數與分項的時間序列。"""
    rows = _load(ROOT / "data" / f"eos_history_{instrument}.json")
    if not rows:
        return None
    header = (["交易日", label, "評級", "覆蓋率", "資料狀態"]
              + [n for _, n in dims] + [n for _, n in extra])
    out = []
    for r in rows:
        line = [r.get("date"), r.get("eos"), r.get("rating"),
                r.get("coverage"), r.get("status")]
        line += [r.get(k) for k, _ in dims]
        line += [r.get(k) for k, _ in extra]
        out.append(line)
    return write_csv(EXPORTS / f"{instrument}_每日分數.csv", header, out)


def export_fields(instrument: str) -> Path | None:
    """每日每個欄位的值、來源與狀態 —— 這是最完整的一份，可用來重建任何結論。"""
    daily = ROOT / "data" / "daily" / instrument
    if not daily.exists():
        return None
    out: list[list[Any]] = []
    for p in sorted(daily.glob("*.json")):
        snap = _load(p) or {}
        for name, f in sorted((snap.get("fields") or {}).items()):
            v = f.get("value")
            if isinstance(v, (list, dict)):
                v = json.dumps(v, ensure_ascii=False)   # 結構化欄位保留原樣
            out.append([snap.get("trade_date"), name, v, f.get("status"),
                        f.get("source"), f.get("as_of"), f.get("note")])
    if not out:
        return None
    return write_csv(EXPORTS / f"{instrument}_每日欄位明細.csv",
                     ["交易日", "欄位", "值", "狀態", "來源", "資料時點", "備註"], out)


def export_levels(instrument: str) -> Path | None:
    """最新一日的壓力與支撐點位。"""
    daily = ROOT / "data" / "daily" / instrument
    files = sorted(daily.glob("*.json")) if daily.exists() else []
    for p in reversed(files):
        snap = _load(p) or {}
        lv = ((snap.get("eos") or {}).get("levels")) or {}
        if not lv.get("rows"):
            continue
        close = lv.get("latest_close")
        out = [[lv.get("as_of"), r.get("label"), r.get("kind"), r.get("value"),
                r.get("gap_pct"), r.get("date"), r.get("basis")]
               for r in lv["rows"]]
        out.append([lv.get("as_of"), "最新收盤", "", close, 0, lv.get("as_of"), ""])
        out.sort(key=lambda r: -(r[3] or 0))
        return write_csv(EXPORTS / f"{instrument}_壓力與支撐.csv",
                         ["資料日", "點位", "成因", "指數", "距現價", "依據日", "依據"], out)
    return None


def export_streaks() -> Path | None:
    """連續買賣超 Top5（含所有候補，不只前五名）。"""
    rep = _load(ROOT / "data" / "top5_streaks.json")
    if not rep:
        return None
    out: list[list[Any]] = []
    for key, block in (rep.get("institutions") or {}).items():
        for side, side_label in (("buy", "連續買超"), ("sell", "連續賣超")):
            for i, r in enumerate(block.get(side) or [], start=1):
                out.append([rep.get("as_of"), block.get("label"), side_label, i,
                            r.get("code"), r.get("name"),
                            r.get("institution_label"), r.get("days"),
                            "是" if r.get("truncated") else "",
                            r.get("status_label"), r.get("peers"),
                            r.get("lots"), r.get("price"), r.get("amount_100m"),
                            r.get("score"), r.get("start"), r.get("end"),
                            "是" if r.get("etf") else ""])
    if not out:
        return None
    return write_csv(EXPORTS / "連續買賣超.csv",
                     ["資料日", "分頁", "方向", "名次", "代號", "名稱", "主導法人",
                      "連續天數", "連到視窗底", "狀態", "同向法人數", "累計(張)",
                      "參考價", "約當金額(億)", "分數", "起始日", "迄日", "ETF"], out)


def export_sectors() -> Path | None:
    """產業資金輪動：每日 × 每個上榜產業一列。"""
    rep = _load(ROOT / "data" / "sector_flow.json")
    if not rep or not rep.get("rows"):
        return None
    out: list[list[Any]] = []
    for row in rep["rows"]:
        for side, label in (("inflow", "流入"), ("outflow", "流出")):
            for rank, s in enumerate(row.get(side) or [], start=1):
                top = "、".join(f"{t['name']}({t['lots']:+,.0f}張)"
                                for t in s.get("top") or [])
                out.append([row["date"], row.get("leading_label"), label, rank,
                            s.get("label"), s.get("amount_100m"), s.get("lots"),
                            s.get("stocks"), s.get("confirm_ratio"), top,
                            row.get("read")])
    if not out:
        return None
    return write_csv(EXPORTS / "產業資金輪動.csv",
                     ["資料日", "主導法人", "方向", "名次", "產業", "金額(億)",
                      "淨額(張)", "檔數", "價格同向比例", "代表股", "判讀"], out)


def export_stock_detail() -> Path | None:
    """候選標的的逐日三大法人明細，對齊工作表〈個股法人明細〉。"""
    rep = _load(ROOT / "data" / "stock_detail.json")
    if not rep or not rep.get("stocks"):
        return None
    out: list[list[Any]] = []
    for s in rep["stocks"]:
        for r in s.get("rows") or []:
            if r.get("missing"):
                out.append([r["date"], s["code"], s["name"]] + [""] * 9
                           + ["當日無法人交易紀錄"])
                continue
            out.append([r["date"], s["code"], s["name"],
                        r["foreign"], r["trust"], r["dealer"],
                        r["sum_lots"], r["total"], r["check_diff"],
                        r["run_foreign"], r["run_trust"], r["run_dealer"],
                        r["run_total"], rep.get("source", "")])
    if not out:
        return None
    return write_csv(EXPORTS / "個股法人明細.csv",
                     ["日期", "代號", "名稱", "外資(張)", "投信(張)", "自營商(張)",
                      "三大合計(公式)", "合計(來源)", "檢核差",
                      "外資連續", "投信連續", "自營連續", "合計連續", "備註／來源"],
                     out)


def export_us_map() -> Path | None:
    """美股映射：每個台股日配上它實際對應的美股時段與兩邊結果。"""
    rep = _load(ROOT / "data" / "us_map.json")
    if not rep or not rep.get("rows"):
        return None
    shared = set(rep.get("shared_us_sessions") or [])
    us_cols = [c[0] for c in (rep.get("fields") or {}).get("us", [])]
    tw_cols = [c[0] for c in (rep.get("fields") or {}).get("tw", [])]
    head = (["台股日", "美股時段", "美股休市順延", "跨時段欄位"]
            + [c[1] for c in (rep.get("fields") or {}).get("us", [])]
            + [c[1] for c in (rep.get("fields") or {}).get("tw", [])]
            + ["費半方向", "加權方向", "同向"])
    out: list[list[Any]] = []
    for r in rep["rows"]:
        off = r.get("off_session") or []
        out.append([r["tw_date"], r.get("us_date"),
                    "是" if r.get("us_date") in shared else "",
                    "、".join(f"{k}={(r.get('us_as_of') or {}).get(k)}" for k in off)]
                   + [(r.get("us") or {}).get(c) for c in us_cols]
                   + [(r.get("tw") or {}).get(c) for c in tw_cols]
                   + [r.get("sox_dir"), r.get("taiex_dir"),
                      "" if r.get("agree") is None else ("是" if r["agree"] else "否")])
    return write_csv(EXPORTS / "美股映射.csv", head, out)


# ---------------------------------------------------------------- 總入口

DIMS_00881 = [("A", "A 價格/折溢價"), ("B", "B 含息趨勢/回檔"), ("C", "C 成分股廣度"),
              ("D", "D 海外科技"), ("E", "E 波動/風險"), ("F", "F 量價/法人")]
EXTRA_00881 = [("close", "收盤"), ("premium", "折溢價"), ("rsi14", "RSI14"),
               ("volume_ratio", "量比")]
DIMS_MARKET = [("LIGHT", "燈號得分")]
EXTRA_MARKET = [("taiex", "加權指數"), ("taiex_change_pct", "漲跌幅"),
                ("market_foreign_net_100m", "外資買賣超(億)"),
                ("foreign_futures_net_oi", "外資期貨淨OI(口)"),
                ("margin_balance_100m", "融資餘額(億)")]


def export_washout(instrument: str = "TWMARKET") -> Path | None:
    """洗盤假說的逐日總分與七個構面得分。

    每天一列、構面各一欄。缺值構面留空而不是 0 —— 匯出檔要能直接看出
    「這一天這個構面沒有資料」，填 0 會在 Excel 裡變成「拿了 0 分」。
    """
    daily = ROOT / "data" / "daily" / instrument
    if not daily.exists():
        return None
    keys = ["W1", "W2", "W3", "W4", "W5", "W6", "W7"]
    names: dict[str, str] = {}
    rows: list[list[Any]] = []
    for p in sorted(daily.glob("*.json")):
        snap = _load(p) or {}
        w = ((snap.get("eos") or {}).get("washout")) or {}
        if not w:
            continue
        dims = w.get("dimensions") or {}
        for k in keys:
            if k in dims and k not in names:
                names[k] = dims[k].get("name") or k
        rows.append([snap.get("trade_date"),
                     w.get("eos") if w.get("published") else None,
                     w.get("rating") if w.get("published") else "未出分",
                     w.get("available"), w.get("coverage_status")]
                    + [(dims.get(k) or {}).get("earned") for k in keys])
    if not rows:
        return None
    head = (["資料日", "洗盤支持度", "分類", "可得滿分", "覆蓋率狀態"]
            + [f"{k} {names.get(k, k)}" for k in keys])
    return write_csv(EXPORTS / f"{instrument}_洗盤假說.csv", head, rows)


def export_all() -> list[Path]:
    """輸出全部 CSV，回傳實際產生的檔案清單。"""
    made: list[Path] = []
    for p in (export_history("00881", DIMS_00881, EXTRA_00881, "EOS"),
              export_fields("00881"),
              export_history("TWMARKET", DIMS_MARKET, EXTRA_MARKET, "燈號"),
              export_fields("TWMARKET"),
              export_levels("TWMARKET"),
              export_washout("TWMARKET"),
              export_streaks(),
              export_sectors(),
              export_stock_detail(),
              export_us_map()):
        if p is not None:
            made.append(p)

    # 索引檔：前端要列出可下載的檔案與大小，不必自己維護一份清單。
    # 先建目錄 —— 全新的 repo 一份都產不出來時，write_csv 沒跑過，
    # 目錄也就不存在，直接寫索引會 FileNotFoundError。
    EXPORTS.mkdir(parents=True, exist_ok=True)
    index = [{"file": p.name,
              "bytes": p.stat().st_size,
              "rows": max(0, p.read_text(encoding=ENCODING).count("\n") - 1)}
             for p in made]
    (EXPORTS / "index.json").write_text(
        json.dumps({"generated_at": datetime.now().isoformat(timespec="seconds"),
                    "files": index}, ensure_ascii=False, indent=1),
        encoding="utf-8")
    return made
