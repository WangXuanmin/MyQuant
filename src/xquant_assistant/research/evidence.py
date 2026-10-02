"""Evidence-card output while structured research sources are disabled."""

from __future__ import annotations


def disabled_evidence_cards(as_of_date: str) -> dict:
    """Return an explicit empty state instead of fabricated evidence."""

    return {
        "as_of_date": as_of_date,
        "status": "NOT_EVALUATED",
        "reason": "no authorized fundamental or industry research provider configured",
        "cards": [],
    }

