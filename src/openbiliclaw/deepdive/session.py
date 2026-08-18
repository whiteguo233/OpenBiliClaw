"""深潜研究 · 会话管理器（SQLite 持久化）"""

from __future__ import annotations

import json
import threading
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class SearchPlan:
    """搜索计划：LLM 生成的结构化搜索方案"""
    topic: str
    keywords: list[dict[str, str]]  # [{platform, query, type}, ...]
    clarifying_questions: list[str]  # 需要用户确认的问题
    raw_prompt: str = ""

    def to_dict(self) -> dict:
        return {
            "topic": self.topic,
            "keywords": self.keywords,
            "clarifying_questions": self.clarifying_questions,
            "raw_prompt": self.raw_prompt,
        }

    @classmethod
    def from_dict(cls, d: dict | None) -> "SearchPlan | None":
        if not d:
            return None
        return cls(
            topic=d.get("topic", ""),
            keywords=d.get("keywords", []) or [],
            clarifying_questions=d.get("clarifying_questions", []) or [],
            raw_prompt=d.get("raw_prompt", ""),
        )


@dataclass
class DeepDiveMessage:
    """会话中的一条消息"""
    role: str  # "user" | "assistant" | "system"
    content: str
    timestamp: str = field(default_factory=_now)

    def to_dict(self) -> dict:
        return {"role": self.role, "content": self.content, "timestamp": self.timestamp}

    @classmethod
    def from_dict(cls, d: dict) -> "DeepDiveMessage":
        return cls(role=d.get("role", ""), content=d.get("content", ""), timestamp=d.get("timestamp", _now()))


@dataclass
class DeepDiveCard:
    """搜索结果卡片"""
    platform: str
    title: str
    url: str
    description: str = ""
    author: str | None = None
    cover_url: str | None = None
    published_at: str | None = None
    score: float = 0.0
    bvid: str = ""  # 内容 ID（反馈用）

    def to_dict(self) -> dict:
        return {
            "platform": self.platform,
            "title": self.title,
            "url": self.url,
            "description": self.description,
            "author": self.author,
            "cover_url": self.cover_url,
            "published_at": self.published_at,
            "score": self.score,
            "bvid": self.bvid,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "DeepDiveCard":
        return cls(
            platform=d.get("platform", ""),
            title=d.get("title", ""),
            url=d.get("url", ""),
            description=d.get("description", ""),
            author=d.get("author"),
            cover_url=d.get("cover_url"),
            published_at=d.get("published_at"),
            score=float(d.get("score", 0) or 0),
            bvid=d.get("bvid", ""),
        )


@dataclass
class DeepDiveSession:
    """一个深潜研究会话"""
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    topic: str = ""
    status: str = "initial"  # initial | clarifying | planning | searching | displaying | completed
    messages: list[DeepDiveMessage] = field(default_factory=list)
    search_plan: SearchPlan | None = None
    results: list[DeepDiveCard] = field(default_factory=list)
    folder: str = ""  # 所属文件夹名（空 = 根目录）
    created_at: str = field(default_factory=_now)
    updated_at: str = field(default_factory=_now)


class SessionManager:
    """SQLite 持久化会话管理器

    会话数据永久保存到本地 SQLite 文件（默认 data/deepdive_sessions.db），
    重启服务后会话不丢失。支持创建、读取、删除、重命名、列出。
    """

    def __init__(self, db_path: str | Path | None = None):
        self._db_path = Path(db_path) if db_path else Path("data/deepdive_sessions.db")
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._init_db()

    # ── 数据库 ──────────────────────────────────────────────
    def _connect(self):
        import sqlite3
        con = sqlite3.connect(str(self._db_path))
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA journal_mode=WAL")
        return con

    def _init_db(self) -> None:
        with self._lock, self._connect() as con:
            con.execute("""
                CREATE TABLE IF NOT EXISTS deepdive_sessions (
                    id          TEXT PRIMARY KEY,
                    topic       TEXT NOT NULL DEFAULT '',
                    status      TEXT NOT NULL DEFAULT 'initial',
                    messages    TEXT NOT NULL DEFAULT '[]',
                    search_plan TEXT,
                    results     TEXT NOT NULL DEFAULT '[]',
                    folder      TEXT NOT NULL DEFAULT '',
                    created_at  TEXT NOT NULL,
                    updated_at  TEXT NOT NULL
                )
            """)
            con.execute("""
                CREATE TABLE IF NOT EXISTS deepdive_folders (
                    name       TEXT PRIMARY KEY,
                    created_at TEXT NOT NULL
                )
            """)
            # 兼容旧库：给已存在的表补 folder 列（若缺）
            cols = [r[1] for r in con.execute("PRAGMA table_info(deepdive_sessions)").fetchall()]
            if "folder" not in cols:
                con.execute("ALTER TABLE deepdive_sessions ADD COLUMN folder TEXT NOT NULL DEFAULT ''")

    def _serialize(self, session: DeepDiveSession) -> tuple:
        return (
            session.id,
            session.topic,
            session.status,
            json.dumps([m.to_dict() for m in session.messages], ensure_ascii=False),
            json.dumps(session.search_plan.to_dict(), ensure_ascii=False) if session.search_plan else None,
            json.dumps([c.to_dict() for c in session.results], ensure_ascii=False),
            session.folder,
            session.created_at,
            session.updated_at,
        )

    def _deserialize(self, row) -> DeepDiveSession:
        msgs = json.loads(row["messages"] or "[]")
        results = json.loads(row["results"] or "[]")
        plan = row["search_plan"]
        return DeepDiveSession(
            id=row["id"],
            topic=row["topic"],
            status=row["status"],
            messages=[DeepDiveMessage.from_dict(m) for m in msgs],
            search_plan=SearchPlan.from_dict(json.loads(plan)) if plan else None,
            results=[DeepDiveCard.from_dict(c) for c in results],
            folder=row["folder"] or "",
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def _write(self, session: DeepDiveSession) -> None:
        with self._lock, self._connect() as con:
            con.execute(
                """INSERT INTO deepdive_sessions
                   (id, topic, status, messages, search_plan, results, folder, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(id) DO UPDATE SET
                    topic=excluded.topic, status=excluded.status, messages=excluded.messages,
                    search_plan=excluded.search_plan, results=excluded.results, folder=excluded.folder,
                    updated_at=excluded.updated_at""",
                self._serialize(session),
            )

    # ── CRUD ────────────────────────────────────────────────
    def create_session(self, topic: str = "") -> DeepDiveSession:
        """创建新会话（写入磁盘）"""
        session = DeepDiveSession(topic=topic)
        if topic:
            session.messages.append(DeepDiveMessage(role="user", content=topic))
        self._write(session)
        return session

    def get_session(self, session_id: str) -> DeepDiveSession | None:
        """获取会话"""
        with self._lock, self._connect() as con:
            row = con.execute(
                "SELECT * FROM deepdive_sessions WHERE id = ?", (session_id,)
            ).fetchone()
        return self._deserialize(row) if row else None

    def delete_session(self, session_id: str) -> bool:
        """删除会话（从磁盘删除）"""
        with self._lock, self._connect() as con:
            cur = con.execute("DELETE FROM deepdive_sessions WHERE id = ?", (session_id,))
        return cur.rowcount > 0

    def rename_session(self, session_id: str, new_name: str) -> DeepDiveSession | None:
        """重命名会话 topic"""
        session = self.get_session(session_id)
        if not session:
            return None
        session.topic = (new_name or "").strip()
        session.updated_at = _now()
        self._write(session)
        return session

    def list_sessions(self) -> list[DeepDiveSession]:
        """列出所有会话（最新在前）"""
        with self._lock, self._connect() as con:
            rows = con.execute(
                "SELECT * FROM deepdive_sessions ORDER BY updated_at DESC"
            ).fetchall()
        return [self._deserialize(r) for r in rows]

    def add_message(self, session_id: str, role: str, content: str) -> DeepDiveMessage | None:
        """添加消息（写盘）"""
        session = self.get_session(session_id)
        if not session:
            return None
        msg = DeepDiveMessage(role=role, content=content)
        session.messages.append(msg)
        session.updated_at = _now()
        self._write(session)
        return msg

    def set_plan(self, session_id: str, plan: SearchPlan) -> bool:
        """设置搜索计划"""
        session = self.get_session(session_id)
        if not session:
            return False
        session.search_plan = plan
        session.status = "planning"
        session.updated_at = _now()
        self._write(session)
        return True

    def set_results(self, session_id: str, results: list[DeepDiveCard]) -> bool:
        """设置搜索结果"""
        session = self.get_session(session_id)
        if not session:
            return False
        session.results = results
        session.status = "displaying"
        session.updated_at = _now()
        self._write(session)
        return True

    def set_status(self, session_id: str, status: str) -> bool:
        """更新会话状态"""
        session = self.get_session(session_id)
        if not session:
            return False
        session.status = status
        session.updated_at = _now()
        self._write(session)
        return True

    # ── 文件夹（树形收纳） ────────────────────────────────────
    def create_folder(self, name: str) -> bool:
        """创建文件夹（已存在返回 False）"""
        name = (name or "").strip()
        if not name:
            return False
        with self._lock, self._connect() as con:
            try:
                con.execute(
                    "INSERT INTO deepdive_folders (name, created_at) VALUES (?, ?)",
                    (name, _now()),
                )
                return True
            except Exception:
                return False

    def list_folders(self) -> list[str]:
        """列出所有文件夹名"""
        with self._lock, self._connect() as con:
            rows = con.execute("SELECT name FROM deepdive_folders ORDER BY created_at").fetchall()
        return [r["name"] for r in rows]

    def rename_folder(self, old_name: str, new_name: str) -> bool:
        """重命名文件夹：更新 folders 表 + 更新归属会话的 folder 字段"""
        old_name = (old_name or "").strip()
        new_name = (new_name or "").strip()
        if not old_name or not new_name or old_name == new_name:
            return False
        with self._lock, self._connect() as con:
            cur = con.execute(
                "UPDATE deepdive_folders SET name = ? WHERE name = ?", (new_name, old_name)
            )
            if cur.rowcount == 0:
                return False
            con.execute(
                "UPDATE deepdive_sessions SET folder = ? WHERE folder = ?", (new_name, old_name)
            )
        return True

    def delete_folder(self, name: str) -> bool:
        """删除文件夹：移除 folders 记录 + 把归属会话移回根目录"""
        name = (name or "").strip()
        if not name:
            return False
        with self._lock, self._connect() as con:
            cur = con.execute("DELETE FROM deepdive_folders WHERE name = ?", (name,))
            if cur.rowcount == 0:
                return False
            con.execute("UPDATE deepdive_sessions SET folder = '' WHERE folder = ?", (name,))
        return True

    def move_session(self, session_id: str, folder: str) -> bool:
        """把会话移动到指定文件夹（folder='' 移回根目录）"""
        session = self.get_session(session_id)
        if not session:
            return False
        session.folder = (folder or "").strip()
        session.updated_at = _now()
        self._write(session)
        return True
        return True