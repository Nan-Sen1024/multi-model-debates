"""
属性测试：漂移分数范围
"""
from __future__ import annotations

from hypothesis import given, settings, strategies as st

from backend.drift_detector import DriftDetector


# Feature: multi-model-debate, Property 8: 漂移分数范围约束
@given(
    text=st.text(max_size=200),
    topic=st.text(min_size=1, max_size=200),
)
@settings(max_examples=30, deadline=5000)
def test_property_drift_score_within_unit_interval(text, topic):
    detector = DriftDetector()
    score = detector.compute_similarity(text, topic)
    assert 0.0 <= score <= 1.0
