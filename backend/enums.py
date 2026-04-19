"""
枚举定义：协作模式、消息类型、会话状态、Provider 类型等
"""
from enum import Enum


class CollaborationMode(str, Enum):
    """16 种协作模式"""
    CHAT = "chat"
    BRAINSTORM = "brainstorm"
    CODE_COLLABORATION = "code_collaboration"
    CODE_WORKSPACE = "code_workspace"
    DATA_ANALYSIS = "data_analysis"
    DEBATE = "debate"
    WEREWOLF = "werewolf"
    MURDER_MYSTERY = "murder_mystery"
    UNDERCOVER = "undercover"
    MOCK_TRIAL = "mock_trial"
    ROLE_PLAY = "role_play"
    SOCRATIC_DIALOGUE = "socratic_dialogue"
    PEER_REVIEW = "peer_review"
    MOCK_INTERVIEW = "mock_interview"
    STORY_CHAIN = "story_chain"
    NEGOTIATION = "negotiation"


class MessageType(str, Enum):
    """消息类型"""
    DIALOGUE = "dialogue"
    TOOL_OUTPUT = "tool_output"
    USER_INTERVENTION = "user_intervention"


class SessionStatus(str, Enum):
    """会话状态"""
    ACTIVE = "active"
    ENDED = "ended"
    PAUSED = "paused"


class ProviderType(str, Enum):
    """模型提供商类型"""
    OPENAI = "openai"
    ANTHROPIC = "anthropic"
    GOOGLE = "google"
    GROQ = "groq"
    MISTRAL = "mistral"
    XAI = "xai"
    OLLAMA = "ollama"
    LM_STUDIO = "lm_studio"
    VLLM = "vllm"
    OPENROUTER = "openrouter"
    LITELLM = "litellm"
    GATEWAY = "gateway"
    CUSTOM = "custom"


class APIFormat(str, Enum):
    """API 格式"""
    OPENAI_COMPLETIONS = "openai-completions"
    ANTHROPIC_MESSAGES = "anthropic-messages"


class AuthType(str, Enum):
    """认证方式"""
    API_KEY = "api_key"
    OAUTH = "oauth"
    BEARER = "bearer"
    IAM = "iam"
    ADC = "adc"
    HELPER = "helper"
