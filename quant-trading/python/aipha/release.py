"""Release metadata helpers for deployment version consistency."""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class ReleaseInfo:
    """Immutable build/release identity exposed by API and scheduler."""

    release_id: str
    git_commit: str
    build_time: str
    image_tag: str
    source: str

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


def _read_release_file(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    return data if isinstance(data, dict) else {}


def get_release_info(path: str | Path | None = None) -> ReleaseInfo:
    """Load release metadata from env or /app/release.json."""
    release_path = Path(path or os.environ.get("AIPHA_RELEASE_FILE", "/app/release.json"))
    file_data = _read_release_file(release_path)
    now = datetime.now(timezone.utc).isoformat()
    return ReleaseInfo(
        release_id=str(os.environ.get("AIPHA_RELEASE_ID") or file_data.get("release_id") or "dev"),
        git_commit=str(os.environ.get("AIPHA_GIT_COMMIT") or file_data.get("git_commit") or "unknown"),
        build_time=str(os.environ.get("AIPHA_BUILD_TIME") or file_data.get("build_time") or now),
        image_tag=str(os.environ.get("AIPHA_IMAGE_TAG") or file_data.get("image_tag") or "local"),
        source="env_or_release_file",
    )


__all__ = ["ReleaseInfo", "get_release_info"]