"""EOS 計分引擎。

核心是「以可得滿分標準化」：
    EOS = 已得分 / 可計分滿分 * 100

而不是「缺值當 0 分」。差別很大 —— 當日 NAV 未發布時，A 構面 15 分
如果當 0 分計，EOS 會憑空掉 15 分，看起來像市況惡化，實際上只是資料還沒到。

標準化的代價是覆蓋率低時會外插失真，所以搭配 coverage_gate 分級：
覆蓋率 ≥90 才算「確定」，70–89 標「暫定」，<70 不出分。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from eos.rubric import Rubric, Tier


@dataclass
class ItemResult:
    id: str
    name: str
    earned: float | None
    available: float
    max: float
    detail: str

    @property
    def scored(self) -> bool:
        return self.earned is not None

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "name": self.name, "earned": self.earned,
                "available": self.available, "max": self.max,
                "scored": self.scored, "detail": self.detail}


@dataclass
class DimensionResult:
    key: str
    name: str
    earned: float | None
    available: float
    max: float
    items: list[ItemResult] = field(default_factory=list)

    @property
    def complete(self) -> bool:
        return abs(self.available - self.max) < 1e-9

    @property
    def missing_items(self) -> list[str]:
        return [i.id for i in self.items if not i.scored]

    def to_dict(self) -> dict[str, Any]:
        return {"key": self.key, "name": self.name, "earned": self.earned,
                "available": self.available, "max": self.max,
                "complete": self.complete,
                "items": [i.to_dict() for i in self.items]}


@dataclass
class EosResult:
    rubric_version: str
    dimensions: dict[str, DimensionResult]
    earned: float
    available: float
    eos: int | None            # 已標準化並四捨五入；未達閘門時為 None
    raw_eos: float | None      # 未四捨五入，供除錯與比對
    tier: Tier
    rating: str | None

    @property
    def coverage(self) -> float:
        return self.available

    @property
    def published(self) -> bool:
        return self.tier.publish_eos and self.eos is not None

    def missing_summary(self) -> list[str]:
        out: list[str] = []
        for d in self.dimensions.values():
            out.extend(d.missing_items)
        return out

    def to_dict(self) -> dict[str, Any]:
        return {
            "rubric_version": self.rubric_version,
            "eos": self.eos,
            "raw_eos": self.raw_eos,
            "earned": round(self.earned, 4),
            "available": self.available,
            "coverage_status": self.tier.status,
            "published": self.published,
            "rating": self.rating,
            "missing": self.missing_summary(),
            "dimensions": {k: v.to_dict() for k, v in self.dimensions.items()},
        }

    def explain(self) -> str:
        """一行一構面的可讀說明，用於 Email 摘要與除錯。"""
        lines = [f"EOS {self.eos if self.eos is not None else '--'}"
                 f"（{self.rating or self.tier.status}）"
                 f"  覆蓋率 {self.available:g}/100"]
        for key in sorted(self.dimensions):
            d = self.dimensions[key]
            got = f"{d.earned:.1f}" if d.earned is not None else "--"
            flag = "" if d.complete else "  ← 部分缺值"
            lines.append(f"  {key} {d.name}: {got}/{d.available:g}"
                         f"（滿分 {d.max:g}）{flag}")
        return "\n".join(lines)


def compute(rubric: Rubric, inputs: dict[str, Any]) -> EosResult:
    """依 rubric 對一組輸入計分。

    inputs 的 key 是 rubric 裡的 input 名稱（premium、rsi14、wcr…）。
    值為 None 或 key 不存在都視為缺值 —— 呼叫端不需要先過濾。
    """
    dims: dict[str, DimensionResult] = {}
    total_earned = 0.0
    total_available = 0.0

    for key, dim in rubric.dimensions.items():
        items: list[ItemResult] = []
        d_earned = 0.0
        d_available = 0.0
        any_scored = False

        for item in dim.items:
            earned, available, detail = item.score(inputs)
            items.append(ItemResult(id=item.id, name=item.name, earned=earned,
                                    available=available, max=item.max, detail=detail))
            if earned is not None:
                any_scored = True
                d_earned += earned
                d_available += available

        dims[key] = DimensionResult(
            key=key, name=dim.name,
            earned=d_earned if any_scored else None,
            available=d_available, max=dim.max, items=items,
        )
        total_earned += d_earned
        total_available += d_available

    raw = (total_earned / total_available * rubric.scale) if total_available > 0 else None
    tier = rubric.tier_for(total_available)

    eos: int | None = None
    rating: str | None = None
    if raw is not None and tier.publish_eos:
        eos = int(round(raw))
        rating = rubric.rating_for(eos)

    return EosResult(rubric_version=rubric.version, dimensions=dims,
                     earned=total_earned, available=total_available,
                     eos=eos, raw_eos=raw, tier=tier, rating=rating)


def compare(current: EosResult, previous: EosResult | None) -> dict[str, Any]:
    """與前一交易日比較，給出總分與各構面的變化。

    兩邊都必須是已發布的分數才比 —— 拿「暫定」跟「確定」相減會產生
    看起來像市況變化、實際上只是資料回補的假訊號。
    """
    if previous is None or not current.published or not previous.published:
        return {"comparable": False, "reason": "前一交易日無已發布分數，或當日未達發布門檻"}

    per_dim: dict[str, float | None] = {}
    for key, d in current.dimensions.items():
        p = previous.dimensions.get(key)
        if p is None or d.earned is None or p.earned is None:
            per_dim[key] = None
            continue
        # 構面覆蓋率不同時分數不可直接相減，改以得分率比較
        if abs(d.available - p.available) > 1e-9:
            per_dim[key] = None
            continue
        per_dim[key] = round(d.earned - p.earned, 2)

    assert current.eos is not None and previous.eos is not None
    return {
        "comparable": True,
        "eos_delta": current.eos - previous.eos,
        "dimension_delta": per_dim,
    }
