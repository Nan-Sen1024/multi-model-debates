"""
单元测试：DriftDetector
覆盖需求：24.1、24.2、24.3、24.4、24.5、24.6
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Dict, List, Optional
from unittest.mock import MagicMock, patch

import pytest

from backend.drift_detector import DriftDetector, DriftResult
from backend.enums import CollaborationMode, MessageType, SessionStatus
from backend.models import (
    CollaborationMessage,
    ModelParticipant,
    Session,
    SessionConfig,
    SessionSnapshot,
)


# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------

def make_session(
    topic: str = "人工智能的未来",
    drift_threshold: float = 0.4,
) -> Session:
    p1 = ModelParticipant(
        id=str(uuid.uuid4()),
        session_id="sess-drift",
        custom_id="ModelA",
        model_ref="openai/gpt-4o",
        sequence_order=0,
    )
    snapshot = SessionSnapshot(
        topic=topic,
        mode=CollaborationMode.DEBATE,
        participant_summaries={"ModelA": ""},
        consensus_list=[],
        key_events=[],
    )
    return Session(
        id="sess-drift",
        topic=topic,
        mode=CollaborationMode.DEBATE,
        status=SessionStatus.ACTIVE,
        participants=[p1],
        config=SessionConfig(drift_threshold=drift_threshold),
        snapshot=snapshot,
    )


def make_message(content: str = "hello", session_id: str = "sess-drift") -> CollaborationMessage:
    return CollaborationMessage(
        id=str(uuid.uuid4()),
        session_id=session_id,
        sender_id="ModelA",
        message_type=MessageType.DIALOGUE,
        content=content,
    )


def make_mock_model(similarity_value: float = 0.8):
    """创建一个 mock SentenceTransformer，encode 返回固定向量使余弦相似度可控"""
    import numpy as np
    mock_model = MagicMock()
    # 两个相同向量的余弦相似度 = 1.0，归一化后 = 1.0
    # 通过控制向量方向来控制相似度
    # 使用固定向量：topic=[1,0], text=[cos(θ), sin(θ)]
    # cosine = cos(θ)，归一化后 = (cos(θ)+1)/2
    # 要得到 similarity_value，需要 cos(θ) = 2*similarity_value - 1
    import math
    cos_theta = 2 * similarity_value - 1
    cos_theta = max(-1.0, min(1.0, cos_theta))
    sin_theta = math.sqrt(1 - cos_theta ** 2)
    topic_vec = np.array([1.0, 0.0])
    text_vec = np.array([cos_theta, sin_theta])

    call_count = {"n": 0}

    def encode_side_effect(text):
        call_count["n"] += 1
        # 第一次调用返回 topic_vec，后续返回 text_vec
        if call_count["n"] == 1:
            return topic_vec.copy()
        return text_vec.copy()

    mock_model.encode.side_effect = encode_side_effect
    return mock_model


# ---------------------------------------------------------------------------
# 1. compute_similarity 返回值在 [0, 1] 范围内（使用 mock）
# ---------------------------------------------------------------------------

def test_compute_similarity_returns_value_in_range():
    """compute_similarity 返回值在 [0, 1] 范围内，需求 24.1"""
    detector = DriftDetector.__new__(DriftDetector)
    detector._model_name = DriftDetector.DEFAULT_MODEL
    detector._topic_embeddings = {}
    detector._available = True

    import numpy as np
    mock_model = MagicMock()
    mock_model.encode.side_effect = [
        np.array([1.0, 0.0]),   # topic embedding
        np.array([0.6, 0.8]),   # text embedding
    ]
    detector._model = mock_model

    score = detector.compute_similarity("测试文本", "人工智能")
    assert 0.0 <= score <= 1.0


def test_compute_similarity_identical_texts_returns_high_score():
    """相同文本的相似度应接近 1.0，需求 24.1"""
    detector = DriftDetector.__new__(DriftDetector)
    detector._model_name = DriftDetector.DEFAULT_MODEL
    detector._topic_embeddings = {}
    detector._available = True

    import numpy as np
    vec = np.array([1.0, 0.0])
    mock_model = MagicMock()
    mock_model.encode.side_effect = [vec.copy(), vec.copy()]
    detector._model = mock_model

    score = detector.compute_similarity("人工智能", "人工智能")
    assert score >= 0.9


def test_compute_similarity_opposite_vectors_returns_low_score():
    """反向向量的相似度应接近 0.0，需求 24.1"""
    detector = DriftDetector.__new__(DriftDetector)
    detector._model_name = DriftDetector.DEFAULT_MODEL
    detector._topic_embeddings = {}
    detector._available = True

    import numpy as np
    mock_model = MagicMock()
    mock_model.encode.side_effect = [
        np.array([1.0, 0.0]),    # topic
        np.array([-1.0, 0.0]),   # text（反向）
    ]
    detector._model = mock_model

    score = detector.compute_similarity("完全不相关", "人工智能")
    assert score <= 0.1


def test_compute_similarity_caches_topic_embedding():
    """topic embedding 应被缓存，第二次调用不重新计算，需求 24.1"""
    detector = DriftDetector.__new__(DriftDetector)
    detector._model_name = DriftDetector.DEFAULT_MODEL
    detector._topic_embeddings = {}
    detector._available = True

    import numpy as np
    mock_model = MagicMock()
    topic_vec = np.array([1.0, 0.0])
    text_vec = np.array([0.8, 0.6])
    # 第一次：topic；第二次：text1；第三次：text2（topic 不再重新计算）
    mock_model.encode.side_effect = [
        topic_vec.copy(),
        text_vec.copy(),
        text_vec.copy(),
    ]
    detector._model = mock_model

    detector.compute_similarity("文本1", "人工智能")
    detector.compute_similarity("文本2", "人工智能")

    # encode 应只被调用 3 次（topic 1 次 + text 2 次）
    assert mock_model.encode.call_count == 3


# ---------------------------------------------------------------------------
# 2. check_drift：score < threshold 时 is_drifted=True
# ---------------------------------------------------------------------------

def test_check_drift_is_drifted_when_score_below_threshold():
    """score < drift_threshold 时 is_drifted=True，需求 24.2"""
    detector = DriftDetector.__new__(DriftDetector)
    detector._model_name = DriftDetector.DEFAULT_MODEL
    detector._topic_embeddings = {}
    detector._available = True

    import numpy as np
    # 构造相似度约 0.2（低于默认阈值 0.4）
    mock_model = MagicMock()
    mock_model.encode.side_effect = [
        np.array([1.0, 0.0]),
        np.array([-0.6, 0.8]),   # cosine ≈ -0.6，归一化 ≈ 0.2
    ]
    detector._model = mock_model

    session = make_session(drift_threshold=0.4)
    msg = make_message("完全偏离话题的内容")
    result = detector.check_drift(msg, session)

    assert result.is_drifted is True
    assert result.score is not None
    assert result.score < 0.4
    assert result.unavailable is False


def test_check_drift_not_drifted_when_score_above_threshold():
    """score >= drift_threshold 时 is_drifted=False，需求 24.2"""
    detector = DriftDetector.__new__(DriftDetector)
    detector._model_name = DriftDetector.DEFAULT_MODEL
    detector._topic_embeddings = {}
    detector._available = True

    import numpy as np
    mock_model = MagicMock()
    mock_model.encode.side_effect = [
        np.array([1.0, 0.0]),
        np.array([1.0, 0.0]),   # 完全相同，相似度 = 1.0
    ]
    detector._model = mock_model

    session = make_session(drift_threshold=0.4)
    msg = make_message("人工智能的未来发展趋势")
    result = detector.check_drift(msg, session)

    assert result.is_drifted is False
    assert result.score is not None
    assert result.score >= 0.4


def test_check_drift_score_stored_in_result():
    """check_drift 返回的 DriftResult 包含 score 字段，需求 24.1"""
    detector = DriftDetector.__new__(DriftDetector)
    detector._model_name = DriftDetector.DEFAULT_MODEL
    detector._topic_embeddings = {}
    detector._available = True

    import numpy as np
    mock_model = MagicMock()
    mock_model.encode.side_effect = [
        np.array([1.0, 0.0]),
        np.array([0.8, 0.6]),
    ]
    detector._model = mock_model

    session = make_session()
    msg = make_message("测试内容")
    result = detector.check_drift(msg, session)

    assert result.score is not None
    assert 0.0 <= result.score <= 1.0


# ---------------------------------------------------------------------------
# 3. 服务不可用时降级（unavailable=True）
# ---------------------------------------------------------------------------

def test_check_drift_unavailable_when_model_not_loaded():
    """模型未加载时 check_drift 返回 unavailable=True，需求 24.6"""
    detector = DriftDetector.__new__(DriftDetector)
    detector._model_name = DriftDetector.DEFAULT_MODEL
    detector._topic_embeddings = {}
    detector._available = False
    detector._model = None

    session = make_session()
    msg = make_message("任意内容")
    result = detector.check_drift(msg, session)

    assert result.unavailable is True
    assert result.score is None
    assert result.is_drifted is False


def test_compute_similarity_returns_1_when_unavailable():
    """sentence-transformers 不可用时 compute_similarity 返回 1.0，需求 24.6"""
    detector = DriftDetector.__new__(DriftDetector)
    detector._model_name = DriftDetector.DEFAULT_MODEL
    detector._topic_embeddings = {}
    detector._available = False
    detector._model = None

    score = detector.compute_similarity("任意文本", "任意话题")
    assert score == 1.0


def test_check_drift_unavailable_does_not_trigger_drift():
    """服务不可用时不触发漂移（is_drifted=False），需求 24.6"""
    detector = DriftDetector.__new__(DriftDetector)
    detector._model_name = DriftDetector.DEFAULT_MODEL
    detector._topic_embeddings = {}
    detector._available = False
    detector._model = None

    session = make_session(drift_threshold=0.9)  # 高阈值
    msg = make_message("完全不相关的内容")
    result = detector.check_drift(msg, session)

    assert result.is_drifted is False
    assert result.unavailable is True


# ---------------------------------------------------------------------------
# 4. handle_consecutive_drift：连续 3 条时返回话题重申提示
# ---------------------------------------------------------------------------

def test_handle_consecutive_drift_returns_reminder_at_3():
    """连续 3 条漂移时返回话题重申提示，需求 24.3"""
    detector = DriftDetector()
    session = make_session(topic="量子计算的应用前景")

    result = detector.handle_consecutive_drift(session, consecutive_count=3)

    assert result is not None
    assert isinstance(result, str)
    assert len(result) > 0


def test_handle_consecutive_drift_returns_none_below_3():
    """连续漂移不足 3 条时返回 None，需求 24.3"""
    detector = DriftDetector()
    session = make_session()

    assert detector.handle_consecutive_drift(session, consecutive_count=0) is None
    assert detector.handle_consecutive_drift(session, consecutive_count=1) is None
    assert detector.handle_consecutive_drift(session, consecutive_count=2) is None


def test_handle_consecutive_drift_returns_reminder_above_3():
    """连续漂移超过 3 条时也返回话题重申提示，需求 24.3"""
    detector = DriftDetector()
    session = make_session(topic="气候变化")

    result = detector.handle_consecutive_drift(session, consecutive_count=5)
    assert result is not None


def test_handle_consecutive_drift_contains_topic():
    """话题重申提示包含原始 Topic 原文，需求 24.3"""
    detector = DriftDetector()
    topic = "区块链技术在金融领域的应用"
    session = make_session(topic=topic)

    result = detector.handle_consecutive_drift(session, consecutive_count=3)

    assert result is not None
    assert topic in result


# ---------------------------------------------------------------------------
# 5. build_topic_reminder：包含原始 Topic
# ---------------------------------------------------------------------------

def test_build_topic_reminder_contains_topic():
    """build_topic_reminder 返回的字符串包含原始 Topic，需求 24.4"""
    detector = DriftDetector()
    topic = "深度学习在医疗诊断中的应用"
    session = make_session(topic=topic)

    reminder = detector.build_topic_reminder(session)

    assert topic in reminder


def test_build_topic_reminder_returns_non_empty_string():
    """build_topic_reminder 返回非空字符串，需求 24.4"""
    detector = DriftDetector()
    session = make_session(topic="任意话题")

    reminder = detector.build_topic_reminder(session)

    assert isinstance(reminder, str)
    assert len(reminder.strip()) > 0


def test_build_topic_reminder_different_topics():
    """不同 Topic 的提示字符串包含各自的 Topic，需求 24.4"""
    detector = DriftDetector()
    topic1 = "太空探索的未来"
    topic2 = "可再生能源发展"

    session1 = make_session(topic=topic1)
    session2 = make_session(topic=topic2)

    reminder1 = detector.build_topic_reminder(session1)
    reminder2 = detector.build_topic_reminder(session2)

    assert topic1 in reminder1
    assert topic2 in reminder2
    assert topic1 not in reminder2
    assert topic2 not in reminder1


# ---------------------------------------------------------------------------
# 6. DriftResult 数据类基本行为
# ---------------------------------------------------------------------------

def test_drift_result_default_unavailable_false():
    """DriftResult 默认 unavailable=False，需求 24.1"""
    result = DriftResult(score=0.8, is_drifted=False)
    assert result.unavailable is False


def test_drift_result_unavailable_true():
    """DriftResult 可以设置 unavailable=True，需求 24.6"""
    result = DriftResult(score=None, is_drifted=False, unavailable=True)
    assert result.unavailable is True
    assert result.score is None


# ---------------------------------------------------------------------------
# 7. 异常处理：compute_similarity 内部异常时降级返回 1.0
# ---------------------------------------------------------------------------

def test_compute_similarity_handles_exception_gracefully():
    """compute_similarity 内部异常时返回 1.0，不抛出异常，需求 24.6"""
    detector = DriftDetector.__new__(DriftDetector)
    detector._model_name = DriftDetector.DEFAULT_MODEL
    detector._topic_embeddings = {}
    detector._available = True

    mock_model = MagicMock()
    mock_model.encode.side_effect = RuntimeError("模型推理失败")
    detector._model = mock_model

    score = detector.compute_similarity("文本", "话题")
    assert score == 1.0


def test_check_drift_handles_exception_gracefully():
    """check_drift 内部异常时不抛出异常，正常返回结果，需求 24.6"""
    detector = DriftDetector.__new__(DriftDetector)
    detector._model_name = DriftDetector.DEFAULT_MODEL
    detector._topic_embeddings = {}
    detector._available = True

    mock_model = MagicMock()
    mock_model.encode.side_effect = RuntimeError("模型推理失败")
    detector._model = mock_model

    session = make_session()
    msg = make_message("测试内容")
    # compute_similarity 内部捕获异常返回 1.0，check_drift 不应抛出异常
    result = detector.check_drift(msg, session)

    # 不抛出异常，返回合法的 DriftResult
    assert isinstance(result, DriftResult)
    assert result.is_drifted is False  # score=1.0 不触发漂移
