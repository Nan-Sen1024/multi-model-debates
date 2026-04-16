"""
Topic drift detection with optional sentence-transformers support.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Dict, Optional

logger = logging.getLogger(__name__)

try:
    import numpy as np

    _NUMPY_AVAILABLE = True
except ImportError:  # pragma: no cover - optional dependency
    np = None  # type: ignore
    _NUMPY_AVAILABLE = False

try:
    from sentence_transformers import SentenceTransformer

    _ST_AVAILABLE = True
except ImportError:  # pragma: no cover - optional dependency
    SentenceTransformer = None  # type: ignore
    _ST_AVAILABLE = False
    logger.warning(
        "sentence-transformers is not installed; drift detection will stay in degraded mode"
    )


@dataclass
class DriftResult:
    score: Optional[float]
    is_drifted: bool
    unavailable: bool = False


class DriftDetector:
    DEFAULT_MODEL = "paraphrase-multilingual-MiniLM-L12-v2"

    def __init__(self, model_name: str = DEFAULT_MODEL):
        self._model_name = model_name
        self._model = None
        self._topic_embeddings: Dict[str, object] = {}
        self._available = False
        self._load_attempted = False

    def _ensure_model_loaded(self) -> bool:
        if getattr(self, "_available", False):
            return True
        if (
            "_load_attempted" not in getattr(self, "__dict__", {})
            and getattr(self, "_model", None) is None
            and "_available" in getattr(self, "__dict__", {})
        ):
            return False
        if getattr(self, "_load_attempted", False):
            return False

        self._load_attempted = True
        if not (_ST_AVAILABLE and _NUMPY_AVAILABLE):
            return False

        try:
            self._model = SentenceTransformer(self._model_name)
            self._available = True
        except Exception as exc:
            logger.warning(
                "DriftDetector model load failed, degraded mode remains active: %s",
                exc,
            )
            self._model = None
            self._available = False
        return self._available

    def compute_similarity(self, text: str, topic: str) -> float:
        if not self._ensure_model_loaded():
            logger.warning("DriftDetector unavailable; returning similarity 1.0")
            return 1.0

        try:
            if topic not in self._topic_embeddings:
                self._topic_embeddings[topic] = self._model.encode(topic)

            topic_emb = self._topic_embeddings[topic]
            text_emb = self._model.encode(text)

            dot = float(np.dot(text_emb, topic_emb))
            norm = float(np.linalg.norm(text_emb) * np.linalg.norm(topic_emb))
            if norm == 0:
                return 0.0
            cosine = dot / norm
            return max(0.0, min(1.0, (cosine + 1.0) / 2.0))
        except Exception as exc:
            logger.warning("DriftDetector.compute_similarity failed, returning 1.0: %s", exc)
            return 1.0

    def check_drift(self, message, session) -> DriftResult:
        if not self._ensure_model_loaded():
            return DriftResult(score=None, is_drifted=False, unavailable=True)

        try:
            score = self.compute_similarity(message.content, session.topic)
            return DriftResult(
                score=score,
                is_drifted=score < session.config.drift_threshold,
            )
        except Exception as exc:
            logger.warning("DriftDetector.check_drift failed, degraded mode used: %s", exc)
            return DriftResult(score=None, is_drifted=False, unavailable=True)

    def handle_consecutive_drift(self, session, consecutive_count: int) -> Optional[str]:
        if consecutive_count >= 3:
            return self.build_topic_reminder(session)
        return None

    def build_topic_reminder(self, session) -> str:
        return (
            "[话题重申]\n"
            f"当前协作的原始话题为：{session.topic}\n"
            "请将你的回复聚焦于上述话题，避免偏离主题。\n"
            "[话题重申结束]"
        )
