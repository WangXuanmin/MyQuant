"""Exceptions raised by the backtest metrics component."""


class MetricInputError(ValueError):
    """Raised when a result or series cannot produce trustworthy metrics."""


class MetricConfigError(ValueError):
    """Raised when metric configuration is internally inconsistent."""

