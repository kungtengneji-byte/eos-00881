"""每日收集協調器。

  python collect.py --window w1              # 台股收盤窗
  python collect.py --window w2 --date 2026-09-24
  python collect.py --window w4              # 回補過去 N 天的缺值
  python collect.py --window all             # 一次跑完（手動補救用）

四個窗對應資料實際可得的時間，不是硬要湊整點：

  W1 交易日 15:10   TWSE 收盤（00881 與 Top10 成分股）、三大法人
  W2 交易日 19:30   國泰投信正式 NAV 與成分股權重（常延遲，21:30 再試一次）
  W3 次日 05:30     美股前一時段收盤與風險指標
  W4 次日 09:00     回補過去 N 天仍缺的欄位

設計原則：
  * 每個部件（part）獨立 try/except。一個來源掛掉只讓該部件的欄位變成
    UNAVAILABLE，其餘照常收 —— 不讓單一來源拖垮整天的收集。
  * 所有部件都是冪等的。重跑同一個窗不會造成重複或資料損壞。
  * 寫入一律走 store.merge_fields，永不把已取得的值降級成缺值。
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Callable

import yaml

from eos import engine, series as series_mod, store, summary
from eos.models import Field, Status
from eos.rubric import Rubric
from sources import cathay, twse, yahoo
from sources.base import SourceError, fetch_json

ROOT = Path(__file__).resolve().parent
RAW = ROOT / "raw"
CONFIG_DIR = ROOT / "config" / "instruments"

PARTS_BY_WINDOW = {
    "w1": ("prices", "institutional"),
    "w2": ("nav", "constituents"),
    "w3": ("overseas",),
}


# ---------------------------------------------------------------- 設定

def load_config(instrument: str) -> dict[str, Any]:
    path = CONFIG_DIR / f"{instrument}.yaml"
    if not path.exists():
        raise SystemExit(f"找不到標的設定：{path}")
    return yaml.safe_load(path.read_text(encoding="utf-8"))


# ---------------------------------------------------------------- 原始資料快取

def month_cache_path(stock_no: str, yyyymm: str) -> Path:
    return RAW / "twse" / stock_no / f"STOCK_DAY_{yyyymm}.json"


# TWSE 對連續請求會限流。C 構面一次要抓 10 檔成分股，沒有節流會被擋，
# 而且被擋的表現是回傳空資料而不是錯誤 —— 那會靜默地變成「成分股無報酬」。
TWSE_MIN_INTERVAL = 3.0
_last_twse_call = 0.0


def _throttle_twse() -> None:
    global _last_twse_call
    wait = TWSE_MIN_INTERVAL - (time.monotonic() - _last_twse_call)
    if wait > 0:
        time.sleep(wait)
    _last_twse_call = time.monotonic()


def ensure_month(stock_no: str, day: date, *, force: bool) -> None:
    """抓取並快取該月日線。當月檔會持續增長，所以預設重抓當月。"""
    yyyymm = f"{day:%Y%m}"
    path = month_cache_path(stock_no, yyyymm)
    if path.exists() and not force:
        return
    _throttle_twse()
    payload = fetch_json(twse.stock_day_url(stock_no, yyyymm))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def load_all_bars(stock_no: str) -> list:
    bars = []
    d = RAW / "twse" / stock_no
    for f in sorted(d.glob("STOCK_DAY_*.json")):
        bars.extend(twse.parse_stock_day(json.loads(f.read_text(encoding="utf-8")),
                                         url=str(f)))
    return bars


def load_dividends(stock_no: str) -> dict[date, float]:
    p = RAW / "twse" / stock_no / "EXRIGHT.json"
    if not p.exists():
        return {}
    return twse.parse_dividends(json.loads(p.read_text(encoding="utf-8")),
                                stock_no, url=str(p))


# ---------------------------------------------------------------- 部件

def part_prices(cfg: dict, day: date, *, force: bool) -> dict[str, Field]:
    """00881 自身的收盤與所有含息技術指標（B 構面 + F1 量比）。"""
    inst = cfg["instrument"]
    ensure_month(inst, day, force=force)
    bars = load_all_bars(inst)
    rows = series_mod.build(bars, load_dividends(inst))
    idx = {r["date"]: i for i, r in enumerate(rows)}
    key = day.isoformat()
    src = "TWSE STOCK_DAY"
    url = twse.stock_day_url(inst, f"{day:%Y%m}")

    if key not in idx:
        note = "TWSE 尚未發布該日資料（休市或收盤資料未更新）"
        return {n: Field.missing(n, source=src, url=url, note=note)
                for n in ("close", "volume_lots", "turnover_100m", "rsi14", "rv20",
                          "drawdown20", "volume_ratio", "day_direction",
                          "close_adj_gt_ma20", "ma20_gt_ma60",
                          "close_adj_gt_ma120", "ret60_positive")}

    i = idx[key]
    row = rows[i]
    values: dict[str, Any] = {
        "close": row["close"], "open": row["open"], "high": row["high"], "low": row["low"],
        "volume_lots": row["volume_lots"], "turnover_100m": row["turnover_100m"],
        "close_adj": row["close_adj"], "ma20": row["ma20"], "ma60": row["ma60"],
        "ma120": row["ma120"], "ret5": row["ret5"], "ret20": row["ret20"],
        "ret60": row["ret60"], "drawdown20": row["drawdown20"],
        "drawdown60": row["drawdown60"], "rsi14": row["rsi14"], "rv20": row["rv20"],
        "avg_vol20": row["avg_vol20"],
        **series_mod.to_rubric_inputs(rows, i),
    }
    out: dict[str, Field] = {}
    for name, value in values.items():
        if value is None:
            out[name] = Field.missing(name, source=src, url=url, note="序列長度不足或該欄無值")
        else:
            out[name] = Field(name=name, value=value, source=src, url=url,
                              as_of=key, status=Status.OK)
    return out


def part_institutional(cfg: dict, day: date, **_: Any) -> dict[str, Field]:
    """三大法人（F2）與該標的自身的法人買賣超。"""
    out = twse.fetch_institutional(day)
    # rubric 的 F2 讀 institutional_net（億元）
    total = out["institutional_net_100m"]
    out["institutional_net"] = Field(
        name="institutional_net", value=total.value, source=total.source,
        url=total.url, as_of=total.as_of, status=total.status, note=total.note)
    try:
        out.update(twse.fetch_stock_institutional(cfg["instrument"], day))
    except SourceError as exc:
        for n in ("stock_foreign_net_lots", "stock_institutional_net_lots"):
            out[n] = Field.unavailable(n, "TWSE T86", twse.stock_institutional_url(day), str(exc))
    return out


def part_nav(cfg: dict, day: date, **_: Any) -> dict[str, Field]:
    """國泰投信正式 NAV 與折溢價（A 構面）。回傳 30 個交易日，順帶回補近期缺漏。"""
    history, url = cathay.fetch_nav_history(cfg["instrument"])
    _cache(RAW / "cathay" / cfg["instrument"] / f"nav30_{day.isoformat()}.json", history_json(history))
    return cathay.nav_fields(history, day, url=url)


def history_json(history: dict[date, dict[str, float | None]]) -> dict[str, Any]:
    return {d.isoformat(): v for d, v in sorted(history.items())}


def _cache(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")


def part_constituents(cfg: dict, day: date, *, force: bool) -> dict[str, Field]:
    """Top10 加權報酬貢獻（WCR）與上漲家數（C 構面）。

    權重來自國泰投信當日公告，個股報酬來自 TWSE —— 兩邊都是官方，
    不使用第三方彙整網站，避免權重基準日與報價來源不一致。
    """
    inst = cfg["instrument"]
    top_n = int(cfg.get("top_n", 10))
    as_of, holdings, url = cathay.fetch_weights(inst)
    if not holdings:
        note = "國泰投信未回傳成分股權重"
        return {n: Field.missing(n, source="國泰投信 cwapi", url=url, note=note)
                for n in ("wcr", "breadth_count")}

    top = cathay.top_n(holdings, top_n)
    _cache(RAW / "cathay" / inst / f"weights_{day.isoformat()}.json",
           {"as_of": as_of.isoformat() if as_of else None,
            "holdings": [{"code": h.code, "name": h.name, "weight": h.weight} for h in top]})

    breakdown, details = holdings_breakdown(top, day, force=force)
    scored = len(breakdown)
    wcr = sum(b["contrib"] for b in breakdown)
    up = sum(1 for b in breakdown if b["ret"] > 0)

    src = "國泰投信權重 + TWSE 個股收盤"
    if scored < top_n:
        note = f"僅 {scored}/{top_n} 檔取得報酬：" + "；".join(details[-3:])
        return {
            "wcr": Field.missing("wcr", source=src, url=url, note=note),
            "breadth_count": Field.missing("breadth_count", source=src, url=url, note=note),
        }
    # 國泰投信只提供「當日」權重快照，沒有歷史權重。回填歷史日時只能沿用
    # 目前的權重去套當天的個股報酬 —— 那是估計值，不是那天的實際加權貢獻。
    # 標成 STALE 而不是 OK：值仍可計分，但 UI 必須看得出它不是當日權重。
    stale = as_of is not None and as_of != day
    status = Status.STALE if stale else Status.OK
    note = f"權重基準日 {as_of.isoformat() if as_of else '未知'}"
    if stale:
        note += f"，非 {day.isoformat()} 當日權重；歷史日的加權貢獻為估計值"

    as_of_str = (as_of or day).isoformat()
    return {
        "wcr": Field(name="wcr", value=wcr, source=src, url=url,
                     as_of=as_of_str, status=status, note=note),
        "breadth_count": Field(name="breadth_count", value=up, source=src, url=url,
                               as_of=as_of_str, status=status, note=note),
        # 保留每一檔的分解，前端才做得出「前十大持股」明細表。
        # 只存彙總數字的話，看到 WCR -0.24% 也不知道是誰拖累的。
        "top_holdings": Field(name="top_holdings", value=breakdown, source=src, url=url,
                              as_of=as_of_str, status=status, note=note),
    }


def holdings_breakdown(top, day: date, *, force: bool) -> tuple[list[dict[str, Any]], list[str]]:
    """逐檔算出當日漲跌幅與權重貢獻。回傳 (明細, 問題紀錄)。"""
    rows: list[dict[str, Any]] = []
    problems: list[str] = []
    for h in top:
        try:
            ensure_month(h.code, day, force=force)
            bars = load_all_bars(h.code)
        except SourceError as exc:
            problems.append(f"{h.code} 取得失敗：{exc}")
            continue
        by_date = {b.date: b for b in bars}
        dates = sorted(by_date)
        if day not in by_date:
            problems.append(f"{h.code} 無 {day} 收盤")
            continue
        i = dates.index(day)
        if i == 0:
            problems.append(f"{h.code} 無前一交易日可比")
            continue
        prev, cur = by_date[dates[i - 1]].close, by_date[day].close
        if not prev or cur is None:
            problems.append(f"{h.code} 收盤價缺值")
            continue
        r = cur / prev - 1
        rows.append({
            "code": h.code, "name": h.name, "weight": h.weight,
            "close": cur, "prev_close": prev, "ret": r,
            "contrib": h.weight * r,
        })
    return rows, problems


def part_overseas(cfg: dict, day: date, **_: Any) -> dict[str, Field]:
    """海外科技（D）與風險環境（E）。一律取台股日 T-1 的美股時段。"""
    out: dict[str, Field] = {}
    for name in yahoo.CHANGE_FIELDS:
        out[name] = _safe_yahoo(name, day, as_change=True)
    for name in ("vix", "us10y"):
        out[name] = _safe_yahoo(name, day, as_change=False)

    # 匯率同時需要水準值與日變動；E3 讀的是變動
    symbol = yahoo.SYMBOLS["usdtwd"]
    url = yahoo.chart_url(symbol)
    try:
        s = yahoo.parse_chart(fetch_json(url), url=url)
        out["usdtwd"] = yahoo.to_field("usdtwd", symbol, s, as_change=False,
                                       target_day=day, url=url)
        out["twd_change"] = yahoo.to_field("twd_change", symbol, s, as_change=True,
                                           target_day=day, url=url)
    except SourceError as exc:
        for n in ("usdtwd", "twd_change"):
            out[n] = Field.unavailable(n, f"Yahoo Finance {symbol}", url, str(exc))
    return out


def _safe_yahoo(name: str, day: date, *, as_change: bool) -> Field:
    symbol = yahoo.SYMBOLS[name]
    url = yahoo.chart_url(symbol)
    try:
        return yahoo.fetch_field(name, day, as_change=as_change)
    except SourceError as exc:
        return Field.unavailable(name, f"Yahoo Finance {symbol}", url, str(exc))


PARTS: dict[str, Callable[..., dict[str, Field]]] = {
    "prices": part_prices,
    "institutional": part_institutional,
    "nav": part_nav,
    "constituents": part_constituents,
    "overseas": part_overseas,
}


# ---------------------------------------------------------------- 執行

def run_parts(cfg: dict, day: date, parts: tuple[str, ...], *, force: bool) -> dict[str, Field]:
    collected: dict[str, Field] = {}
    for name in parts:
        try:
            got = PARTS[name](cfg, day, force=force)
            collected.update(got)
            ok = sum(1 for f in got.values() if f.usable)
            print(f"  [{name}] {ok}/{len(got)} 欄位取得")
        except SourceError as exc:
            # 單一來源失敗不影響其他部件；該部件的欄位留給下次回補
            print(f"  [{name}] 來源失敗：{exc}")
        except Exception as exc:                      # noqa: BLE001
            print(f"  [{name}] 非預期錯誤：{type(exc).__name__}: {exc}")
    return collected


def score_and_save(cfg: dict, day: date, rubric: Rubric,
                   collected: dict[str, Field], window: str) -> dict[str, Any]:
    inst = cfg["instrument"]
    existing = store.load(inst, day) or {}
    fields, changed = store.merge_fields(existing.get("fields", {}), collected)

    inputs = {name: f.get("value") for name, f in fields.items()
              if f.get("status") in ("ok", "stale")}
    result = engine.compute(rubric, inputs)

    # 與前一個「有發布分數」的交易日比較，不是單純的前一天 ——
    # 前一天可能因覆蓋率不足而未出分，拿它比會得到假的變化
    prev_result = prev_date = None
    for d in store.recent_days(inst, 10):
        if d >= day:
            continue
        snap = store.load(inst, d) or {}
        pf = snap.get("fields") or {}
        pin = {n: f.get("value") for n, f in pf.items()
               if f.get("status") in ("ok", "stale")}
        pr = engine.compute(rubric, pin)
        if pr.published:
            prev_result, prev_date = pr, d.isoformat()
            break

    meta_path = store.write_instrument_meta(cfg)
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    payload = result.to_dict()
    payload["summary"] = summary.build(rubric, result, prev_result, fields,
                                       meta=meta, prev_date=prev_date)

    store.save(inst, day, fields=fields, eos=payload, windows=[window])
    print(f"  更新 {len(changed)} 個欄位" + (f"：{', '.join(changed[:6])}" if changed else ""))
    print(result.explain())

    still_missing = store.missing_fields({"fields": fields}, rubric.required_inputs())
    if still_missing:
        print(f"  仍缺：{', '.join(still_missing)}")
    return result.to_dict()


def backfill(cfg: dict, rubric: Rubric, days: int, *, force: bool) -> None:
    """回補過去 N 天仍有缺值的快照。

    這是「當天留白、隔天補回」的執行者。NAV 晚發布、美股休市、
    來源暫時掛掉，都靠這個窗把歷史補完整，而不是永久留白。
    """
    inst = cfg["instrument"]
    targets = store.recent_days(inst, days)
    if not targets:
        print("  沒有既有快照可回補")
        return
    required = rubric.required_inputs()
    for day in targets:
        snap = store.load(inst, day) or {}
        missing = store.missing_fields(snap, required)
        if not missing:
            continue
        print(f"\n回補 {day}（缺 {len(missing)} 欄：{', '.join(missing[:5])}）")
        parts = tuple(dict.fromkeys(
            p for w in ("w1", "w2", "w3") for p in PARTS_BY_WINDOW[w]))
        collected = run_parts(cfg, day, parts, force=force)
        score_and_save(cfg, day, rubric, collected, "w4")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="00881 EOS 每日收集")
    ap.add_argument("--instrument", default="00881")
    ap.add_argument("--window", required=True, choices=["w1", "w2", "w3", "w4", "all"])
    ap.add_argument("--date", help="目標交易日 YYYY-MM-DD，預設為今天")
    ap.add_argument("--force-refetch", action="store_true",
                    help="即使已有月檔快取也重抓")
    args = ap.parse_args(argv)

    cfg = load_config(args.instrument)
    rubric = Rubric.load()
    day = date.fromisoformat(args.date) if args.date else date.today()
    force = args.force_refetch or args.window in ("w1", "all")

    print(f"標的 {cfg['instrument']}  目標日 {day}  窗 {args.window}  "
          f"rubric {rubric.version}")

    if args.window == "w4":
        backfill(cfg, rubric, int(cfg.get("backfill_days", 7)), force=force)
        store.write_series_index(cfg["instrument"])
        return 0

    windows = ("w1", "w2", "w3") if args.window == "all" else (args.window,)
    parts = tuple(dict.fromkeys(p for w in windows for p in PARTS_BY_WINDOW[w]))
    collected = run_parts(cfg, day, parts, force=force)
    if not collected:
        print("  沒有取得任何欄位，不寫入快照")
        return 1
    score_and_save(cfg, day, rubric, collected, args.window)
    store.write_series_index(cfg["instrument"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
