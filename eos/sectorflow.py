"""產業資金輪動：把逐檔法人買賣超依產業彙總，並用價格確認方向。

對齊工作表〈產業資金輪動〉分頁，但有兩個刻意的差異，先說清楚：

## 一、這裡算的是真正的產業合計

工作表那頁標了「非官方類股總額」，因為它是從各日的**法人前十大榜單**
拼出來的 —— 看得到的只有榜上那幾檔。平台有全市場 1,300 餘檔的逐檔資料，
所以算的是整個產業的淨額。比較準，但也代表數字不會與工作表逐格相同。

## 二、判讀只寫資料支持的話

工作表的「判讀」欄是人寫的，而且來源明寫「與新聞交叉」——
「資金躲入金融防禦」「外資回補電子，資金自 ODM 轉向記憶體/面板」
這種句子需要新聞面與盤感，平台生不出來也不該假裝生得出來。
這裡只輸出資料本身能支持的敘述：誰流入最多、誰流出最多、
價格有沒有同向、主導的是哪一類法人。

## 價格同向確認

工作表的規則是「流入／流出以法人買賣超榜單＋價格同向為準」。
這裡把它量化成 confirm_ratio：該產業當日**買超金額中，個股同時上漲的比例**
（賣超則看下跌）。比例高代表買盤真的推動了價格，而不是被動承接。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any, Iterable

from sources.twse import INDUSTRY_NAME

# ETF／ETN 不屬於任何產業，但自營商的避險部位幾乎都在這裡 ——
# 混進產業合計會讓「資金流入某產業」變成造市行為的雜訊。工作表也寫「ETF 另列」。
ETF_KEY = "ETF"
ETF_LABEL = "ETF／受益憑證"
UNKNOWN_KEY = "OTHER"
UNKNOWN_LABEL = "未分類"

SHARES_PER_LOT = 1000
HUNDRED_M = 1e8

# 金額太小的產業不列入流入／流出榜：幾千萬的淨額在 1,300 檔的市場裡是雜訊
MIN_AMOUNT_100M = 1.0
TOP_SECTORS = 3
TOP_STOCKS = 3


@dataclass(frozen=True)
class StockFlow:
    code: str
    name: str
    lots: float
    amount_100m: float
    ret: float | None

    @property
    def confirmed(self) -> bool | None:
        """買超且上漲，或賣超且下跌 —— 價格站在資金那一邊。"""
        if self.ret is None or self.amount_100m == 0:
            return None
        return (self.amount_100m > 0) == (self.ret > 0)


@dataclass(frozen=True)
class SectorFlow:
    key: str
    label: str
    amount_100m: float
    lots: float
    stocks: int
    confirm_ratio: float | None      # 同向的金額佔比；無價格資料時為 None
    top: list[StockFlow]

    def to_dict(self) -> dict[str, Any]:
        return {"key": self.key, "label": self.label,
                "amount_100m": round(self.amount_100m, 2),
                "lots": round(self.lots, 1), "stocks": self.stocks,
                "confirm_ratio": (None if self.confirm_ratio is None
                                  else round(self.confirm_ratio, 3)),
                "top": [{"code": s.code, "name": s.name,
                         "lots": round(s.lots, 1),
                         "amount_100m": round(s.amount_100m, 2),
                         "ret": s.ret, "confirmed": s.confirmed}
                        for s in self.top]}


def sector_of(code: str, industries: dict[str, str]) -> tuple[str, str]:
    """代號 -> (產業key, 產業名)。ETF 與查不到產業的各自成一類。"""
    if code.startswith("0"):
        return ETF_KEY, ETF_LABEL
    ind = industries.get(code)
    if not ind:
        return UNKNOWN_KEY, UNKNOWN_LABEL
    return ind, INDUSTRY_NAME.get(ind, f"產業{ind}")


def _net(row: list[int], institution: str) -> int:
    if institution == "total":
        return sum(row)
    if institution == "foreign":
        return row[0] + row[1]
    return {"trust": row[2], "dealer": row[3]}[institution]


def compute(nets: dict[str, list[int]], prices: dict[str, list[Any]] | None,
            industries: dict[str, str], names: dict[str, str],
            *, institution: str = "total") -> list[SectorFlow]:
    """單一交易日的產業資金流，依金額由大到小（流入為正）。"""
    prices = prices or {}
    buckets: dict[str, list[StockFlow]] = {}
    labels: dict[str, str] = {}

    for code, row in nets.items():
        shares = _net(row, institution)
        if shares == 0:
            continue
        px = prices.get(code) or []
        close = px[0] if px else None
        ret = px[1] if len(px) > 1 else None
        if close is None:
            continue                       # 沒有價格就算不出金額，不以張數混充
        amount = shares * close / HUNDRED_M
        key, label = sector_of(code, industries)
        labels[key] = label
        buckets.setdefault(key, []).append(StockFlow(
            code=code, name=names.get(code, code),
            lots=shares / SHARES_PER_LOT, amount_100m=amount, ret=ret))

    out: list[SectorFlow] = []
    for key, rows in buckets.items():
        total = sum(r.amount_100m for r in rows)
        if total == 0:
            continue
        side = [r for r in rows if (r.amount_100m > 0) == (total > 0)]
        # 只用與該產業同方向的部位算確認率：逆向的那幾檔本來就不是這股資金流
        weighted = [r for r in side if r.confirmed is not None]
        denom = sum(abs(r.amount_100m) for r in weighted)
        ratio = (sum(abs(r.amount_100m) for r in weighted if r.confirmed) / denom
                 if denom else None)
        out.append(SectorFlow(
            key=key, label=labels[key], amount_100m=total,
            lots=sum(r.lots for r in rows), stocks=len(rows),
            confirm_ratio=ratio,
            top=sorted(side, key=lambda r: -abs(r.amount_100m))[:TOP_STOCKS],
        ))
    out.sort(key=lambda s: -s.amount_100m)
    return out


def leading_institution(nets: dict[str, list[int]],
                        prices: dict[str, list[Any]] | None) -> tuple[str, float]:
    """當日金額規模最大的法人別。工作表的「主導法人」欄。"""
    prices = prices or {}
    best, best_amt = "total", 0.0
    for inst in ("foreign", "trust", "dealer"):
        amt = 0.0
        for code, row in nets.items():
            px = prices.get(code) or []
            if not px or px[0] is None:
                continue
            amt += abs(_net(row, inst) * px[0] / HUNDRED_M)
        if amt > best_amt:
            best, best_amt = inst, amt
    return best, best_amt


INSTITUTION_LABEL = {"foreign": "外資", "trust": "投信",
                     "dealer": "自營商", "total": "三大法人"}


def _fmt_sector(s: SectorFlow) -> str:
    names = "／".join(t.name for t in s.top[:2])
    return f"{s.label}（{names}）" if names else s.label


def read(inflow: list[SectorFlow], outflow: list[SectorFlow],
         inst: str) -> str:
    """只寫資料支持的一句話。

    工作表的判讀欄還會交叉新聞（「資金躲入金融防禦」那類），
    平台沒有新聞面，不寫那種句子 —— 寫了就是沒有根據的臆測。
    """
    bits: list[str] = []
    if inflow:
        s = inflow[0]
        line = f"資金流入{_fmt_sector(s)} {s.amount_100m:+.1f} 億"
        if s.confirm_ratio is not None:
            line += f"，價格同向 {s.confirm_ratio:.0%}"
        bits.append(line)
    if outflow:
        s = outflow[0]
        line = f"流出{_fmt_sector(s)} {s.amount_100m:+.1f} 億"
        if s.confirm_ratio is not None:
            line += f"，價格同向 {s.confirm_ratio:.0%}"
        bits.append(line)
    if not bits:
        return "當日無達門檻的產業淨流向。"
    bits.append(f"主導法人為{INSTITUTION_LABEL.get(inst, inst)}")
    return "；".join(bits) + "。"


def day_row(day: date, nets: dict[str, list[int]],
            prices: dict[str, list[Any]] | None,
            industries: dict[str, str], names: dict[str, str],
            *, min_amount: float = MIN_AMOUNT_100M,
            top_n: int = TOP_SECTORS) -> dict[str, Any]:
    inst, _ = leading_institution(nets, prices)
    sectors = compute(nets, prices, industries, names, institution="total")
    inflow = [s for s in sectors if s.amount_100m >= min_amount][:top_n]
    outflow = [s for s in reversed(sectors)
               if s.amount_100m <= -min_amount][:top_n]
    return {
        "date": day.isoformat(),
        "leading": inst,
        "leading_label": INSTITUTION_LABEL.get(inst, inst),
        "inflow": [s.to_dict() for s in inflow],
        "outflow": [s.to_dict() for s in outflow],
        "read": read(inflow, outflow, inst),
    }


def build_report(days: Iterable[date], loader, *, limit: int = 20,
                 **kw: Any) -> dict[str, Any]:
    """loader(day) -> (nets, prices) 或 None。由新到舊取最多 limit 天。"""
    rows: list[dict[str, Any]] = []
    for day in sorted(days, reverse=True):
        got = loader(day)
        if not got:
            continue
        nets, prices, industries, names = got
        if not nets:
            continue
        rows.append(day_row(day, nets, prices, industries, names, **kw))
        if len(rows) >= limit:
            break
    rows.reverse()
    return {"as_of": rows[-1]["date"] if rows else None,
            "days": len(rows), "source": "TWSE T86 + MI_INDEX + 上市公司基本資料",
            "rows": rows}
