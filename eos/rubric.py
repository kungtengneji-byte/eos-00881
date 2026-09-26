"""讀取 eos_rubric_v1.1.yaml 並提供計分原語。

設計重點：**規則全部在 YAML，這裡只有解釋器**。
調整門檻不需要改 Python，改 YAML 即可 —— 這樣 rubric 的版本演進
（v1.1 -> v1.2）才能單獨 review，不會混在程式碼修改裡。

四種 item 型態，對應 YAML 的四種寫法：
  bands             一般查表（A、B2/B3/B4、C、E）
  bands_pct         查表結果為滿分的比例（D 的四個美股變數共用同一組門檻）
  checks            檢核清單，依可得項目 prorate（B1 趨勢）
  bands_by_condition 條件式查表（F1 量比，依當日漲跌方向切換）
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

DEFAULT_PATH = Path(__file__).resolve().parent.parent / "eos_rubric_v1.1.yaml"


class RubricError(Exception):
    """YAML 結構有問題 —— 寧可啟動就爆，也不要算出安靜錯誤的分數。"""


@dataclass(frozen=True)
class Band:
    lt: float | None          # 上界（開區間）；None 代表最後一段
    score: float              # bands 為絕對分數；bands_pct 為滿分比例


# 線性正規化：把指標值映射到 0–1，再乘以該因子的權重。
# 大盤資金流向模型用的是公式而非查表，例如「0.5 + 5日期貨淨OI變化/(2×10,000口)」。
# 每個轉換都夾在 0–1：模型定義明文要求（F1「夾在0–1」），
# 而且不夾的話單一極端值會讓總分超出 0–100。
LINEAR_TRANSFORMS = {
    # 1 − 值/參數1：用量越少，餘裕越大（本波天數、本波累計）
    "one_minus_ratio": lambda v, p1, p2: 1 - v / p1,
    # 值/參數1：距離越遠，空間越大（距前高壓力）
    "ratio": lambda v, p1, p2: v / p1,
    # 0.5 為中性，正向偏離加分（期貨回補、費半上漲）
    "centered": lambda v, p1, p2: 0.5 + v / (2 * p1),
    # 0.5 為中性，正向偏離扣分（融資增加＝散戶追價）
    "centered_inv": lambda v, p1, p2: 0.5 - v / (2 * p1),
    # 以參數1 為中性水準，高於中性扣分（10 年債殖利率）
    "centered_offset_inv": lambda v, p1, p2: 0.5 - (v - p1) / (2 * p2),
}


def apply_linear(value: float, transform: str, p1: float, p2: float | None) -> float:
    fn = LINEAR_TRANSFORMS.get(transform)
    if fn is None:
        raise RubricError(f"未知的線性轉換：{transform}")
    if not p1:
        raise RubricError(f"{transform} 的參數1 不可為 0 或缺值")
    raw = fn(float(value), float(p1), None if p2 is None else float(p2))
    return max(0.0, min(1.0, raw))


def pick_band(value: float, bands: list[Band]) -> float:
    for b in bands:
        if b.lt is None or value < b.lt:
            return b.score
    return bands[-1].score


@dataclass(frozen=True)
class Check:
    name: str
    input: str
    weight: float


@dataclass
class Item:
    id: str
    name: str
    max: float
    input: str | None = None
    bands: list[Band] | None = None
    pct_bands: list[Band] | None = None
    checks: list[Check] | None = None
    conditional_on: str | None = None
    bands_by_condition: dict[str, list[Band]] | None = None
    transform: str | None = None
    # 線性正規化（大盤燈號模型）：分數 = clamp(公式(值), 0, 1) × max
    linear: str | None = None
    param1: float | None = None
    param2: float | None = None
    param1_input: str | None = None      # 參數本身來自資料時（如期間最長買波天數）
    missing_policy: str = "exclude"

    def score(self, inputs: dict[str, Any]) -> tuple[float | None, float, str]:
        """回傳 (earned, available, detail)。earned 為 None 代表此項缺值不計分。"""
        if self.linear is not None:
            return self._score_linear(inputs)
        if self.checks is not None:
            return self._score_checklist(inputs)
        if self.bands_by_condition is not None:
            return self._score_conditional(inputs)
        return self._score_bands(inputs)

    # -- 一般查表 / 比例查表 ------------------------------------------------
    def _score_bands(self, inputs: dict[str, Any]) -> tuple[float | None, float, str]:
        raw = inputs.get(self.input) if self.input else None
        if raw is None:
            return None, 0.0, "缺值"
        v = abs(float(raw)) if self.transform == "abs" else float(raw)
        if self.pct_bands is not None:
            pct = pick_band(v, self.pct_bands)
            return pct * self.max, self.max, f"{v:.4g} -> {pct:.0%} x {self.max:g}"
        if self.bands is None:
            raise RubricError(f"{self.id} 既無 bands 也無 bands_pct")
        s = pick_band(v, self.bands)
        return s, self.max, f"{v:.4g} -> {s:g}"

    # -- 線性正規化（大盤燈號 F1–F7）---------------------------------------
    def _score_linear(self, inputs: dict[str, Any]) -> tuple[float | None, float, str]:
        raw = inputs.get(self.input) if self.input else None
        if raw is None:
            return None, 0.0, "缺值"
        p1 = self.param1
        if self.param1_input:
            p1 = inputs.get(self.param1_input)
            if p1 in (None, 0):
                return None, 0.0, f"參數 {self.param1_input} 缺值或為 0"
        norm = apply_linear(float(raw), self.linear, float(p1), self.param2)
        return norm * self.max, self.max, f"{float(raw):.6g} -> {norm:.4f} x {self.max:g}"

    # -- 檢核清單（B1 趨勢）------------------------------------------------
    def _score_checklist(self, inputs: dict[str, Any]) -> tuple[float | None, float, str]:
        """只以「可得的條件」按比例換算，缺的條件不當成 0 分也不當成滿分。

        目前 MA60/MA120 已由 Phase 1 回填補齊，四條件通常都在；
        但保留 prorate 是因為新標的剛納入追蹤時長序列仍會不足。
        """
        earned_w = 0.0
        avail_w = 0.0
        hits: list[str] = []
        for c in self.checks or []:
            val = inputs.get(c.input)
            if val is None:
                continue
            avail_w += c.weight
            if val:
                earned_w += c.weight
                hits.append(c.name)
        if avail_w == 0:
            return None, 0.0, "四項條件全缺"
        earned = earned_w / avail_w * self.max
        return earned, self.max, f"{earned_w:g}/{avail_w:g} 成立({', '.join(hits) or '無'})"

    # -- 條件式查表（F1 量比）----------------------------------------------
    def _score_conditional(self, inputs: dict[str, Any]) -> tuple[float | None, float, str]:
        raw = inputs.get(self.input) if self.input else None
        cond = inputs.get(self.conditional_on) if self.conditional_on else None
        if raw is None or cond is None:
            return None, 0.0, "缺值（量比或漲跌方向）"
        bands = (self.bands_by_condition or {}).get(str(cond))
        if bands is None:
            raise RubricError(f"{self.id} 沒有對應 {self.conditional_on}={cond} 的門檻表")
        s = pick_band(float(raw), bands)
        return s, self.max, f"{cond} 日 量比 {float(raw):.3g} -> {s:g}"


@dataclass
class Dimension:
    key: str
    name: str
    max: float
    items: list[Item] = field(default_factory=list)


@dataclass(frozen=True)
class Tier:
    min_available: float
    status: str
    publish_eos: bool
    ui_badge: str = ""
    ui_message: str = ""


@dataclass(frozen=True)
class RatingBand:
    min: float
    max: float
    label: str


@dataclass
class Rubric:
    version: str
    scale: float
    tiers: list[Tier]
    rating_bands: list[RatingBand]
    dimensions: dict[str, Dimension]

    # -- 載入 ---------------------------------------------------------------
    @classmethod
    def load(cls, path: str | Path = DEFAULT_PATH) -> "Rubric":
        raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise RubricError(f"{path} 不是合法的 rubric")

        tiers = [Tier(min_available=float(t["min_available"]), status=str(t["status"]),
                      publish_eos=bool(t.get("publish_eos", True)),
                      ui_badge=str(t.get("ui_badge", "")),
                      ui_message=str(t.get("ui_message", "")))
                 for t in raw.get("coverage_gate", {}).get("tiers", [])]
        tiers.sort(key=lambda t: t.min_available, reverse=True)
        if not tiers:
            raise RubricError("coverage_gate.tiers 不可為空")

        ratings = [RatingBand(min=float(b["min"]), max=float(b["max"]), label=str(b["label"]))
                   for b in raw.get("rating_bands", [])]

        dims: dict[str, Dimension] = {}
        for key, d in (raw.get("dimensions") or {}).items():
            items = [cls._parse_item(key, it) for it in (d.get("items") or [])]
            dim = Dimension(key=key, name=str(d.get("name", key)),
                            max=float(d["max"]), items=items)
            total = sum(i.max for i in items)
            if abs(total - dim.max) > 1e-9:
                raise RubricError(
                    f"構面 {key} 的子項滿分合計 {total} 與宣告的 {dim.max} 不符"
                )
            dims[key] = dim
        if not dims:
            raise RubricError("dimensions 不可為空")

        total_max = sum(d.max for d in dims.values())
        scale = float(raw.get("scale", 100))
        if abs(total_max - scale) > 1e-9:
            raise RubricError(f"所有構面滿分合計 {total_max} 與 scale {scale} 不符")

        return cls(version=str(raw.get("version", "unknown")), scale=scale,
                   tiers=tiers, rating_bands=ratings, dimensions=dims)

    @staticmethod
    def _parse_bands(raw: list[dict[str, Any]], key: str) -> list[Band]:
        return [Band(lt=(None if b.get("lt") is None else float(b["lt"])),
                     score=float(b[key])) for b in raw]

    @classmethod
    def _parse_item(cls, dim_key: str, it: dict[str, Any]) -> Item:
        item = Item(id=str(it.get("id", f"{dim_key}?")), name=str(it.get("name", "")),
                    max=float(it["max"]), input=it.get("input"),
                    transform=it.get("transform"),
                    conditional_on=it.get("conditional_on"),
                    linear=it.get("linear"),
                    param1=(None if it.get("param1") is None else float(it["param1"])),
                    param2=(None if it.get("param2") is None else float(it["param2"])),
                    param1_input=it.get("param1_input"),
                    missing_policy=str(it.get("missing_policy", "exclude")))
        if "bands" in it:
            item.bands = cls._parse_bands(it["bands"], "score")
        if "bands_pct" in it:
            item.pct_bands = cls._parse_bands(it["bands_pct"], "score_pct")
        if "checks" in it:
            item.checks = [Check(name=str(c["name"]), input=str(c["input"]),
                                 weight=float(c["weight"])) for c in it["checks"]]
        if "bands_by_condition" in it:
            item.bands_by_condition = {
                str(k): cls._parse_bands(v, "score")
                for k, v in it["bands_by_condition"].items()
            }
        if not any((item.bands, item.pct_bands, item.checks,
                    item.bands_by_condition, item.linear)):
            raise RubricError(f"{item.id} 沒有任何計分規則")
        return item

    # -- 查詢 ---------------------------------------------------------------
    def tier_for(self, available: float) -> Tier:
        for t in self.tiers:            # 已由高到低排序
            if available >= t.min_available:
                return t
        return self.tiers[-1]

    def rating_for(self, eos: float) -> str:
        for b in self.rating_bands:
            if b.min <= eos <= b.max:
                return b.label
        return "未分級"

    def required_inputs(self) -> set[str]:
        """所有會被讀取的 input 名稱，供收集器檢查覆蓋率。"""
        names: set[str] = set()
        for d in self.dimensions.values():
            for it in d.items:
                if it.input:
                    names.add(it.input)
                if it.conditional_on:
                    names.add(it.conditional_on)
                if it.param1_input:
                    names.add(it.param1_input)
                for c in it.checks or []:
                    names.add(c.input)
        return names
