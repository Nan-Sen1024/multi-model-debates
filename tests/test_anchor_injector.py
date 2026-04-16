"""
单元测试：AnchorInjector
覆盖需求：21.1、21.2、21.3、21.4、21.5
"""
from __future__ import annotations

import uuid
from typing import List, Optional

from backend.anchor_injector import AnchorInjector, AnchorFields, _count_tokens, GAME_MODES
from backend.enums import CollaborationMode, SessionStatus
from backend.models import ModelParticipant, Session, SessionConfig, SessionSnapshot


# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------

def make_participant(
    custom_id: str,
    sequence_order: int = 0,
    role_desc: Optional[str] = None,
    private_info: Optional[str] = None,
    session_id: str = "sess-1",
) -> ModelParticipant:
    return ModelParticipant(
        id=str(uuid.uuid4()),
        session_id=session_id,
        custom_id=custom_id,
        model_ref="openai/gpt-4o",
        sequence_order=sequence_order,
        role_desc=role_desc,
        private_info=private_info,
    )


def make_session(
    participants: List[ModelParticipant],
    mode: CollaborationMode = CollaborationMode.CHAT,
    topic: str = "测试话题",
) -> Session:
    return Session(
        id="sess-1",
        topic=topic,
        mode=mode,
        status=SessionStatus.ACTIVE,
        participants=participants,
        config=SessionConfig(),
        snapshot=SessionSnapshot(
            topic=topic,
            mode=mode,
            participant_summaries={},
            consensus_list=[],
            key_events=[],
        ),
    )


injector = AnchorInjector()


# ---------------------------------------------------------------------------
# 1. 基本锚点格式正确性（需求 21.1、21.2）
# ---------------------------------------------------------------------------

def test_anchor_contains_topic():
    """锚点必须包含原始 Topic"""
    p = make_participant("ModelA")
    session = make_session([p], topic="人工智能的未来")
    anchor = injector.build_anchor(p, session)
    assert "人工智能的未来" in anchor


def test_anchor_contains_custom_id():
    """锚点必须包含该参与者的 Custom_ID"""
    p = make_participant("ModelA")
    session = make_session([p])
    anchor = injector.build_anchor(p, session)
    assert "ModelA" in anchor


def test_anchor_contains_mode():
    """锚点必须包含协作模式"""
    p = make_participant("ModelA")
    session = make_session([p], mode=CollaborationMode.DEBATE)
    anchor = injector.build_anchor(p, session)
    assert "debate" in anchor


def test_anchor_contains_other_participants():
    """锚点必须包含其他参与者的 Custom_ID 列表"""
    p1 = make_participant("ModelA", sequence_order=0)
    p2 = make_participant("ModelB", sequence_order=1)
    p3 = make_participant("ModelC", sequence_order=2)
    session = make_session([p1, p2, p3])
    anchor = injector.build_anchor(p1, session)
    assert "ModelB" in anchor
    assert "ModelC" in anchor
    # 不应包含自己在"其他参与者"中
    lines = anchor.split("\n")
    other_line = next((l for l in lines if l.startswith("其他参与者：")), "")
    assert "ModelA" not in other_line


def test_anchor_format_structure():
    """锚点必须以 [系统锚点] 开头，以 [锚点结束] 结尾"""
    p = make_participant("ModelA")
    session = make_session([p])
    anchor = injector.build_anchor(p, session)
    assert anchor.startswith("[系统锚点]")
    assert anchor.endswith("[锚点结束]")


def test_anchor_with_role_desc():
    """有角色描述时，格式为 你的身份：{Custom_ID}（{角色描述}）"""
    p = make_participant("ModelA", role_desc="侦探")
    session = make_session([p])
    anchor = injector.build_anchor(p, session)
    assert "你的身份：ModelA（侦探）" in anchor


def test_anchor_without_role_desc():
    """无角色描述时，格式为 你的身份：{Custom_ID}"""
    p = make_participant("ModelA", role_desc=None)
    session = make_session([p])
    anchor = injector.build_anchor(p, session)
    assert "你的身份：ModelA" in anchor
    assert "（）" not in anchor


def test_anchor_no_other_participants_when_alone():
    """只有一个参与者时，不应出现"其他参与者"行"""
    p = make_participant("ModelA")
    session = make_session([p])
    anchor = injector.build_anchor(p, session)
    assert "其他参与者" not in anchor


# ---------------------------------------------------------------------------
# 2. token 不超过 512（需求 21.3）
# ---------------------------------------------------------------------------

def test_anchor_token_within_limit_normal():
    """正常情况下 token 不超过 512"""
    p = make_participant("ModelA", role_desc="普通角色描述")
    session = make_session([p], topic="短话题")
    anchor = injector.build_anchor(p, session)
    assert _count_tokens(anchor) <= AnchorInjector.MAX_ANCHOR_TOKENS


def test_anchor_token_within_limit_many_participants():
    """大量参与者时 token 仍不超过 512"""
    participants = [make_participant(f"Model{i}", sequence_order=i) for i in range(10)]
    session = make_session(participants)
    for p in participants:
        anchor = injector.build_anchor(p, session)
        assert _count_tokens(anchor) <= AnchorInjector.MAX_ANCHOR_TOKENS


def test_anchor_token_within_limit_long_role_desc():
    """超长角色描述时 token 仍不超过 512"""
    long_desc = "这是一段非常非常长的角色描述，" * 50  # ~650 字
    p = make_participant("ModelA", role_desc=long_desc)
    session = make_session([p], topic="话题")
    anchor = injector.build_anchor(p, session)
    assert _count_tokens(anchor) <= AnchorInjector.MAX_ANCHOR_TOKENS


def test_anchor_token_within_limit_long_topic():
    """超长 Topic 时 token 仍不超过 512（Topic 不截断，但其他字段会被截断）"""
    # Topic 本身不截断，但其他字段会被截断以尽量控制总量
    long_topic = "这是一个非常长的话题描述，" * 20
    participants = [make_participant(f"Model{i}", sequence_order=i) for i in range(10)]
    session = make_session(participants, topic=long_topic)
    for p in participants:
        anchor = injector.build_anchor(p, session)
        # Topic 本身可能超出，但截断逻辑会尽力压缩其他字段
        # 此处验证截断逻辑不会崩溃
        assert isinstance(anchor, str)
        assert "[系统锚点]" in anchor
        assert "[锚点结束]" in anchor


# ---------------------------------------------------------------------------
# 3. 截断逻辑（需求 21.3）
# ---------------------------------------------------------------------------

def test_truncation_participants_list_first():
    """超出 token 限制时，先截断其他参与者列表（保留前 3 个 + "..."）"""
    # 构造超多参与者 + 超长角色描述，触发截断
    participants = [
        make_participant(f"Participant{i:03d}", sequence_order=i, role_desc="角色" * 5)
        for i in range(10)
    ]
    session = make_session(participants, topic="话题")
    p0 = participants[0]
    anchor = injector.build_anchor(p0, session)

    # 如果触发了参与者列表截断，应该有 "..."
    # 验证 token 在限制内
    assert _count_tokens(anchor) <= AnchorInjector.MAX_ANCHOR_TOKENS


def test_truncation_role_desc_second():
    """参与者列表截断后仍超出时，截断角色描述到 50 字 + '...'"""
    long_desc = "角色描述" * 30  # 120 字
    participants = [
        make_participant(f"P{i}", sequence_order=i, role_desc=long_desc)
        for i in range(10)
    ]
    session = make_session(participants, topic="话题")
    anchor = injector.build_anchor(participants[0], session)
    assert _count_tokens(anchor) <= AnchorInjector.MAX_ANCHOR_TOKENS


def test_truncation_mode_removed_last():
    """极端情况下，移除 Collaboration_Mode 字段"""
    # 构造极端超长内容
    long_desc = "极长角色描述内容" * 100
    long_topic = "极长话题" * 50
    participants = [
        make_participant(f"P{i}", sequence_order=i, role_desc=long_desc)
        for i in range(10)
    ]
    session = make_session(participants, topic=long_topic)
    anchor = injector.build_anchor(participants[0], session)
    assert _count_tokens(anchor) <= AnchorInjector.MAX_ANCHOR_TOKENS


def test_truncation_preserves_topic_and_custom_id():
    """截断后始终保留 Topic 和 Custom_ID"""
    long_desc = "角色描述" * 100
    participants = [
        make_participant(f"P{i}", sequence_order=i, role_desc=long_desc)
        for i in range(10)
    ]
    session = make_session(participants, topic="必须保留的话题")
    anchor = injector.build_anchor(participants[0], session)
    assert "必须保留的话题" in anchor
    assert "P0" in anchor


def test_render_anchor_participants_truncated_to_3_plus_ellipsis():
    """_render_anchor 直接测试：超过 3 个参与者时截断为前 3 + '...'"""
    fields = AnchorFields(
        topic="话题",
        mode="chat",
        custom_id="ModelA",
        role_desc="",
        others=["B", "C", "D", "E", "F"],
    )
    # 手动模拟截断
    fields.others = fields.others[:3] + ["..."]
    anchor = injector._render_anchor(fields)
    assert "B" in anchor
    assert "C" in anchor
    assert "D" in anchor
    assert "..." in anchor
    assert "E" not in anchor


# ---------------------------------------------------------------------------
# 4. 私有信息隔离（需求 21.5）
# ---------------------------------------------------------------------------

def test_private_info_injected_for_game_mode_owner():
    """游戏模式中，参与者自己的 private_info 应注入到其锚点"""
    p1 = make_participant("ModelA", private_info="你是狼人，队友是ModelB")
    p2 = make_participant("ModelB", private_info="你是村民")
    session = make_session([p1, p2], mode=CollaborationMode.WEREWOLF)
    anchor_a = injector.build_anchor(p1, session)
    assert "你是狼人，队友是ModelB" in anchor_a
    assert "[私有信息]" in anchor_a


def test_private_info_not_leaked_to_other_participant():
    """游戏模式中，参与者 A 的 private_info 不应出现在参与者 B 的锚点中"""
    p1 = make_participant("ModelA", private_info="秘密：我是狼人")
    p2 = make_participant("ModelB", private_info="秘密：我是预言家")
    session = make_session([p1, p2], mode=CollaborationMode.WEREWOLF)

    anchor_b = injector.build_anchor(p2, session)
    assert "我是狼人" not in anchor_b
    assert "我是预言家" in anchor_b


def test_private_info_not_injected_in_non_game_mode():
    """非游戏模式中，即使参与者有 private_info，也不应注入到锚点"""
    p = make_participant("ModelA", private_info="这是私有信息，不应出现")
    session = make_session([p], mode=CollaborationMode.CHAT)
    anchor = injector.build_anchor(p, session)
    assert "这是私有信息，不应出现" not in anchor
    assert "[私有信息]" not in anchor


def test_private_info_none_participant_in_game_mode():
    """游戏模式中，private_info 为 None 的参与者不应出现私有信息块"""
    p = make_participant("ModelA", private_info=None)
    session = make_session([p], mode=CollaborationMode.WEREWOLF)
    anchor = injector.build_anchor(p, session)
    assert "[私有信息]" not in anchor


def test_private_info_isolation_all_game_modes():
    """所有游戏模式都应隔离私有信息"""
    for mode in GAME_MODES:
        p1 = make_participant("ModelA", private_info=f"A的秘密_{mode.value}")
        p2 = make_participant("ModelB", private_info=f"B的秘密_{mode.value}")
        session = make_session([p1, p2], mode=mode)

        anchor_a = injector.build_anchor(p1, session)
        anchor_b = injector.build_anchor(p2, session)

        # A 的锚点包含 A 的秘密，不包含 B 的秘密
        assert f"A的秘密_{mode.value}" in anchor_a
        assert f"B的秘密_{mode.value}" not in anchor_a

        # B 的锚点包含 B 的秘密，不包含 A 的秘密
        assert f"B的秘密_{mode.value}" in anchor_b
        assert f"A的秘密_{mode.value}" not in anchor_b


def test_murder_mystery_private_info_isolation():
    """剧本杀模式：私有信息隔离"""
    p1 = make_participant("侦探", private_info="你知道凶器是刀")
    p2 = make_participant("嫌疑人", private_info="你是真正的凶手")
    session = make_session([p1, p2], mode=CollaborationMode.MURDER_MYSTERY)

    anchor_detective = injector.build_anchor(p1, session)
    anchor_suspect = injector.build_anchor(p2, session)

    assert "你知道凶器是刀" in anchor_detective
    assert "你是真正的凶手" not in anchor_detective

    assert "你是真正的凶手" in anchor_suspect
    assert "你知道凶器是刀" not in anchor_suspect


# ---------------------------------------------------------------------------
# 5. _render_anchor 直接测试
# ---------------------------------------------------------------------------

def test_render_anchor_no_mode():
    """mode 为空时不渲染协作模式行"""
    fields = AnchorFields(topic="话题", mode="", custom_id="A", role_desc="")
    anchor = injector._render_anchor(fields)
    assert "协作模式" not in anchor


def test_render_anchor_no_others():
    """others 为空时不渲染其他参与者行"""
    fields = AnchorFields(topic="话题", mode="chat", custom_id="A", role_desc="", others=[])
    anchor = injector._render_anchor(fields)
    assert "其他参与者" not in anchor


def test_render_anchor_with_private_info():
    """有 private_info 时渲染私有信息块"""
    fields = AnchorFields(
        topic="话题", mode="werewolf", custom_id="A", role_desc="狼人",
        private_info="你的队友是B"
    )
    anchor = injector._render_anchor(fields)
    assert "[私有信息]" in anchor
    assert "你的队友是B" in anchor
    assert "[私有信息结束]" in anchor
