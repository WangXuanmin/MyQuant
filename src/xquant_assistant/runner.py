"""Public entry point for the transactional daily adapter."""
from .runtime.daily import DailyRunner
__all__ = ["DailyRunner"]
