"""Load pinned agent graph configs from configs/analysis/*.yaml."""

from __future__ import annotations

from pathlib import Path

import yaml

from trading.contracts.analysis import AgentGraphConfig

CONFIG_DIR = Path(__file__).resolve().parents[4] / "configs" / "analysis"


def load_graph(path: str | Path) -> AgentGraphConfig:
    """Parse and validate a graph file. Amounts must be quoted strings; floats are refused."""
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    if not isinstance(data, dict) or set(data) != {"analysis_profile"}:
        raise ValueError(f"{path}: expected a single top-level 'analysis_profile' mapping")
    return AgentGraphConfig.model_validate(data["analysis_profile"])


def load_all(directory: Path = CONFIG_DIR) -> dict[str, AgentGraphConfig]:
    graphs = {p.stem: load_graph(p) for p in sorted(directory.glob("*.yaml"))}
    versions = [g.graph_version for g in graphs.values()]
    if len(versions) != len(set(versions)):
        raise ValueError("graph_version must be unique across config files")
    return graphs
