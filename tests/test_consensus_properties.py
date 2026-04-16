"""
Property tests for consensus detection precision.
"""
from __future__ import annotations

from hypothesis import given, settings, strategies as st

from backend.consensus_detector import ConsensusDetector


detector = ConsensusDetector()


# Feature: multi-model-debate, Property 10: explicit consensus is detected
@given(st.sampled_from(list(ConsensusDetector.CONSENSUS_KEYWORDS)))
@settings(max_examples=30, deadline=5000)
def test_property_explicit_consensus_detected(keyword):
    result = detector.detect("ModelA", f"经过讨论，{keyword}，我们就按这个方案执行。")
    assert result.is_consensus is True
    assert result.agreeing_party == "ModelA"


# Feature: multi-model-debate, Property 10: partial agreement is not consensus
@given(st.sampled_from(list(ConsensusDetector.NON_CONSENSUS_HINTS)))
@settings(max_examples=30, deadline=5000)
def test_property_partial_agreement_not_detected(hint):
    result = detector.detect("ModelA", f"我{hint}，但仍然需要补充条件。")
    assert result.is_consensus is False
