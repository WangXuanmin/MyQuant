"""Transaction-cost model for mainland A shares and domestic ETFs."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP

from .settings import AccountConfig


_CENT = Decimal("0.01")


@dataclass(frozen=True)
class FeeBreakdown:
    """Auditable components for one simulated exchange trade."""

    commission: float
    regulatory_fee: float
    exchange_handling_fee: float
    transfer_fee: float
    stamp_duty: float
    total: float


def calculate_fee(
    notional: float,
    asset_type: str,
    side: str,
    config: AccountConfig,
) -> FeeBreakdown:
    """Calculate the configured net-commission cost, rounded to fen in total."""

    if notional < 0:
        raise ValueError("notional must be non-negative")
    normalized_side = side.upper()
    if normalized_side not in {"BUY", "SELL"}:
        raise ValueError("side must be BUY or SELL")
    if config.fee_model == "unconfigured":
        return FeeBreakdown(0.0, 0.0, 0.0, 0.0, 0.0, 0.0)

    amount = Decimal(str(notional))
    commission = max(
        amount * Decimal(str(config.commission_rate)),
        Decimal(str(config.commission_minimum)),
    ) if amount else Decimal("0")
    regulatory_fee = Decimal("0")
    exchange_handling_fee = Decimal("0")
    transfer_fee = Decimal("0")
    stamp_duty = Decimal("0")
    if asset_type == "CN_ETF":
        exchange_handling_fee = amount * Decimal(
            str(config.etf_exchange_handling_fee_rate)
        )
    else:
        # UNKNOWN holdings are charged as A shares, the conservative case.
        regulatory_fee = amount * Decimal(str(config.cn_a_regulatory_fee_rate))
        exchange_handling_fee = amount * Decimal(
            str(config.cn_a_exchange_handling_fee_rate)
        )
        transfer_fee = amount * Decimal(str(config.cn_a_transfer_fee_rate))
        if normalized_side == "SELL":
            stamp_duty = amount * Decimal(
                str(config.cn_a_sell_stamp_duty_rate)
            )
    total = (
        commission
        + regulatory_fee
        + exchange_handling_fee
        + transfer_fee
        + stamp_duty
    ).quantize(_CENT, rounding=ROUND_HALF_UP)
    return FeeBreakdown(
        commission=float(commission),
        regulatory_fee=float(regulatory_fee),
        exchange_handling_fee=float(exchange_handling_fee),
        transfer_fee=float(transfer_fee),
        stamp_duty=float(stamp_duty),
        total=float(total),
    )
