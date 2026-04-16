"""
ConsensusDetector：辩论模式的共识检测组件
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Optional


@dataclass
class ConsensusResult:
    """共识检测结果。"""

    is_consensus: bool
    agreeing_party: Optional[str] = None
    consensus_summary: str = ""


class ConsensusDetector:
    """
    使用明确同意关键词做第一层检测。
    当前实现刻意保守，只在明显同意时返回 True，
    避免把礼貌性认可误判为共识。
    """

    CONSENSUS_KEYWORDS = (
        "我同意",
        "我接受",
        "你说得对",
        "达成一致",
        "我们一致认为",
        "I agree",
        "you're right",
        "we agree",
        "accepted",
    )

    NON_CONSENSUS_HINTS = (
        "部分同意",
        "不完全同意",
        "但是",
        "不过",
        "yet",
        "however",
        "partly agree",
    )

    def detect(self, sender_id: str, content: str) -> ConsensusResult:
        """检测单条消息是否表达了明确共识。"""
        normalized = content.strip().lower()
        if not normalized:
            return ConsensusResult(is_consensus=False)

        if any(hint in normalized for hint in self._lowered(self.NON_CONSENSUS_HINTS)):
            return ConsensusResult(is_consensus=False)

        matched = [
            keyword for keyword in self.CONSENSUS_KEYWORDS
            if keyword.lower() in normalized
        ]
        if not matched:
            return ConsensusResult(is_consensus=False)

        return ConsensusResult(
            is_consensus=True,
            agreeing_party=sender_id,
            consensus_summary=content[:160],
        )

    @staticmethod
    def _lowered(items: Iterable[str]) -> Iterable[str]:
        for item in items:
            yield item.lower()
