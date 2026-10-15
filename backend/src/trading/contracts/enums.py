from __future__ import annotations

from enum import StrEnum


class TradingMode(StrEnum):
    """§23.2 mode routing."""
    BACKTEST = "BACKTEST"
    HISTORICAL_REPLAY = "HISTORICAL_REPLAY"
    SHADOW = "SHADOW"
    TESTNET = "TESTNET"
    LIVE = "LIVE"


class OperatorRole(StrEnum):
    """§20.1 operator_roles; authorization is checked server-side against the DB."""
    VIEWER = "viewer"
    OPERATOR = "operator"
    ADMIN = "admin"
    LIVE_APPROVER = "live_approver"
