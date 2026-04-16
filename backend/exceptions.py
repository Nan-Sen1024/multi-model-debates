"""
异常类定义：ValidationError 等平台异常
"""
from typing import Optional


class MultiModelDebateError(Exception):
    """平台基础异常"""
    pass


class ValidationError(MultiModelDebateError):
    """参数校验失败异常"""

    def __init__(self, message: str, field: Optional[str] = None):
        super().__init__(message)
        self.message = message
        self.field = field

    def to_dict(self) -> dict:
        return {
            "error": {
                "code": "VALIDATION_ERROR",
                "message": self.message,
                "field": self.field,
                "session_id": None,
            }
        }


class ProviderUnavailableError(MultiModelDebateError):
    """Provider 不可达异常"""

    def __init__(self, message: str, provider: Optional[str] = None):
        super().__init__(message)
        self.message = message
        self.provider = provider


class AuthenticationError(MultiModelDebateError):
    """认证失败异常"""

    def __init__(self, message: str, provider: Optional[str] = None):
        super().__init__(message)
        self.message = message
        self.provider = provider


class ContextOverflowError(MultiModelDebateError):
    """上下文溢出异常"""
    pass


class CompressionError(MultiModelDebateError):
    """摘要压缩失败异常"""
    pass


class DriftDetectorError(MultiModelDebateError):
    """漂移检测服务不可用异常"""
    pass


class GameRuleViolation(MultiModelDebateError):
    """游戏规则违规异常（如私有信息泄露）"""
    pass


class CheckpointWriteError(MultiModelDebateError):
    """检查点写入失败异常"""
    pass


class SnapshotUpdateError(MultiModelDebateError):
    """快照更新失败异常"""
    pass
