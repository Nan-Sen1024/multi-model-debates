"""
Collaboration mode strategies.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Dict, List, Optional

from .consensus_detector import ConsensusDetector
from .enums import CollaborationMode, MessageType
from .game_master import GameMaster
from .models import CollaborationMessage, ModelParticipant, Session


@dataclass
class TerminationResult:
    """策略终止结果。"""

    should_terminate: bool
    reason: str
    summary_hint: str = ""


class CollaborationModeStrategy(ABC):
    """协作模式策略基类。"""

    def initialize_session(self, session: Session) -> None:
        """在会话创建后初始化特定模式的附加状态。"""

    @abstractmethod
    def get_next_speaker(
        self,
        session: Session,
        messages: List[CollaborationMessage],
    ) -> Optional[ModelParticipant]:
        raise NotImplementedError

    @abstractmethod
    def check_termination(
        self,
        session: Session,
        messages: List[CollaborationMessage],
    ) -> Optional[TerminationResult]:
        raise NotImplementedError

    @abstractmethod
    def build_system_prompt(
        self,
        participant: ModelParticipant,
        session: Session,
    ) -> str:
        raise NotImplementedError

    def on_message_received(
        self,
        message: CollaborationMessage,
        session: Session,
    ) -> None:
        """处理消息后的状态更新，默认无操作。"""

    def build_summary(
        self,
        session: Session,
        messages: List[CollaborationMessage],
        reason: str,
    ) -> str:
        """生成模式相关的摘要。"""
        participant_lines = [
            f"{custom_id}：{summary or '暂无总结'}"
            for custom_id, summary in session.snapshot.participant_summaries.items()
        ]
        return "\n".join(
            [
                f"会话结束原因：{reason}",
                f"模式：{session.mode.value}",
                *participant_lines,
            ]
        )


class RoundRobinStrategy(CollaborationModeStrategy):
    """顺序轮询策略。"""

    def get_next_speaker(
        self,
        session: Session,
        messages: List[CollaborationMessage],
    ) -> Optional[ModelParticipant]:
        if not session.participants:
            return None

        total = len(session.participants)
        for offset in range(total):
            index = (session.next_speaker_index + offset) % total
            participant = session.participants[index]
            if participant.is_active:
                session.next_speaker_index = index
                return participant
        return None

    def check_termination(
        self,
        session: Session,
        messages: List[CollaborationMessage],
    ) -> Optional[TerminationResult]:
        if session.current_round >= session.config.max_rounds and session.next_speaker_index == 0:
            return TerminationResult(True, "max_rounds_reached")
        return None

    def build_system_prompt(
        self,
        participant: ModelParticipant,
        session: Session,
    ) -> str:
        return (
            f"你是参与者 {participant.custom_id}。"
            f"当前模式为 {session.mode.value}，请围绕话题“{session.topic}”继续推进。"
        )


class OneRoundStrategy(RoundRobinStrategy):
    """只执行一轮的模式。"""

    def check_termination(
        self,
        session: Session,
        messages: List[CollaborationMessage],
    ) -> Optional[TerminationResult]:
        if session.current_round >= 1 and session.next_speaker_index == 0:
            return TerminationResult(True, "single_round_completed")
        return super().check_termination(session, messages)


class ChatStrategy(RoundRobinStrategy):
    """自由聊天模式。"""

    def build_system_prompt(self, participant: ModelParticipant, session: Session) -> str:
        return (
            f"你是多模型协作聊天中的参与者 {participant.custom_id}。"
            "请阅读完整历史，承接上文，并给出对话式回复。"
        )


class BrainstormStrategy(OneRoundStrategy):
    """头脑风暴模式。"""

    def build_system_prompt(self, participant: ModelParticipant, session: Session) -> str:
        return (
            f"你是头脑风暴参与者 {participant.custom_id}。"
            "请独立提出创意列表，不要重复前文已有方案，并标明最值得深挖的一项。"
        )

    def build_summary(self, session: Session, messages: List[CollaborationMessage], reason: str) -> str:
        ideas = [
            f"{message.sender_id}：{message.content[:120]}"
            for message in messages
            if message.message_type == MessageType.DIALOGUE
        ]
        return "\n".join(["头脑风暴汇总：", *ideas]) if ideas else super().build_summary(session, messages, reason)


class CodeCollabStrategy(OneRoundStrategy):
    """代码协作分析模式。"""

    ANALYSIS_ANGLES = ("安全性", "性能", "可读性", "测试性", "架构设计")

    def build_system_prompt(self, participant: ModelParticipant, session: Session) -> str:
        angle = self.ANALYSIS_ANGLES[(participant.sequence_order - 1) % len(self.ANALYSIS_ANGLES)]
        return (
            f"你是代码协作分析参与者 {participant.custom_id}。"
            f"请重点从{angle}角度分析输入代码，结论尽量结构化，并在有代码时用代码块输出。"
        )

    def build_summary(self, session: Session, messages: List[CollaborationMessage], reason: str) -> str:
        suggestions = [
            f"{message.sender_id}：{message.content[:160]}"
            for message in messages
            if message.message_type == MessageType.DIALOGUE
        ]
        return "\n".join(["代码协作总结：", *suggestions]) if suggestions else super().build_summary(session, messages, reason)


class WorkspaceStrategy(RoundRobinStrategy):
    """代码工作区模式。"""

    def build_system_prompt(self, participant: ModelParticipant, session: Session) -> str:
        workspace_name = session.config.workspace.display_name if session.config.workspace and session.config.workspace.display_name else "本地代码工作区"
        return (
            f"你是代码工作区参与者 {participant.custom_id}。"
            f"当前工作区：{workspace_name}。"
            "请结合共享仓库上下文、历史输出和当前任务，像工程代理一样推进任务。"
            "如果任务要求修复、实现、修改代码或验证结果，优先完成实际执行，不要停留在高层建议。"
            "最终答复保持简洁，并明确列出：修改的文件、运行的命令、验证结果、剩余阻塞。"
        )


class DataAnalysisStrategy(OneRoundStrategy):
    """数据分析模式。"""

    def build_system_prompt(self, participant: ModelParticipant, session: Session) -> str:
        return (
            f"你是数据分析参与者 {participant.custom_id}。"
            "请给出关键发现、可能风险和下一步建议，必要时以表格或分点展示。"
        )


class DebateStrategy(RoundRobinStrategy):
    """辩论模式。"""

    def __init__(self, consensus_detector: ConsensusDetector):
        self.consensus_detector = consensus_detector

    def build_system_prompt(self, participant: ModelParticipant, session: Session) -> str:
        return (
            f"你是辩论参与者 {participant.custom_id}。"
            "请明确表达立场，回应其他观点；只有在确实同意时才明确说明共识。"
        )

    def on_message_received(self, message: CollaborationMessage, session: Session) -> None:
        result = self.consensus_detector.detect(message.sender_id, message.content)
        if result.is_consensus and result.consensus_summary:
            if result.consensus_summary not in session.snapshot.consensus_list:
                session.snapshot.consensus_list.append(result.consensus_summary)

    def check_termination(self, session: Session, messages: List[CollaborationMessage]) -> Optional[TerminationResult]:
        if session.snapshot.consensus_list:
            return TerminationResult(True, "consensus_reached", session.snapshot.consensus_list[-1])
        if session.current_round >= session.config.max_rounds and session.next_speaker_index == 0:
            return TerminationResult(True, "max_rounds_reached")
        return None

    def build_summary(self, session: Session, messages: List[CollaborationMessage], reason: str) -> str:
        parts = ["辩论总结："]
        for custom_id, summary in session.snapshot.participant_summaries.items():
            parts.append(f"{custom_id} 立场：{summary or '暂无'}")
        if session.snapshot.consensus_list:
            parts.append("共识：")
            parts.extend(session.snapshot.consensus_list)
        return "\n".join(parts)


class GameStrategyBase(RoundRobinStrategy):
    """游戏类模式基础策略。"""

    def __init__(self, game_master: GameMaster):
        self.game_master = game_master

    def initialize_session(self, session: Session) -> None:
        roles = self.game_master.assign_roles(session.participants, session.mode)
        for participant in session.participants:
            role = roles.get(participant.id)
            if role is None:
                continue
            participant.role_desc = role.name
            participant.private_info = self.game_master.inject_private_info(participant, role)

    def build_system_prompt(self, participant: ModelParticipant, session: Session) -> str:
        return (
            f"你是游戏模式参与者 {participant.custom_id}，当前身份为 {participant.role_desc or '未分配'}。"
            "请遵守角色设定，不要泄露他人的私有信息。"
        )

    def on_message_received(self, message: CollaborationMessage, session: Session) -> None:
        participant = next((p for p in session.participants if p.custom_id == message.sender_id), None)
        if participant is None:
            return
        message.content = self.game_master.filter_private_info_leak(
            message.content,
            participant,
            session.participants,
        )


class WerewolfStrategy(GameStrategyBase):
    """狼人杀模式。"""

    def check_termination(self, session: Session, messages: List[CollaborationMessage]) -> Optional[TerminationResult]:
        win = self.game_master.check_win_condition(session)
        if win:
            return TerminationResult(True, win)
        if session.current_round >= min(session.config.max_rounds, 15) and session.next_speaker_index == 0:
            return TerminationResult(True, "draw")
        return None


class MurderMysteryStrategy(GameStrategyBase):
    """剧本杀模式。"""

    def check_termination(self, session: Session, messages: List[CollaborationMessage]) -> Optional[TerminationResult]:
        if session.current_round >= 5 and session.next_speaker_index == 0:
            return TerminationResult(True, "investigation_complete")
        return super().check_termination(session, messages)


class UndercoverStrategy(GameStrategyBase):
    """谁是卧底模式。"""

    def check_termination(self, session: Session, messages: List[CollaborationMessage]) -> Optional[TerminationResult]:
        win = self.game_master.check_win_condition(session)
        if win:
            return TerminationResult(True, win)
        if session.current_round >= session.config.max_rounds and session.next_speaker_index == 0:
            return TerminationResult(True, "round_limit_reached")
        return None


class MockTrialStrategy(RoundRobinStrategy):
    """模拟法庭模式。"""

    STAGES = ("开庭陈述", "举证质证", "辩论", "最终陈述", "裁决")

    def build_system_prompt(self, participant: ModelParticipant, session: Session) -> str:
        stage = self.STAGES[min(session.current_round, len(self.STAGES) - 1)]
        return (
            f"你是模拟法庭参与者 {participant.custom_id}。"
            f"当前阶段：{stage}。请基于该阶段职责发言。"
        )

    def check_termination(self, session: Session, messages: List[CollaborationMessage]) -> Optional[TerminationResult]:
        if session.current_round >= len(self.STAGES) and session.next_speaker_index == 0:
            return TerminationResult(True, "trial_complete")
        return None


class RolePlayStrategy(RoundRobinStrategy):
    """角色扮演模式。"""

    def build_system_prompt(self, participant: ModelParticipant, session: Session) -> str:
        return (
            f"你是角色扮演参与者 {participant.custom_id}。"
            "请保持世界观一致，延续剧情，不要脱离角色。"
        )


class SocraticStrategy(RoundRobinStrategy):
    """苏格拉底对话模式。"""

    def build_system_prompt(self, participant: ModelParticipant, session: Session) -> str:
        role = "提问者" if participant.sequence_order == 1 else "回答者"
        return (
            f"你是苏格拉底对话中的{role} {participant.custom_id}。"
            "请围绕前一轮内容持续追问或回答，推动推理深入。"
        )


class PeerReviewStrategy(RoundRobinStrategy):
    """多模型评审模式。"""

    def build_system_prompt(self, participant: ModelParticipant, session: Session) -> str:
        role = "Producer" if participant.sequence_order == 1 else "Reviewer"
        return (
            f"你是多模型评审中的 {role} {participant.custom_id}。"
            "Producer 负责生成内容，Reviewer 负责打分和评论。"
        )

    def check_termination(self, session: Session, messages: List[CollaborationMessage]) -> Optional[TerminationResult]:
        if session.current_round >= 3 and session.next_speaker_index == 0:
            return TerminationResult(True, "review_iterations_complete")
        return None


class MockInterviewStrategy(RoundRobinStrategy):
    """模拟面试模式。"""

    def build_system_prompt(self, participant: ModelParticipant, session: Session) -> str:
        role = "Interviewer" if participant.sequence_order == 1 else "Candidate"
        return (
            f"你是模拟面试中的 {role} {participant.custom_id}。"
            "Interviewer 提问追问，Candidate 直接作答。"
        )

    def check_termination(self, session: Session, messages: List[CollaborationMessage]) -> Optional[TerminationResult]:
        if session.current_round >= 3 and session.next_speaker_index == 0:
            return TerminationResult(True, "interview_complete")
        return None


class StoryChainStrategy(RoundRobinStrategy):
    """故事接龙模式。"""

    def build_system_prompt(self, participant: ModelParticipant, session: Session) -> str:
        return (
            f"你是故事接龙作者 {participant.custom_id}。"
            "请延续故事，不要与已有情节矛盾，每次推进一段清晰情节。"
        )


class NegotiationStrategy(GameStrategyBase):
    """模拟谈判模式。"""

    def check_termination(self, session: Session, messages: List[CollaborationMessage]) -> Optional[TerminationResult]:
        accepted = [
            summary for summary in session.snapshot.participant_summaries.values()
            if any(token in summary for token in ("接受", "同意", "accept", "agree"))
        ]
        if accepted and len(accepted) == len(session.participants):
            return TerminationResult(True, "agreement_reached")
        if session.current_round >= session.config.max_rounds and session.next_speaker_index == 0:
            return TerminationResult(True, "deadlock")
        return None


class StrategyRegistry:
    """策略注册表。"""

    def __init__(self) -> None:
        consensus_detector = ConsensusDetector()
        game_master = GameMaster()
        self._strategies: Dict[CollaborationMode, CollaborationModeStrategy] = {
            CollaborationMode.CHAT: ChatStrategy(),
            CollaborationMode.BRAINSTORM: BrainstormStrategy(),
            CollaborationMode.CODE_COLLABORATION: CodeCollabStrategy(),
            CollaborationMode.CODE_WORKSPACE: WorkspaceStrategy(),
            CollaborationMode.DATA_ANALYSIS: DataAnalysisStrategy(),
            CollaborationMode.DEBATE: DebateStrategy(consensus_detector=consensus_detector),
            CollaborationMode.WEREWOLF: WerewolfStrategy(game_master=game_master),
            CollaborationMode.MURDER_MYSTERY: MurderMysteryStrategy(game_master=game_master),
            CollaborationMode.UNDERCOVER: UndercoverStrategy(game_master=game_master),
            CollaborationMode.MOCK_TRIAL: MockTrialStrategy(),
            CollaborationMode.ROLE_PLAY: RolePlayStrategy(),
            CollaborationMode.SOCRATIC_DIALOGUE: SocraticStrategy(),
            CollaborationMode.PEER_REVIEW: PeerReviewStrategy(),
            CollaborationMode.MOCK_INTERVIEW: MockInterviewStrategy(),
            CollaborationMode.STORY_CHAIN: StoryChainStrategy(),
            CollaborationMode.NEGOTIATION: NegotiationStrategy(game_master=game_master),
        }

    def get(self, mode: CollaborationMode) -> CollaborationModeStrategy:
        return self._strategies[mode]
