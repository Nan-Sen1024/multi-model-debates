from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple


@dataclass(frozen=True)
class ModelCatalogEntry:
    provider_id: str
    provider_name: str
    provider_type: str
    models: Tuple[str, ...]
    detected_at: Optional[int] = None


@dataclass(frozen=True)
class StreamEvent:
    event: str
    payload: Dict[str, Any]
