"""
属性测试：漂移分数范围
"""
from __future__ import annotations

import numpy as np
from hypothesis import given, settings, strategies as st

from backend.drift_detector import DriftDetector


class FakeEmbeddingModel:
    def encode(self, text: str):
        codepoint_sum = sum(ord(char) for char in text)
        return np.array(
            [
                1.0 + float(len(text) % 17),
                1.0 + float(codepoint_sum % 19),
                1.0 + float((len(text) + codepoint_sum) % 23),
            ],
            dtype=float,
        )


def make_test_detector() -> DriftDetector:
    detector = DriftDetector.__new__(DriftDetector)
    detector._model_name = DriftDetector.DEFAULT_MODEL
    detector._topic_embeddings = {}
    detector._available = True
    detector._model = FakeEmbeddingModel()
    return detector


# Feature: multi-model-debate, Property 8: 漂移分数范围约束
@given(
    text=st.text(max_size=200),
    topic=st.text(min_size=1, max_size=200),
)
@settings(max_examples=30, deadline=5000)
def test_property_drift_score_within_unit_interval(text, topic):
    detector = make_test_detector()
    score = detector.compute_similarity(text, topic)
    assert 0.0 <= score <= 1.0
