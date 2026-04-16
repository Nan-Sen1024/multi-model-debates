"""
SQLite 数据库初始化：创建所有表及 FTS5 全文索引
"""
import aiosqlite

DB_PATH = "multi_model_debate.db"

CREATE_SESSIONS_TABLE = """
CREATE TABLE IF NOT EXISTS collaboration_sessions (
    id          TEXT PRIMARY KEY,
    topic       TEXT NOT NULL,
    mode        TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'active',
    config      TEXT NOT NULL,
    created_at  INTEGER NOT NULL,
    updated_at  INTEGER NOT NULL
);
"""

CREATE_PARTICIPANTS_TABLE = """
CREATE TABLE IF NOT EXISTS model_participants (
    id              TEXT PRIMARY KEY,
    session_id      TEXT NOT NULL REFERENCES collaboration_sessions(id),
    custom_id       TEXT NOT NULL,
    display_name    TEXT,
    model_ref       TEXT NOT NULL,
    provider_id     TEXT,
    role_desc       TEXT,
    private_info    TEXT,
    sequence_order  INTEGER NOT NULL,
    is_active       BOOLEAN DEFAULT TRUE,
    UNIQUE(session_id, custom_id)
);
"""

CREATE_MESSAGES_TABLE = """
CREATE TABLE IF NOT EXISTS collaboration_messages (
    id              TEXT PRIMARY KEY,
    session_id      TEXT NOT NULL REFERENCES collaboration_sessions(id),
    sender_id       TEXT NOT NULL,
    message_type    TEXT NOT NULL,
    content         TEXT NOT NULL,
    is_masked       BOOLEAN DEFAULT FALSE,
    is_compressed   BOOLEAN DEFAULT FALSE,
    drift_score     REAL,
    round_number    INTEGER,
    created_at      INTEGER NOT NULL
);
"""

CREATE_MESSAGES_FTS = """
CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(
    content, sender_id, session_id,
    content='collaboration_messages',
    content_rowid='rowid'
);
"""

CREATE_COMPRESSED_SUMMARIES_TABLE = """
CREATE TABLE IF NOT EXISTS compressed_summaries (
    id              TEXT PRIMARY KEY,
    session_id      TEXT NOT NULL,
    parent_id       TEXT,
    covers_from     INTEGER NOT NULL,
    covers_to       INTEGER NOT NULL,
    summary_text    TEXT NOT NULL,
    created_at      INTEGER NOT NULL
);
"""

CREATE_CHECKPOINTS_TABLE = """
CREATE TABLE IF NOT EXISTS checkpoints (
    id              TEXT PRIMARY KEY,
    session_id      TEXT NOT NULL,
    topic           TEXT NOT NULL,
    mode            TEXT NOT NULL,
    snapshot_json   TEXT NOT NULL,
    next_step       TEXT,
    created_at      INTEGER NOT NULL
);
"""

CREATE_PROVIDER_CONFIGS_TABLE = """
CREATE TABLE IF NOT EXISTS provider_configs (
    id              TEXT PRIMARY KEY,
    name            TEXT NOT NULL UNIQUE,
    provider_type   TEXT NOT NULL,
    base_url        TEXT,
    api_format      TEXT NOT NULL,
    auth_type       TEXT NOT NULL,
    auth_config     TEXT,
    fallback_ids    TEXT,
    is_active       BOOLEAN DEFAULT TRUE
);
"""

# Device_Code_Flow 认证会话表
# status: pending | completed | failed | expired
CREATE_AUTH_SESSIONS_TABLE = """
CREATE TABLE IF NOT EXISTS auth_sessions (
    id              TEXT PRIMARY KEY,       -- UUID
    provider_id     TEXT NOT NULL,          -- 关联的 provider_configs.id
    flow_type       TEXT NOT NULL,          -- aws_iam | openai_codex | generic_oauth
    status          TEXT NOT NULL DEFAULT 'pending',
    verification_uri TEXT,                  -- 展示给用户的授权 URL
    user_code       TEXT,                   -- 展示给用户的设备码
    device_code     TEXT,                   -- 内部轮询用的 device_code（不暴露给前端）
    client_id       TEXT,
    client_secret   TEXT,
    interval        INTEGER DEFAULT 5,      -- 轮询间隔（秒）
    expires_at      INTEGER,                -- Unix timestamp
    access_token    TEXT,                   -- 完成后存储（加密）
    refresh_token   TEXT,                   -- 完成后存储（加密）
    token_expires_at INTEGER,
    sigv4_json      TEXT,                   -- AWS: Sigv4_Credentials JSON（加密）
    accounts_json   TEXT,                   -- AWS: 可用账号列表 JSON
    selected_account_id   TEXT,
    selected_role_name    TEXT,
    error_message   TEXT,
    created_at      INTEGER NOT NULL,
    updated_at      INTEGER NOT NULL
);
"""

# FTS5 triggers to keep the index in sync with the messages table
CREATE_FTS_INSERT_TRIGGER = """
CREATE TRIGGER IF NOT EXISTS messages_fts_insert
AFTER INSERT ON collaboration_messages BEGIN
    INSERT INTO messages_fts(rowid, content, sender_id, session_id)
    VALUES (new.rowid, new.content, new.sender_id, new.session_id);
END;
"""

CREATE_FTS_DELETE_TRIGGER = """
CREATE TRIGGER IF NOT EXISTS messages_fts_delete
AFTER DELETE ON collaboration_messages BEGIN
    INSERT INTO messages_fts(messages_fts, rowid, content, sender_id, session_id)
    VALUES ('delete', old.rowid, old.content, old.sender_id, old.session_id);
END;
"""

CREATE_FTS_UPDATE_TRIGGER = """
CREATE TRIGGER IF NOT EXISTS messages_fts_update
AFTER UPDATE ON collaboration_messages BEGIN
    INSERT INTO messages_fts(messages_fts, rowid, content, sender_id, session_id)
    VALUES ('delete', old.rowid, old.content, old.sender_id, old.session_id);
    INSERT INTO messages_fts(rowid, content, sender_id, session_id)
    VALUES (new.rowid, new.content, new.sender_id, new.session_id);
END;
"""

ALL_DDL = [
    CREATE_SESSIONS_TABLE,
    CREATE_PARTICIPANTS_TABLE,
    CREATE_MESSAGES_TABLE,
    CREATE_MESSAGES_FTS,
    CREATE_COMPRESSED_SUMMARIES_TABLE,
    CREATE_CHECKPOINTS_TABLE,
    CREATE_PROVIDER_CONFIGS_TABLE,
    CREATE_AUTH_SESSIONS_TABLE,
    CREATE_FTS_INSERT_TRIGGER,
    CREATE_FTS_DELETE_TRIGGER,
    CREATE_FTS_UPDATE_TRIGGER,
]


async def init_db(db_path: str = DB_PATH) -> None:
    """初始化数据库，创建所有表和索引"""
    async with aiosqlite.connect(db_path) as db:
        await db.execute("PRAGMA journal_mode=WAL;")
        await db.execute("PRAGMA foreign_keys=ON;")
        for ddl in ALL_DDL:
            await db.execute(ddl)
        await _ensure_compatible_schema(db)
        await db.commit()


async def get_db(db_path: str = DB_PATH) -> aiosqlite.Connection:
    """获取数据库连接（调用方负责关闭）"""
    conn = await aiosqlite.connect(db_path)
    await conn.execute("PRAGMA foreign_keys=ON;")
    conn.row_factory = aiosqlite.Row
    return conn


async def _ensure_compatible_schema(db: aiosqlite.Connection) -> None:
    await _ensure_column(
        db,
        table_name="model_participants",
        column_name="provider_id",
        ddl="ALTER TABLE model_participants ADD COLUMN provider_id TEXT",
    )


async def _ensure_column(
    db: aiosqlite.Connection,
    table_name: str,
    column_name: str,
    ddl: str,
) -> None:
    async with db.execute(f"PRAGMA table_info({table_name})") as cursor:
        rows = await cursor.fetchall()
    existing = {row[1] for row in rows}
    if column_name not in existing:
        await db.execute(ddl)
