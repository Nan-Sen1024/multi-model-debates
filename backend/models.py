"""
核心 Python 数据类定义
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional

from .enums import (
    APIFormat,
    AuthType,
    CollaborationMode,
    MessageType,
    ProviderType,
    SessionStatus,
)


@dataclass
class OAuthToken:
    access_token: str
    refresh_token: Optional[str] = None
    expires_at: Optional[datetime] = None
    token_type: str = "Bearer"


@dataclass
class AuthConfig:
    auth_type: AuthType
    api_key: Optional[str] = None           # 加密存储
    bearer_token: Optional[str] = None
    helper_script: Optional[str] = None     # API_Key_Helper 脚本路径
    oauth_token: Optional[OAuthToken] = None
    metadata: Dict[str, object] = field(default_factory=dict)


@dataclass
class ProviderConfig:
    id: str
    name: str
    provider_type: ProviderType
    base_url: Optional[str]
    api_format: APIFormat
    auth_type: AuthType
    auth_config: AuthConfig
    fallback_ids: List[str] = field(default_factory=list)
    is_active: bool = True


@dataclass
class ModelParticipant:
    id: str
    session_id: str
    custom_id: str
    model_ref: str                          # "provider/model"
    sequence_order: int
    provider_id: Optional[str] = None
    display_name: Optional[str] = None
    role_desc: Optional[str] = None
    private_info: Optional[str] = None     # 加密存储，游戏模式专用
    is_active: bool = True


@dataclass
class SessionConfig:
    max_rounds: int = 20
    drift_threshold: float = 0.4
    retention_window: int = 10
    context_threshold: float = 0.7
    summary_model: Optional[str] = None
    delegate_all_tools: bool = False


@dataclass
class SessionSnapshot:
    topic: str
    mode: CollaborationMode
    participant_summaries: Dict[str, str]   # custom_id -> 立场摘要（≤100字）
    consensus_list: List[str]               # 已达成共识列表
    key_events: List[str]                   # 关键事件（含用户插入）


@dataclass
class Session:
    id: str
    topic: str
    mode: CollaborationMode
    status: SessionStatus
    participants: List[ModelParticipant]
    config: SessionConfig
    snapshot: SessionSnapshot
    current_round: int = 0
    next_speaker_index: int = 0
    created_at: datetime = field(default_factory=datetime.utcnow)
    updated_at: datetime = field(default_factory=datetime.utcnow)


@dataclass
class CollaborationMessage:
    id: str
    session_id: str
    sender_id: str
    message_type: MessageType
    content: str
    is_masked: bool = False
    is_compressed: bool = False
    drift_score: Optional[float] = None
    round_number: int = 0
    created_at: datetime = field(default_factory=datetime.utcnow)


@dataclass
class ContextAnchor:
    topic: str
    mode: str
    custom_id: str
    role_desc: str
    other_participants: List[str]
    private_info: Optional[str] = None     # 仅游戏模式，仅对应参与者可见


@dataclass
class Checkpoint:
    id: str
    session_id: str
    topic: str
    mode: str
    snapshot: SessionSnapshot
    next_step: str
    created_at: datetime = field(default_factory=datetime.utcnow)
