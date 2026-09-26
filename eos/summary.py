"""由計分結果與當日欄位產生「今日結論」。

對齊既有工作表最下方那段敘述：今天幾分、較前一日變化、哪些構面改善、
哪些拖累、證據是什麼、資料有什麼缺口、還要等什麼條件。

原則是**只說資料支持的話**：
  * 前一日不可比時（覆蓋率不同、信心等級不同）就明說不可比，不硬算變化
  * 每個結論都附具體數字，不寫「表現不佳」這種沒有內容的形容
  * 資料品質問題（缺值、非當日權重）一律點出，不藏在細節裡
"""

from __future__ import annotations

from typing import Any

from eos.engine import EosResult, compare
from eos.rubric import Rubric

# 分數的名稱依模型而異：00881 算的是 EOS，大盤算的是「外資續買燈號」。
# 以 rubric 版本字串的前綴對應，新增模型只要加一行。
SCORE_LABEL = {"market": "燈號"}

DIM_NAME = {
    "A": "價格/折溢價", "B": "含息趨勢/回檔", "C": "成分股廣度",
    "D": "海外科技", "E": "波動/風險", "F": "量價/法人",
}

# 各構面用來佐證的欄位
EVIDENCE_FIELDS = {
    "A": ["premium"],
    "B": ["rsi14", "drawdown20", "rv20"],
    "C": ["wcr", "breadth_count"],
    "D": ["sox_ret", "ndx_ret", "tsm_ret", "nvda_ret"],
    "E": ["vix", "us10y", "twd_change"],
    "F": ["volume_ratio", "institutional_net"],
}

PCT = {"premium", "wcr", "sox_ret", "ndx_ret", "tsm_ret", "nvda_ret",
       "twd_change", "drawdown20", "rv20",
       "gap_to_resistance_pct", "sox_prev_session_ret"}

# 以「億元」計的欄位，正負號要顯示 —— 淨買賣超看不到方向就沒有意義
HUNDRED_M = {"institutional_net", "wave_cumulative_100m", "margin_change_2d_100m",
             "market_foreign_net_100m"}

LABEL = {
    "premium": "折溢價", "rsi14": "RSI14", "drawdown20": "20日回檔",
    "rv20": "RV20", "wcr": "Top10加權貢獻", "breadth_count": "Top10上漲家數",
    "sox_ret": "SOX", "ndx_ret": "Nasdaq", "tsm_ret": "TSM ADR", "nvda_ret": "NVDA",
    "vix": "VIX", "us10y": "US10Y", "twd_change": "台幣日變動",
    "volume_ratio": "量比", "institutional_net": "三大法人",
    # 大盤燈號（market_rubric_v1.0）的七個輸入
    "wave_days": "本波連買天數", "wave_cumulative_100m": "本波累計",
    "foreign_futures_oi_change_5d": "外資期貨5日變化",
    "gap_to_resistance_pct": "距前高", "margin_change_2d_100m": "融資2日變化",
    "sox_prev_session_ret": "費半（前一時段）",
    "market_foreign_net_100m": "外資現貨",
}


def _fmt(name: str, v: Any) -> str:
    if v is None:
        return "缺值"
    if isinstance(v, bool):
        return "是" if v else "否"
    if name in PCT:
        return f"{v * 100:+.2f}%"
    if name == "breadth_count":
        return f"{int(v)}/10 檔"
    if name == "volume_ratio":
        return f"{v:.2f}x"
    if name in HUNDRED_M:
        return f"{v:+.1f} 億元"
    if name == "foreign_futures_oi_change_5d":
        return f"{v:+,.0f} 口"
    if name == "wave_days":
        return f"{int(v)} 天"
    if isinstance(v, float):
        return f"{v:.2f}"
    return str(v)


def _evidence(dim: str, fields: dict[str, Any]) -> str:
    parts = []
    for name in EVIDENCE_FIELDS.get(dim, []):
        f = fields.get(name)
        if not f or f.get("value") is None:
            continue
        parts.append(f"{LABEL.get(name, name)} {_fmt(name, f['value'])}")
    return "、".join(parts)


def _display_names(rubric: Rubric, single: bool) -> dict[str, str]:
    """結論裡每一項的顯示名稱。

    單構面模型拆到子項，多構面模型仍以構面為單位。名稱一律取自 rubric，
    DIM_NAME 只是 00881 既有的簡稱 —— 新標的不必改這支程式就能有正確標題。
    """
    if single:
        dim = next(iter(rubric.dimensions.values()))
        return {it.id: it.name for it in dim.items}
    return {k: DIM_NAME.get(k) or d.name for k, d in rubric.dimensions.items()}


def _item_evidence(rubric: Rubric, item_id: str, fields: dict[str, Any]) -> str:
    """子項的佐證＝它自己的 input 當日值，不必另外維護一張對照表。"""
    for d in rubric.dimensions.values():
        for it in d.items:
            if it.id != item_id:
                continue
            if not it.input:
                return ""
            f = fields.get(it.input)
            if not f or f.get("value") is None:
                return ""
            return f"{LABEL.get(it.input, it.input)} {_fmt(it.input, f['value'])}"
    return ""


def _holdings_extremes(fields: dict[str, Any]) -> str:
    """指出 Top10 裡貢獻最大與最小的成分股 —— 只給 WCR 看不出是誰造成的。"""
    f = fields.get("top_holdings")
    rows = f.get("value") if f else None
    if not rows:
        return ""
    best = max(rows, key=lambda r: r["contrib"])
    worst = min(rows, key=lambda r: r["contrib"])
    if best["contrib"] <= 0 and worst["contrib"] >= 0:
        return ""
    bits = []
    if best["contrib"] > 0:
        bits.append(f"最大貢獻 {best['name']} {best['ret'] * 100:+.2f}%"
                    f"（{best['contrib'] * 100:+.3f}%）")
    if worst["contrib"] < 0:
        bits.append(f"最大拖累 {worst['name']} {worst['ret'] * 100:+.2f}%"
                    f"（{worst['contrib'] * 100:+.3f}%）")
    return "；".join(bits)


def _quality(result: EosResult, fields: dict[str, Any]) -> list[str]:
    out: list[str] = []
    missing = result.missing_summary()
    if missing:
        out.append(f"{len(missing)} 個子項缺值不計分（{', '.join(missing[:4])}），"
                   f"EOS 以可得滿分 {result.available:g} 標準化")
    stale = [LABEL.get(n, n) for n, f in sorted(fields.items())
             if f.get("status") == "stale" and n in LABEL]
    if stale:
        out.append(f"{'、'.join(stale)} 為較舊時點資料")
    hold = fields.get("top_holdings")
    if hold and hold.get("status") == "stale":
        out.append(f"成分股權重基準日為 {hold.get('as_of')}，非當日權重，加權貢獻為估計值")
    if result.tier.status == "provisional":
        out.append("覆蓋率未達 90，分數為暫定值，回補後會更新")
    return out


def _watch(rubric: Rubric, result: EosResult, fields: dict[str, Any],
           meta: dict[str, Any] | None) -> list[str]:
    out: list[str] = []
    if result.eos is not None:
        for b in sorted(rubric.rating_bands, key=lambda x: x.min):
            if b.min > result.eos:
                out.append(f"距「{b.label}」（{b.min:g} 分）尚差 {b.min - result.eos:g} 分")
                break
    ex = (meta or {}).get("ex_dividend") or {}
    close = (fields.get("close") or {}).get("value")
    if close and ex.get("full_recovery"):
        gap = ex["full_recovery"] / close - 1
        out.append("已完成填息" if gap <= 0
                   else f"距完整填息 {ex['full_recovery']} 元尚需 {gap * 100:.2f}%")
    nav = fields.get("nav")
    if nav is not None and nav.get("value") is None:
        out.append("當日正式 NAV 尚未發布，折溢價待回補")
    return out


def build(rubric: Rubric, result: EosResult, previous: EosResult | None,
          fields: dict[str, Any], meta: dict[str, Any] | None = None,
          prev_date: str | None = None) -> dict[str, Any]:
    """產生結構化結論。text 為給人讀的完整段落。"""
    if result.eos is None:
        what = SCORE_LABEL.get(rubric.version.split("-")[0], "EOS")
        headline = (f"當日可計分項目僅 {result.available:g}/100，依規則不計算 {what}；"
                    f"已取得的項目仍列於下方")
        quality = _quality(result, fields)
        return {"headline": headline, "drivers": [], "drags": [],
                "evidence": [], "quality": quality,
                "watch": _watch(rubric, result, fields, meta),
                "text": "　".join([headline] + quality)}

    # 單構面模型（大盤燈號）以子項為拆解單位；多構面模型（00881 EOS）以構面為單位。
    single = len(rubric.dimensions) == 1
    names = _display_names(rubric, single)
    delta_key = "item_delta" if single else "dimension_delta"
    if single:
        def ev_of(k: str) -> str:
            return _item_evidence(rubric, k, fields)
        fallback = list(names)[:3]
        label = SCORE_LABEL.get(rubric.version.split("-")[0], "EOS")
    else:
        def ev_of(k: str) -> str:
            return _evidence(k, fields)
        fallback = ["D", "C", "F"]
        label = "EOS"

    headline = f"{label} {result.eos}/100，屬「{result.rating}」"
    cmp = compare(result, previous)
    drivers: list[str] = []
    drags: list[str] = []
    evidence: list[str] = []

    if cmp.get("comparable"):
        d = cmp["eos_delta"]
        # 手機上「較 2026-09-23」太長，取月日即可；同日期比較不會跨年
        short = prev_date[5:] if prev_date and len(prev_date) == 10 else prev_date
        when = f"較 {short} " if short else "較前一交易日 "
        headline += (f"，{when}{'上升' if d > 0 else '下降'} {abs(d)} 分"
                     if d else f"，{when}持平")
        deltas = {k: v for k, v in (cmp.get(delta_key) or {}).items() if v}
        for k, v in sorted(deltas.items(), key=lambda kv: -kv[1]):
            line = f"{k} {names.get(k, k)} {v:+.1f}"
            (drivers if v > 0 else drags).append(line)
        # 只為變化最大的兩項附證據，避免結論變成欄位傾倒
        for k, _ in sorted(deltas.items(), key=lambda kv: -abs(kv[1]))[:2]:
            ev = ev_of(k)
            if ev:
                evidence.append(f"{k} {names.get(k, k)}：{ev}")
    else:
        headline += f"（{cmp.get('reason', '無法與前一交易日比較')}）"
        for k in fallback:
            ev = ev_of(k)
            if ev:
                evidence.append(f"{k} {names.get(k, k)}：{ev}")

    hx = _holdings_extremes(fields)
    if hx:
        evidence.append(f"Top10 {hx}")

    quality = _quality(result, fields)
    watch = _watch(rubric, result, fields, meta)

    parts = [headline]
    if drivers:
        parts.append("改善：" + "、".join(drivers))
    if drags:
        parts.append("拖累：" + "、".join(drags))
    parts.extend(evidence)
    if quality:
        parts.append("資料品質：" + "；".join(quality))
    if watch:
        parts.append("待觀察：" + "；".join(watch))

    return {"headline": headline, "drivers": drivers, "drags": drags,
            "evidence": evidence, "quality": quality, "watch": watch,
            "text": "　".join(parts)}
