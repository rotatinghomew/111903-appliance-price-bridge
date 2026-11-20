"""区间算术：所有价格调整都带上下界，方向未定时不硬折成单值。"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Range:
    low: float
    high: float

    def __post_init__(self) -> None:
        if self.low > self.high:
            raise ValueError(f"区间下界 {self.low} 大于上界 {self.high}")

    @classmethod
    def point(cls, value: float) -> "Range":
        return cls(value, value)

    def __add__(self, other: "Range") -> "Range":
        return Range(self.low + other.low, self.high + other.high)

    def __mul__(self, scalar: float) -> "Range":
        if scalar < 0:
            return Range(self.high * scalar, self.low * scalar)
        return Range(self.low * scalar, self.high * scalar)

    __rmul__ = __mul__

    def scale(self, scalar: float) -> "Range":
        return self * scalar

    def ratio(self, other: "Range") -> "Range":
        """两个正区间相除。"""
        if other.low <= 0:
            raise ValueError("除数区间必须为正")
        return Range(self.low / other.high, self.high / other.low)

    def reciprocal(self) -> "Range":
        return Range(1 / self.high, 1 / self.low)

    def intersect(self, other: "Range") -> "Range | None":
        low, high = max(self.low, other.low), min(self.high, other.high)
        return Range(low, high) if low <= high else None

    def contains(self, value: float) -> bool:
        return self.low <= value <= self.high

    def within(self, bound: float) -> bool:
        """整个区间都落在 ``[-bound, bound]`` 内。"""
        return -bound <= self.low and self.high <= bound

    def beyond(self, bound: float) -> bool:
        """整个区间都严格越出 ``bound`` 的同一侧。"""
        return self.low > bound or self.high < -bound

    @property
    def direction(self) -> str:
        if self.low > 0:
            return "up"
        if self.high < 0:
            return "down"
        return "undetermined"

    @property
    def is_positive(self) -> bool:
        return self.low > 0

    def rounded(self, digits: int = 6) -> "Range":
        return Range(round(self.low, digits), round(self.high, digits))

    def as_percent(self, digits: int = 2) -> tuple[float, float]:
        return round(self.low * 100, digits), round(self.high * 100, digits)

    def __str__(self) -> str:
        lo, hi = self.as_percent()
        return f"{lo:+.2f}%..{hi:+.2f}%"


def one() -> Range:
    return Range(1.0, 1.0)


def product(values: list[Range]) -> Range:
    out = one()
    for value in values:
        out = Range(out.low * value.low, out.high * value.high)
    return out
