"""
Context_Compressor：上下文分级压缩与 Checkpoint 管理
需求：23.x、25.5、25.6、25.7
"""
from __future__ import annotations

import json
import logging
import math
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable, List, Optional, Tuple

import aiosqlite

from .database import init_db
from .enums import CollaborationMode, MessageType
from .models import Checkpoint, CollaborationMessage, Session, SessionSnapshot

logger = logging.getLogger(__name__)

DEFAULT_MAX_TOKENS = 100_000


@dataclass
class CompressionResult:
    """check_and_compress 的返回结果"""
    action: str                          # "none" | "delegate_tools" | "mask_tools" | "summarize"
    masked_count: int = 0
    checkpoint_id: Optional[str] = None


@dataclass
class DAGNode:
    """DAG 层级摘要节点"""
    id: str
    summary_text: str
    covers_from: int                     # 覆盖的消息 round 范围起始
    covers_to: int                       # 覆盖的消息 round 范围结束
    children: List[str] = field(default_factory=list)   # 子节点 id 列表
    is_leaf: bool = True


def _snapshot_to_dict(snapshot: SessionSnapshot) -> dict:
    """将 SessionSnapshot 序列化为可 JSON 化的字典，mode 用 .value 取枚举值。"""
    return {
        "topic": snapshot.topic,
        "mode": snapshot.mode.value if hasattr(snapshot.mode, "value") else str(snapshot.mode),
        "participant_summaries": snapshot.participant_summaries,
        "consensus_list": snapshot.consensus_list,
        "key_events": snapshot.key_events,
    }


def _dict_to_snapshot(data: dict) -> SessionSnapshot:
    """从字典重建 SessionSnapshot，mode 字段转回枚举。"""
    mode_val = data["mode"]
    try:
        mode = CollaborationMode(mode_val)
    except ValueError:
        mode = mode_val  # 保持字符串（容错）
    return SessionSnapshot(
        topic=data["topic"],
        mode=mode,
        participant_summaries=data.get("participant_summaries", {}),
        consensus_list=data.get("consensus_list", []),
        key_events=data.get("key_events", []),
    )


class ContextCompressor:
    """
    上下文分级压缩器。
    实现四级压缩策略、工具输出遮蔽、Checkpoint 写入与恢复。
    """

    def __init__(self, db_path: Optional[str] = None, max_tokens: int = DEFAULT_MAX_TOKENS) -> None:
        from .database import DB_PATH
        self.db_path = db_path or DB_PATH
        self.max_tokens = max_tokens

    # ------------------------------------------------------------------
    # 上下文使用率估算（需求 23.x）
    # ------------------------------------------------------------------

    def compute_context_usage_ratio(
        self,
        session: Session,
        messages: List[CollaborationMessage],
    ) -> float:
        """
        简单估算上下文使用率：所有消息内容字符数之和 / max_tokens。
        返回值范围 [0, 1]（超出时截断为 1.0）。
        需求：23.1
        """
        total_chars = sum(len(m.content) for m in messages)
        ratio = total_chars / max(self.max_tokens, 1)
        return min(ratio, 1.0)

    # ------------------------------------------------------------------
    # 工具输出遮蔽（需求 23.3）
    # ------------------------------------------------------------------

    async def mask_tool_outputs(
        self,
        messages: List[CollaborationMessage],
    ) -> int:
        """
        将 message_type == TOOL_OUTPUT 且 is_masked=False 的消息替换为占位符，
        更新 is_masked=True，持久化到数据库，返回遮蔽数量。
        需求：23.3
        """
        await init_db(self.db_path)
        masked_count = 0

        async with aiosqlite.connect(self.db_path) as db:
            for msg in messages:
                if msg.message_type == MessageType.TOOL_OUTPUT and not msg.is_masked:
                    msg.content = "[工具输出已遮蔽]"
                    msg.is_masked = True
                    await db.execute(
                        "UPDATE collaboration_messages SET content = ?, is_masked = 1 WHERE id = ?",
                        (msg.content, msg.id),
                    )
                    masked_count += 1
            await db.commit()

        return masked_count

    # ------------------------------------------------------------------
    # 四级压缩策略（需求 23.1-23.4）
    # ------------------------------------------------------------------

    async def check_and_compress(
        self,
        session: Session,
        messages: Optional[List[CollaborationMessage]] = None,
        summary_model_fn: Optional[Callable[[str], str]] = None,
    ) -> CompressionResult:
        """
        四级压缩策略：
          < 0.5  → 不压缩，返回 CompressionResult(action="none")
          0.5-0.7 → 派生工具子代理（设置 delegate_all_tools=True），返回 action="delegate_tools"
          0.7-0.85 → 遮蔽工具输出，返回 action="mask_tools"
          ≥ 0.85  → 写 Checkpoint + 摘要，返回 action="summarize"
        需求：23.1、23.2、23.3、23.4
        """
        msgs = messages if messages is not None else []
        ratio = self.compute_context_usage_ratio(session, msgs)

        if ratio < 0.5:
            return CompressionResult(action="none")

        elif ratio < 0.7:
            # 派生工具密集型子任务给独立子代理
            session.config.delegate_all_tools = True
            return CompressionResult(action="delegate_tools")

        elif ratio < 0.85:
            # 遮蔽工具输出，后续工具调用派生给子代理
            masked_count = await self.mask_tool_outputs(msgs)
            session.config.delegate_all_tools = True
            return CompressionResult(action="mask_tools", masked_count=masked_count)

        else:
            # 写 Checkpoint，然后对早期消息批次生成摘要
            checkpoint = await self.write_checkpoint(session)

            retention = session.config.retention_window
            # 保留最近 retention_window 条消息，对其余可压缩消息摘要
            if retention > 0 and len(msgs) > retention:
                compressible = msgs[:-retention]
            else:
                compressible = []

            # 过滤掉已压缩的消息（禁止递归摘要）
            compressible = [m for m in compressible if not m.is_compressed]

            if compressible and summary_model_fn is not None:
                dag_builder = DAGSummaryBuilder(
                    retention_window=retention,
                    db_path=self.db_path,
                )
                await dag_builder.build_dag_summary(
                    message_batches=_chunk(compressible, 20),
                    summary_model_fn=summary_model_fn,
                )

            return CompressionResult(action="summarize", checkpoint_id=checkpoint.id)

    # ------------------------------------------------------------------
    # Checkpoint 写入（需求 25.5、25.6）
    # ------------------------------------------------------------------

    async def write_checkpoint(self, session: Session) -> Checkpoint:
        """
        将当前会话状态写入 SQLite checkpoints 表，返回 Checkpoint 对象。

        Checkpoint 内容：
          - session_id：会话 ID
          - topic：话题
          - mode：协作模式（枚举 .value 字符串）
          - snapshot_json：SessionSnapshot 序列化为 JSON
          - next_step：默认空字符串
        需求：25.5、25.6
        """
        checkpoint_id = str(uuid.uuid4())
        created_at_ts = int(time.time())
        snapshot_dict = _snapshot_to_dict(session.snapshot)
        snapshot_json = json.dumps(snapshot_dict, ensure_ascii=False)
        mode_str = session.mode.value if hasattr(session.mode, "value") else str(session.mode)
        next_step = ""

        await init_db(self.db_path)

        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                INSERT INTO checkpoints
                    (id, session_id, topic, mode, snapshot_json, next_step, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (checkpoint_id, session.id, session.topic, mode_str,
                 snapshot_json, next_step, created_at_ts),
            )
            await db.commit()

        checkpoint = Checkpoint(
            id=checkpoint_id,
            session_id=session.id,
            topic=session.topic,
            mode=mode_str,
            snapshot=session.snapshot,
            next_step=next_step,
            created_at=datetime.utcfromtimestamp(created_at_ts),
        )
        logger.info("Checkpoint %s written for session %s", checkpoint_id, session.id)
        return checkpoint

    # ------------------------------------------------------------------
    # Checkpoint 恢复（需求 25.7）
    # ------------------------------------------------------------------

    async def restore_from_checkpoint(
        self, checkpoint_id: str, db_path: Optional[str] = None
    ) -> Tuple[Checkpoint, SessionSnapshot]:
        """
        从 SQLite 加载 Checkpoint，重建 SessionSnapshot 和基本 Session 信息。
        返回 (Checkpoint, SessionSnapshot) 元组。
        需求：25.7
        """
        path = db_path or self.db_path

        async with aiosqlite.connect(path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM checkpoints WHERE id = ?", (checkpoint_id,)
            ) as cursor:
                row = await cursor.fetchone()

        if row is None:
            raise ValueError(f"Checkpoint not found: {checkpoint_id}")

        snapshot_data = json.loads(row["snapshot_json"])
        snapshot = _dict_to_snapshot(snapshot_data)

        checkpoint = Checkpoint(
            id=row["id"],
            session_id=row["session_id"],
            topic=row["topic"],
            mode=row["mode"],
            snapshot=snapshot,
            next_step=row["next_step"] or "",
            created_at=datetime.utcfromtimestamp(row["created_at"]),
        )
        return checkpoint, snapshot

# ------------------------------------------------------------------
# 辅助函数
# ------------------------------------------------------------------

def _chunk(lst: list, size: int) -> List[list]:
    """将列表按 size 分块，返回子列表列表。"""
    return [lst[i:i + size] for i in range(0, len(lst), size)]


# ------------------------------------------------------------------
# DAG 层级摘要构建器（需求 23.5、23.6、23.7、23.8）
# ------------------------------------------------------------------

class DAGSummaryBuilder:
    """
    以 DAG 结构组织多层级摘要。
    - 叶节点：每批消息生成摘要
    - 上层节点：合并相邻叶节点摘要（每层合并 4 个）
    - 禁止对已有 is_compressed=True 的消息再次摘要
    - 保留最近 retention_window 条消息完整原文
    需求：23.5、23.6、23.7、23.8
    """

    MERGE_FACTOR = 4  # 每层合并相邻节点数

    def __init__(
        self,
        retention_window: int = 10,
        db_path: Optional[str] = None,
    ) -> None:
        from .database import DB_PATH
        self.retention_window = retention_window
        self.db_path = db_path or DB_PATH

    async def summarize_batch(
        self,
        messages: List[CollaborationMessage],
        summary_model_fn: Callable[[str], str],
    ) -> str:
        """
        对一批消息生成摘要文本。
        - 跳过已有 is_compressed=True 的消息（幂等性）
        - summary_model_fn: (text: str) -> str
        需求：23.5、23.7
        """
        # 只对未压缩的消息摘要
        active = [m for m in messages if not m.is_compressed]
        if not active:
            return ""

        combined = "\n".join(
            f"[{m.sender_id}|{m.message_type.value if hasattr(m.message_type, 'value') else m.message_type}]: {m.content}"
            for m in active
        )
        return summary_model_fn(combined)

    async def build_dag_summary(
        self,
        message_batches: List[List[CollaborationMessage]],
        summary_model_fn: Callable[[str], str],
    ) -> Optional[DAGNode]:
        """
        构建 DAG 层级摘要：
          1. 叶节点：每批消息生成摘要
          2. 上层节点：合并相邻叶节点摘要（每层合并 MERGE_FACTOR 个）
        返回根节点（或 None 若无可压缩内容）。
        需求：23.5、23.6、23.7
        """
        if not message_batches:
            return None

        await init_db(self.db_path)

        # 第一层：叶节点
        leaf_nodes: List[DAGNode] = []
        for batch in message_batches:
            # 过滤已压缩消息
            active = [m for m in batch if not m.is_compressed]
            if not active:
                continue

            summary_text = await self.summarize_batch(active, summary_model_fn)
            if not summary_text:
                continue

            covers_from = active[0].round_number
            covers_to = active[-1].round_number

            node = DAGNode(
                id=str(uuid.uuid4()),
                summary_text=summary_text,
                covers_from=covers_from,
                covers_to=covers_to,
                children=[],
                is_leaf=True,
            )
            leaf_nodes.append(node)

            # 将批次消息标记为已压缩并持久化
            await self._mark_compressed(active, summary_text, covers_from, covers_to, session_id=active[0].session_id)

        if not leaf_nodes:
            return None

        # 逐层向上合并，直到只剩一个根节点或节点数 <= MERGE_FACTOR
        current_level = leaf_nodes
        while len(current_level) > self.MERGE_FACTOR:
            next_level: List[DAGNode] = []
            for group in _chunk(current_level, self.MERGE_FACTOR):
                combined_text = "\n".join(n.summary_text for n in group)
                parent_text = summary_model_fn(combined_text)
                parent = DAGNode(
                    id=str(uuid.uuid4()),
                    summary_text=parent_text,
                    covers_from=group[0].covers_from,
                    covers_to=group[-1].covers_to,
                    children=[n.id for n in group],
                    is_leaf=False,
                )
                next_level.append(parent)
            current_level = next_level

        if len(current_level) == 1:
            return current_level[0]

        # 多个顶层节点时创建一个虚拟根节点
        combined_text = "\n".join(n.summary_text for n in current_level)
        root_text = summary_model_fn(combined_text)
        root = DAGNode(
            id=str(uuid.uuid4()),
            summary_text=root_text,
            covers_from=current_level[0].covers_from,
            covers_to=current_level[-1].covers_to,
            children=[n.id for n in current_level],
            is_leaf=False,
        )
        return root

    async def _mark_compressed(
        self,
        messages: List[CollaborationMessage],
        summary_text: str,
        covers_from: int,
        covers_to: int,
        session_id: str,
    ) -> None:
        """将消息标记为已压缩，并将摘要写入 compressed_summaries 表。"""
        summary_id = str(uuid.uuid4())
        created_at_ts = int(time.time())

        async with aiosqlite.connect(self.db_path) as db:
            # 写入摘要记录
            await db.execute(
                """
                INSERT INTO compressed_summaries
                    (id, session_id, parent_id, covers_from, covers_to, summary_text, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (summary_id, session_id, None, covers_from, covers_to, summary_text, created_at_ts),
            )
            # 标记原始消息为已压缩
            for msg in messages:
                msg.is_compressed = True
                await db.execute(
                    "UPDATE collaboration_messages SET is_compressed = 1 WHERE id = ?",
                    (msg.id,),
                )
            await db.commit()
