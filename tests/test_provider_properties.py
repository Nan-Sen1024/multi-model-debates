"""
属性测试：Model_Ref 格式验证
"""
from __future__ import annotations

import pytest
from hypothesis import given, settings, strategies as st

from backend.exceptions import ValidationError
from backend.llm_gateway import validate_model_ref


segment = st.text(
    alphabet=st.characters(blacklist_categories=("Cs",), blacklist_characters="/\r\n\t"),
    min_size=1,
    max_size=20,
).filter(lambda value: value.strip() != "")


# Feature: multi-model-debate, Property 12: Model_Ref 格式验证
@given(provider=segment, model=segment)
@settings(max_examples=30, deadline=5000)
def test_property_valid_model_ref(provider, model):
    actual_provider, actual_model = validate_model_ref(f"{provider}/{model}")
    assert actual_provider == provider.strip()
    assert actual_model == model.strip()


# Feature: multi-model-debate, Property 12: Model_Ref 格式验证
@given(st.text().filter(lambda value: value.count("/") != 1 or any(part.strip() == "" for part in value.split("/", 1))))
@settings(max_examples=30, deadline=5000)
def test_property_invalid_model_ref_rejected(value):
    with pytest.raises(ValidationError):
        validate_model_ref(value)
