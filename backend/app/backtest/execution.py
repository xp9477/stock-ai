"""Deterministic A-share execution and transaction-cost model for research.

The model is intentionally conservative and shared by every deterministic
backtest path.  It charges costs on actual turnover, applies buy/sell slippage,
enforces round lots for purchases, and keeps untradeable holdings instead of
pretending they were liquidated.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd

from ..runtime_settings import get_setting


def _positive_price(value: Any) -> float | None:
    try:
        price = float(value)
    except (TypeError, ValueError):
        return None
    return price if np.isfinite(price) and price > 0 else None


@dataclass(frozen=True)
class ExecutionCostModel:
    commission_rate: float
    commission_min: float
    transfer_fee_rate: float
    stamp_tax_rate: float
    slippage_bps: float
    lot_size: int = 100

    @classmethod
    def from_settings(cls) -> "ExecutionCostModel":
        return cls(
            commission_rate=float(get_setting("trading.commission_rate")),
            commission_min=float(get_setting("trading.commission_min")),
            transfer_fee_rate=float(get_setting("trading.transfer_fee_rate")),
            stamp_tax_rate=float(get_setting("trading.stamp_tax_rate")),
            slippage_bps=float(get_setting("trading.slippage_bps")),
        )

    @property
    def slippage_rate(self) -> float:
        return max(0.0, self.slippage_bps) / 10_000.0

    def fill_price(self, midpoint: float, side: str) -> float:
        midpoint = float(midpoint)
        direction = 1.0 if side == "buy" else -1.0
        return midpoint * (1.0 + direction * self.slippage_rate)

    def fees(self, notional: float, side: str) -> float:
        notional = max(0.0, float(notional))
        if notional <= 0:
            return 0.0
        commission = max(self.commission_min, notional * self.commission_rate)
        transfer = notional * self.transfer_fee_rate
        stamp = notional * self.stamp_tax_rate if side == "sell" else 0.0
        return commission + transfer + stamp

    def round_buy_quantity(self, quantity: float) -> int:
        if not np.isfinite(quantity) or quantity < self.lot_size:
            return 0
        return int(math.floor(quantity / self.lot_size) * self.lot_size)

    def affordable_buy_quantity(self, cash: float, midpoint: float) -> int:
        fill = self.fill_price(midpoint, "buy")
        quantity = self.round_buy_quantity(max(0.0, cash) / fill)
        while quantity > 0:
            notional = quantity * fill
            if notional + self.fees(notional, "buy") <= cash + 1e-9:
                return quantity
            quantity -= self.lot_size
        return 0


@dataclass(frozen=True)
class RebalanceResult:
    cash: float
    holdings: dict[str, float]
    fees: float
    traded_notional: float
    turnover: float
    closed_positions: int
    untradeable_codes: tuple[str, ...]


def portfolio_value(
    cash: float,
    holdings: dict[str, float],
    valuation_prices: pd.Series,
) -> float:
    value = float(cash)
    for code, quantity in holdings.items():
        price = _positive_price(valuation_prices.get(code))
        if price is None:
            raise ValueError(
                f"cannot value held security without a valid price: {code}")
        value += float(quantity) * price
    return value


def rebalance_equal_weight(
    *,
    cash: float,
    holdings: dict[str, float],
    execution_prices: pd.Series,
    valuation_prices: pd.Series,
    target_codes: list[str] | tuple[str, ...],
    cost_model: ExecutionCostModel,
) -> RebalanceResult:
    """Trade from current holdings toward equal target weights.

    Missing execution prices mean "cannot trade today".  Such positions stay
    in the portfolio and continue to use the last point-in-time valuation;
    they are never silently deleted.  The algorithm sells before buying and
    never borrows cash.
    """
    current = {
        str(code): float(quantity)
        for code, quantity in holdings.items()
        if float(quantity) > 1e-12
    }
    targets = list(dict.fromkeys(str(code) for code in target_codes if str(code)))
    pre_trade_value = portfolio_value(cash, current, valuation_prices)
    target_value = pre_trade_value / len(targets) if targets else 0.0
    fees_paid = 0.0
    traded_notional = 0.0
    closed_positions = 0
    untradeable: set[str] = set()

    # Sell exclusions and overweight positions first.
    for code in sorted(list(current)):
        midpoint = _positive_price(execution_prices.get(code))
        if midpoint is None:
            if code not in targets or current[code] * float(
                    valuation_prices.get(code)) > target_value + 1e-9:
                untradeable.add(code)
            continue
        desired_qty = 0
        if code in targets:
            buy_fill = cost_model.fill_price(midpoint, "buy")
            desired_qty = cost_model.round_buy_quantity(target_value / buy_fill)
        sell_qty = max(0.0, current[code] - desired_qty)
        # Existing legacy fractional quantities can be sold in full.  New
        # purchases are round lots, so this does not create fractional buys.
        if sell_qty <= 1e-12:
            continue
        fill = cost_model.fill_price(midpoint, "sell")
        notional = sell_qty * fill
        fee = cost_model.fees(notional, "sell")
        cash += notional - fee
        fees_paid += fee
        traded_notional += notional
        current[code] -= sell_qty
        if current[code] <= 1e-12:
            del current[code]
            closed_positions += 1

    # Buy underweights.  Deterministic code order makes evidence reproducible.
    for code in sorted(targets):
        midpoint = _positive_price(execution_prices.get(code))
        if midpoint is None:
            untradeable.add(code)
            continue
        fill = cost_model.fill_price(midpoint, "buy")
        desired_qty = cost_model.round_buy_quantity(target_value / fill)
        deficit = max(0, desired_qty - int(round(current.get(code, 0.0))))
        deficit = cost_model.round_buy_quantity(deficit)
        if deficit <= 0:
            continue
        affordable = cost_model.affordable_buy_quantity(cash, midpoint)
        buy_qty = min(deficit, affordable)
        if buy_qty <= 0:
            continue
        notional = buy_qty * fill
        fee = cost_model.fees(notional, "buy")
        cash -= notional + fee
        fees_paid += fee
        traded_notional += notional
        current[code] = current.get(code, 0.0) + buy_qty

    turnover = traded_notional / pre_trade_value if pre_trade_value > 0 else 0.0
    return RebalanceResult(
        cash=max(0.0, cash),
        holdings=current,
        fees=fees_paid,
        traded_notional=traded_notional,
        turnover=turnover,
        closed_positions=closed_positions,
        untradeable_codes=tuple(sorted(untradeable)),
    )


def sell_position(
    *,
    cash: float,
    code: str,
    quantity: float,
    midpoint: Any,
    cost_model: ExecutionCostModel,
) -> tuple[float, float, float] | None:
    """Sell one position with conservative fill/costs; None means untradeable."""
    price = _positive_price(midpoint)
    if price is None or quantity <= 0:
        return None
    fill = cost_model.fill_price(price, "sell")
    notional = float(quantity) * fill
    fee = cost_model.fees(notional, "sell")
    return cash + notional - fee, fee, notional
