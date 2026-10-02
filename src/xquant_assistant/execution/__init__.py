"""Local-only simulated execution."""

from .local_simulator import AccountStore, LocalSimulator
from .pricing import apply_slippage, slippage_bps_for

__all__ = ["AccountStore", "LocalSimulator", "apply_slippage", "slippage_bps_for"]
