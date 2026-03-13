"""RepoMap utilities — serialization and diffing helpers."""

import json
from pathlib import Path

from engine.models.repo_map import RepoMap


def save_repo_map(repo_map: RepoMap, output_path: Path) -> None:
    """Persist the repo map as a JSON file for debugging or caching."""
    output_path.write_text(repo_map.model_dump_json(indent=2))


def load_repo_map(input_path: Path) -> RepoMap:
    """Load a previously saved repo map from a JSON file."""
    data = json.loads(input_path.read_text())
    return RepoMap.model_validate(data)
