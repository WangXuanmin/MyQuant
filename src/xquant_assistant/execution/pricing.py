"""Asset-specific conservative fill-price assumptions."""

from __future__ import annotations

from ..settings import AccountConfig


def slippage_bps_for(asset_type: str, config: AccountConfig) -> float:
    """Return the configured one-way slippage, falling back conservatively."""

    if asset_type in config.slippage_bps:
        return float(config.slippage_bps[asset_type])
    return float(config.slippage_bps.get("UNKNOWN", config.slippage_bps.get("CN_A_MAIN", 10.0)))


def apply_slippage(
    reference_price: float,
    asset_type: str,
    side: str,
    config: AccountConfig,
) -> float:
    """Move the reference price against the simulated order by one-way bps."""

    normalized_side = side.upper()
    if normalized_side not in {"BUY", "SELL"}:
        raise ValueError("side must be BUY or SELL")
    direction = 1.0 if normalized_side == "BUY" else -1.0
    return float(
        reference_price
        * (1.0 + direction * slippage_bps_for(asset_type, config) / 10_000.0)
    )
