"""Strategy library and bot profiles for the web (read-only, docs/strategy-library.md).

Profiles are files in configs/profiles; changing one is a new version, not an edit in the UI.
"""

from __future__ import annotations

from typing import Any

from trading.strategies.library import catalog
from trading.strategies.library import profile as bp


def catalog_view() -> dict[str, Any]:
    items = []
    for s in sorted(catalog.CATALOG.values(), key=lambda s: s.no):
        items.append({"number": s.no, "template_id": s.template_id, "version": s.version, "name": s.name, "family": s.family,
                      "timeframes": list(s.timeframes), "default_timeframe": s.default_timeframe, "summary": s.summary,
                      "params": {k: {"default": p.default, "min": p.min, "max": p.max} for k, p in s.params.items()},
                      "evidence": "untested"})
    return {"items": items}


def profiles_view() -> dict[str, Any]:
    items = []
    for p in bp.load_all():
        items.append({"profile_id": p.profile_id, "version": p.version, "name": p.name, "status": p.status,
                      "instrument": p.instrument, "decision_clock": p.decision_clock, "combine": p.combine,
                      "risk_profile": p.risk_profile, "ai_mode": p.ai_mode, "notes": p.notes,
                      "strategies": p.resolved(), "hash": p.profile_hash()})
    return {"items": items}
