"""Local simulation through the shared engine; legacy writers removed."""
from .simulator_facade import AccountStore, LocalSimulator
__all__ = ["AccountStore", "LocalSimulator"]
