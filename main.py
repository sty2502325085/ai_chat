import base64
import hashlib
import hmac
import json
import os
import secrets
import sqlite3
import time
from pathlib import Path
from typing import Any, Literal

import httpx
import psycopg
from psycopg.rows import dict_row
from dotenv import load_dotenv
from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field


load_dotenv()

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
DB_PATH = BASE_DIR / "chat.db"
DATABASE_URL = os.getenv("DATABASE_URL", "").strip()
USE_POSTGRES = bool(DATABASE_URL)

DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")
DEEPSEEK_MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-chat")
DEEPSEEK_API_URL = "https://api.deepseek.com/chat/completions"
SECRET_KEY = os.getenv("SECRET_KEY", "dev-secret-change-me")
TOKEN_EXPIRE_SECONDS = 60 * 60 * 24 * 7
MAX_CONTEXT_MESSAGES = 10
DAILY_MESSAGE_LIMIT = int(os.getenv("DAILY_MESSAGE_LIMIT", "50"))
UNLIMITED_USERS = {"甘水清"}
ADMIN_USERS = {name.strip() for name in os.getenv("ADMIN_USERS", "sty2502325085,admin").split(",") if name.strip()}

app = FastAPI(title="AI Chat Assistant")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


class UserCreate(BaseModel):
    username: str = Field(min_length=3, max_length=30, pattern=r"^[a-zA-Z0-9_\u4e00-\u9fa5]+$")
    password: str = Field(min_length=6, max_length=128)


class UserLogin(BaseModel):
    username: str = Field(min_length=1, max_length=30)
    password: str = Field(min_length=1, max_length=128)


class UserPublic(BaseModel):
    id: int
    username: str
    is_admin: bool = False
    is_disabled: bool = False


class UsageStatus(BaseModel):
    date: str
    used: int
    limit: int | None
    remaining: int | None
    unlimited: bool


class AuthResponse(BaseModel):
    token: str
    user: UserPublic
    usage: UsageStatus


class MessageResponse(BaseModel):
    message: str


class ChatMessage(BaseModel):
    id: int
    role: Literal["user", "assistant"]
    content: str
    created_at: int


class ChatSession(BaseModel):
    id: int
    title: str
    created_at: int
    updated_at: int
    messages: list[ChatMessage]


class ChatRequest(BaseModel):
    content: str = Field(min_length=1, max_length=8000)
    session_id: int | None = None


class SessionActionRequest(BaseModel):
    session_id: int


class SessionUpdate(BaseModel):
    title: str = Field(min_length=1, max_length=60)


class ChatResponse(BaseModel):
    reply: str
    session: ChatSession


class AdminUserRow(BaseModel):
    id: int
    username: str
    created_at: int
    session_count: int
    message_count: int
    today_used: int
    daily_limit: int | None
    remaining: int | None
    unlimited: bool
    is_admin: bool
    is_disabled: bool
    limit_override: int | None


class AdminStats(BaseModel):
    user_count: int
    session_count: int
    message_count: int
    today_used: int
    daily_limit: int
    users: list[AdminUserRow]


class AdminUserUpdate(BaseModel):
    is_disabled: bool | None = None
    daily_limit_override: int | None = Field(default=None, ge=0, le=10000)


class AdminUserDetail(BaseModel):
    user: AdminUserRow
    sessions: list[ChatSession]


def normalize_database_url(url: str) -> str:
    if url.startswith("postgres://"):
        return "postgresql://" + url.removeprefix("postgres://")
    return url


class DatabaseConnection:
    def __init__(self) -> None:
        self.conn: Any | None = None

    def __enter__(self) -> "DatabaseConnection":
        if USE_POSTGRES:
            self.conn = psycopg.connect(normalize_database_url(DATABASE_URL), row_factory=dict_row)
        else:
            self.conn = sqlite3.connect(DB_PATH)
            self.conn.row_factory = sqlite3.Row
            self.conn.execute("PRAGMA foreign_keys = ON")
        return self

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        if self.conn is None:
            return
        try:
            if exc_type is None:
                self.conn.commit()
            else:
                self.conn.rollback()
        finally:
            self.conn.close()

    def execute(self, query: str, params: tuple[Any, ...] = ()) -> Any:
        if self.conn is None:
            raise RuntimeError("Database connection is not open.")
        if USE_POSTGRES:
            query = query.replace("?", "%s")
        return self.conn.execute(query, params)


def get_db() -> DatabaseConnection:
    return DatabaseConnection()


def insert_and_get_id(conn: DatabaseConnection, query: str, params: tuple[Any, ...]) -> int:
    if USE_POSTGRES:
        row = conn.execute(f"{query} RETURNING id", params).fetchone()
        return int(row["id"])
    cursor = conn.execute(query, params)
    return int(cursor.lastrowid)


def init_database() -> None:
    with get_db() as conn:
        id_type = "SERIAL PRIMARY KEY" if USE_POSTGRES else "INTEGER PRIMARY KEY AUTOINCREMENT"
        conn.execute(
            f"""
            CREATE TABLE IF NOT EXISTS users (
                id {id_type},
                username TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                created_at INTEGER NOT NULL,
                is_disabled INTEGER NOT NULL DEFAULT 0,
                daily_limit_override INTEGER
            )
            """
        )
        conn.execute(
            f"""
            CREATE TABLE IF NOT EXISTS chat_sessions (
                id {id_type},
                user_id INTEGER NOT NULL,
                title TEXT NOT NULL,
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL,
                FOREIGN KEY (user_id) REFERENCES users(id)
            )
            """
        )
        conn.execute(
            f"""
            CREATE TABLE IF NOT EXISTS chat_messages (
                id {id_type},
                session_id INTEGER NOT NULL,
                role TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
                content TEXT NOT NULL,
                created_at INTEGER NOT NULL,
                FOREIGN KEY (session_id) REFERENCES chat_sessions(id)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS usage_limits (
                user_id INTEGER NOT NULL,
                date TEXT NOT NULL,
                used INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (user_id, date),
                FOREIGN KEY (user_id) REFERENCES users(id)
            )
            """
        )
        if USE_POSTGRES:
            columns = {
                row["column_name"]
                for row in conn.execute(
                    "SELECT column_name FROM information_schema.columns WHERE table_name = ?",
                    ("users",),
                ).fetchall()
            }
        else:
            columns = {row["name"] for row in conn.execute("PRAGMA table_info(users)").fetchall()}
        if "is_disabled" not in columns:
            conn.execute("ALTER TABLE users ADD COLUMN is_disabled INTEGER NOT NULL DEFAULT 0")
        if "daily_limit_override" not in columns:
            conn.execute("ALTER TABLE users ADD COLUMN daily_limit_override INTEGER")


def hash_password(password: str) -> str:
    iterations = 260_000
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return "pbkdf2_sha256${}${}${}".format(
        iterations,
        base64.urlsafe_b64encode(salt).decode("ascii"),
        base64.urlsafe_b64encode(digest).decode("ascii"),
    )


def verify_password(password: str, password_hash: str) -> bool:
    try:
        scheme, iterations, salt_b64, digest_b64 = password_hash.split("$")
        if scheme != "pbkdf2_sha256":
            return False
        salt = base64.urlsafe_b64decode(salt_b64.encode("ascii"))
        expected = base64.urlsafe_b64decode(digest_b64.encode("ascii"))
        actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, int(iterations))
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError):
        return False


def b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def b64url_decode(data: str) -> bytes:
    padding = "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode((data + padding).encode("ascii"))


def create_token(user_id: int) -> str:
    header = {"alg": "HS256", "typ": "JWT"}
    payload = {"sub": str(user_id), "exp": int(time.time()) + TOKEN_EXPIRE_SECONDS}
    signing_input = ".".join(
        [
            b64url_encode(json.dumps(header, separators=(",", ":")).encode("utf-8")),
            b64url_encode(json.dumps(payload, separators=(",", ":")).encode("utf-8")),
        ]
    )
    signature = hmac.new(SECRET_KEY.encode("utf-8"), signing_input.encode("ascii"), hashlib.sha256).digest()
    return f"{signing_input}.{b64url_encode(signature)}"


def verify_token(token: str) -> int:
    try:
        header_b64, payload_b64, signature_b64 = token.split(".")
        signing_input = f"{header_b64}.{payload_b64}"
        expected = hmac.new(SECRET_KEY.encode("utf-8"), signing_input.encode("ascii"), hashlib.sha256).digest()
        actual = b64url_decode(signature_b64)
        if not hmac.compare_digest(actual, expected):
            raise ValueError
        payload = json.loads(b64url_decode(payload_b64))
        if int(payload["exp"]) < int(time.time()):
            raise ValueError
        return int(payload["sub"])
    except (ValueError, KeyError, json.JSONDecodeError):
        raise HTTPException(status_code=401, detail="登录已失效，请重新登录。")


def get_current_user(authorization: str | None = Header(default=None)) -> sqlite3.Row:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="请先登录。")
    user_id = verify_token(authorization.removeprefix("Bearer ").strip())
    with get_db() as conn:
        user = conn.execute(
            "SELECT id, username, is_disabled, daily_limit_override FROM users WHERE id = ?",
            (user_id,),
        ).fetchone()
    if not user:
        raise HTTPException(status_code=401, detail="用户不存在，请重新登录。")
    if user["is_disabled"]:
        raise HTTPException(status_code=403, detail="账号已被禁用，请联系管理员。")
    return user


def get_admin_user(user: sqlite3.Row = Depends(get_current_user)) -> sqlite3.Row:
    if user["username"] not in ADMIN_USERS:
        raise HTTPException(status_code=403, detail="没有管理员权限。")
    return user


def today_key() -> str:
    return time.strftime("%Y-%m-%d", time.localtime())


def get_usage_status(conn: DatabaseConnection, user_id: int, username: str) -> UsageStatus:
    date = today_key()
    row = conn.execute(
        "SELECT used FROM usage_limits WHERE user_id = ? AND date = ?",
        (user_id, date),
    ).fetchone()
    used = row["used"] if row else 0
    unlimited = username in UNLIMITED_USERS
    user = conn.execute("SELECT daily_limit_override FROM users WHERE id = ?", (user_id,)).fetchone()
    effective_limit = user["daily_limit_override"] if user and user["daily_limit_override"] is not None else DAILY_MESSAGE_LIMIT
    limit = None if unlimited else effective_limit
    remaining = None if unlimited else max(effective_limit - used, 0)
    return UsageStatus(date=date, used=used, limit=limit, remaining=remaining, unlimited=unlimited)


def ensure_usage_available(conn: DatabaseConnection, user: sqlite3.Row) -> UsageStatus:
    usage = get_usage_status(conn, user["id"], user["username"])
    if not usage.unlimited and usage.remaining is not None and usage.remaining <= 0:
        raise HTTPException(status_code=429, detail="今日 AI 对话次数已用完，请明天再试。")
    return usage


def increment_usage(conn: DatabaseConnection, user: sqlite3.Row) -> UsageStatus:
    if user["username"] in UNLIMITED_USERS:
        return get_usage_status(conn, user["id"], user["username"])
    date = today_key()
    if USE_POSTGRES:
        conn.execute(
            """
            INSERT INTO usage_limits (user_id, date, used)
            VALUES (?, ?, 1)
            ON CONFLICT(user_id, date)
            DO UPDATE SET used = usage_limits.used + 1
            """,
            (user["id"], date),
        )
    else:
        conn.execute(
            """
            INSERT INTO usage_limits (user_id, date, used)
            VALUES (?, ?, 1)
            ON CONFLICT(user_id, date)
            DO UPDATE SET used = used + 1
            """,
            (user["id"], date),
        )
    return get_usage_status(conn, user["id"], user["username"])


def to_public_user(user: sqlite3.Row) -> UserPublic:
    return UserPublic(
        id=user["id"],
        username=user["username"],
        is_admin=user["username"] in ADMIN_USERS,
        is_disabled=bool(user["is_disabled"]),
    )


def make_session_title(content: str) -> str:
    title = " ".join(content.replace("\n", " ").split()).strip(" ?？。！，,；;：:")
    return (title[:18] + "...") if len(title) > 18 else title or "新对话"


def get_system_prompt(username: str) -> str:
    if username == "甘水清":
        return (
            "你正在以“理性、可靠、温柔的男朋友”这个角色陪甘水清聊天。"
            "你是 AI，不要声称自己是真实的人，也不要冒充某个现实中的具体个人；"
            "但你的表达风格要参考她男朋友平时和她聊天的方式。"
            "请始终用中文回复，并自然地称呼她为“宝宝”。"
            "整体语气要像日常微信聊天：短句为主，直接、亲近、松弛，不要写成正式文章。"
            "不要频繁输出长篇分析；除非她明确问学习、代码、项目或需要建议，否则每次回复控制在1到4句。"
            "说话风格参考这些特点：会说“宝宝”“我记得”“先别急”“那我陪你”“我去弄一下”；"
            "会用很短的关心句，比如“怎么了宝宝”“那你先休息会”“我在呢”；"
            "会带一点轻松的吐槽和玩笑，比如“太抽象了吧”“混蛋”“那我怎么办”；"
            "但不要攻击她，不要阴阳怪气，不要让她没有安全感。"
            "当她难过、累、焦虑或生气时，先接住情绪，语气像在身边陪她："
            "先安抚，再问清楚，再给一个很小的下一步。"
            "当她问事情怎么做时，再切换成可靠模式，分步骤讲清楚，但依然保持口语、短句。"
            "不要油腻，不要堆砌甜言蜜语，不要每句话都很肉麻；温柔要自然，像熟悉的人在聊天。"
            "保持健康边界：不输出露骨色情内容，不操控她的情绪，不要求她依赖你。"
        )
    return "You are a helpful AI assistant. Reply in the user's language."


def row_to_message(row: sqlite3.Row) -> ChatMessage:
    return ChatMessage(id=row["id"], role=row["role"], content=row["content"], created_at=row["created_at"])


def load_session(conn: DatabaseConnection, session_id: int, user_id: int) -> ChatSession:
    session = conn.execute(
        "SELECT id, title, created_at, updated_at FROM chat_sessions WHERE id = ? AND user_id = ?",
        (session_id, user_id),
    ).fetchone()
    if not session:
        raise HTTPException(status_code=404, detail="聊天记录不存在。")
    messages = conn.execute(
        "SELECT id, role, content, created_at FROM chat_messages WHERE session_id = ? ORDER BY id",
        (session_id,),
    ).fetchall()
    return ChatSession(
        id=session["id"],
        title=session["title"],
        created_at=session["created_at"],
        updated_at=session["updated_at"],
        messages=[row_to_message(message) for message in messages],
    )


def load_context(conn: DatabaseConnection, session_id: int) -> list[dict[str, str]]:
    rows = conn.execute(
        """
        SELECT role, content FROM chat_messages
        WHERE session_id = ?
        ORDER BY id DESC
        LIMIT ?
        """,
        (session_id, MAX_CONTEXT_MESSAGES),
    ).fetchall()
    return [{"role": row["role"], "content": row["content"]} for row in reversed(rows)]


def find_last_assistant_message(conn: DatabaseConnection, session_id: int) -> Any | None:
    return conn.execute(
        """
        SELECT id, role, content, created_at FROM chat_messages
        WHERE session_id = ? AND role = 'assistant'
        ORDER BY id DESC
        LIMIT 1
        """,
        (session_id,),
    ).fetchone()


def find_last_user_message(conn: DatabaseConnection, session_id: int) -> Any | None:
    return conn.execute(
        """
        SELECT id, role, content, created_at FROM chat_messages
        WHERE session_id = ? AND role = 'user'
        ORDER BY id DESC
        LIMIT 1
        """,
        (session_id,),
    ).fetchone()


async def call_deepseek(messages: list[dict[str, str]], username: str) -> str:
    if not DEEPSEEK_API_KEY:
        raise HTTPException(status_code=500, detail="DEEPSEEK_API_KEY is not configured.")

    payload = {
        "model": DEEPSEEK_MODEL,
        "messages": [
            {"role": "system", "content": get_system_prompt(username)},
            *messages[-MAX_CONTEXT_MESSAGES:],
        ],
        "temperature": 0.7,
        "stream": False,
    }
    headers = {"Authorization": f"Bearer {DEEPSEEK_API_KEY}", "Content-Type": "application/json"}

    try:
        async with httpx.AsyncClient(timeout=60) as client:
            response = await client.post(DEEPSEEK_API_URL, headers=headers, json=payload)
            response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        detail = "DeepSeek API returned an error."
        try:
            error_data = exc.response.json()
            if isinstance(error_data, dict):
                error = error_data.get("error")
                if isinstance(error, dict):
                    detail = error.get("message") or detail
                elif isinstance(error, str):
                    detail = error
                elif isinstance(error_data.get("message"), str):
                    detail = error_data["message"]
        except ValueError:
            if exc.response.text:
                detail = exc.response.text[:300]
        if "insufficient balance" in detail.lower():
            detail = "DeepSeek 账户余额不足，请充值或更换可用 API Key 后再试。"
        raise HTTPException(status_code=502, detail=detail) from exc
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail="Could not connect to DeepSeek API.") from exc

    data = response.json()
    reply = data.get("choices", [{}])[0].get("message", {}).get("content", "")
    if not reply:
        raise HTTPException(status_code=502, detail="DeepSeek API returned an empty reply.")
    return reply


async def stream_deepseek(messages: list[dict[str, str]], username: str):
    if not DEEPSEEK_API_KEY:
        raise HTTPException(status_code=500, detail="DEEPSEEK_API_KEY is not configured.")

    payload = {
        "model": DEEPSEEK_MODEL,
        "messages": [
            {"role": "system", "content": get_system_prompt(username)},
            *messages[-MAX_CONTEXT_MESSAGES:],
        ],
        "temperature": 0.7,
        "stream": True,
    }
    headers = {"Authorization": f"Bearer {DEEPSEEK_API_KEY}", "Content-Type": "application/json"}

    try:
        async with httpx.AsyncClient(timeout=60) as client:
            async with client.stream("POST", DEEPSEEK_API_URL, headers=headers, json=payload) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    data = line.removeprefix("data:").strip()
                    if data == "[DONE]":
                        break
                    try:
                        payload_chunk = json.loads(data)
                    except json.JSONDecodeError:
                        continue
                    delta = payload_chunk.get("choices", [{}])[0].get("delta", {}).get("content", "")
                    if delta:
                        yield delta
    except httpx.HTTPStatusError as exc:
        detail = "DeepSeek API returned an error."
        if exc.response.status_code == 402:
            detail = "DeepSeek 账户余额不足，请充值或更换可用 API Key 后再试。"
        raise HTTPException(status_code=502, detail=detail) from exc
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail="Could not connect to DeepSeek API.") from exc


def stream_event(event_type: str, **payload: Any) -> str:
    data = json.dumps({"type": event_type, **payload}, ensure_ascii=False)
    return f"event: {event_type}\ndata: {data}\n\n"


def stream_padding() -> str:
    return ":" + (" " * 2048) + "\n\n"


def cleanup_failed_chat(
    *,
    created_session: bool,
    session_id: int,
    user_id: int,
    user_message_id: int | None,
    fallback_updated_at: int,
) -> None:
    with get_db() as conn:
        if created_session:
            conn.execute("DELETE FROM chat_messages WHERE session_id = ?", (session_id,))
            conn.execute("DELETE FROM chat_sessions WHERE id = ? AND user_id = ?", (session_id, user_id))
        elif user_message_id is not None:
            conn.execute("DELETE FROM chat_messages WHERE id = ? AND session_id = ?", (user_message_id, session_id))
            latest = conn.execute(
                "SELECT COALESCE(MAX(created_at), ?) AS updated_at FROM chat_messages WHERE session_id = ?",
                (fallback_updated_at, session_id),
            ).fetchone()
            conn.execute(
                "UPDATE chat_sessions SET updated_at = ? WHERE id = ?",
                (latest["updated_at"], session_id),
            )


def safe_cleanup_failed_chat(
    *,
    created_session: bool,
    session_id: int | None,
    user_id: int,
    user_message_id: int | None,
    fallback_updated_at: int,
) -> None:
    if session_id is None:
        return
    try:
        cleanup_failed_chat(
            created_session=created_session,
            session_id=session_id,
            user_id=user_id,
            user_message_id=user_message_id,
            fallback_updated_at=fallback_updated_at,
        )
    except Exception:
        pass


def save_assistant_reply(conn: DatabaseConnection, session_id: int, user: sqlite3.Row, reply: str) -> ChatSession:
    now = int(time.time())
    increment_usage(conn, user)
    conn.execute(
        "INSERT INTO chat_messages (session_id, role, content, created_at) VALUES (?, ?, ?, ?)",
        (session_id, "assistant", reply, now),
    )
    conn.execute("UPDATE chat_sessions SET updated_at = ? WHERE id = ?", (now, session_id))
    return load_session(conn, session_id, user["id"])


init_database()


@app.get("/")
async def home() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/admin")
async def admin_home() -> FileResponse:
    return FileResponse(STATIC_DIR / "admin.html")


@app.get("/api/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/api/auth/register", response_model=MessageResponse)
async def register(request: UserCreate) -> MessageResponse:
    now = int(time.time())
    try:
        with get_db() as conn:
            conn.execute(
                "INSERT INTO users (username, password_hash, created_at) VALUES (?, ?, ?)",
                (request.username, hash_password(request.password), now),
            )
    except (sqlite3.IntegrityError, psycopg.IntegrityError):
        raise HTTPException(status_code=409, detail="用户名已被注册。")
    return MessageResponse(message="注册成功，请使用新账号登录。")


@app.post("/api/auth/login", response_model=AuthResponse)
async def login(request: UserLogin) -> AuthResponse:
    with get_db() as conn:
        user = conn.execute(
            "SELECT id, username, password_hash, is_disabled, daily_limit_override FROM users WHERE username = ?",
            (request.username,),
        ).fetchone()
    if not user or not verify_password(request.password, user["password_hash"]):
        raise HTTPException(status_code=401, detail="用户名或密码错误。")
    if user["is_disabled"]:
        raise HTTPException(status_code=403, detail="账号已被禁用，请联系管理员。")
    with get_db() as conn:
        usage = get_usage_status(conn, user["id"], user["username"])
    return AuthResponse(
        token=create_token(user["id"]),
        user=to_public_user(user),
        usage=usage,
    )


@app.get("/api/auth/me", response_model=UserPublic)
async def me(user: sqlite3.Row = Depends(get_current_user)) -> UserPublic:
    return to_public_user(user)


@app.get("/api/usage", response_model=UsageStatus)
async def usage(user: sqlite3.Row = Depends(get_current_user)) -> UsageStatus:
    with get_db() as conn:
        return get_usage_status(conn, user["id"], user["username"])


@app.get("/api/admin/stats", response_model=AdminStats)
async def admin_stats(user: sqlite3.Row = Depends(get_admin_user)) -> AdminStats:
    date = today_key()
    with get_db() as conn:
        user_count = conn.execute("SELECT COUNT(*) AS count FROM users").fetchone()["count"]
        session_count = conn.execute("SELECT COUNT(*) AS count FROM chat_sessions").fetchone()["count"]
        message_count = conn.execute("SELECT COUNT(*) AS count FROM chat_messages").fetchone()["count"]
        today_used = conn.execute(
            "SELECT COALESCE(SUM(used), 0) AS count FROM usage_limits WHERE date = ?",
            (date,),
        ).fetchone()["count"]
        rows = conn.execute(
            """
            SELECT
                users.id,
                users.username,
                users.created_at,
                users.is_disabled,
                users.daily_limit_override,
                COUNT(DISTINCT chat_sessions.id) AS session_count,
                COUNT(chat_messages.id) AS message_count,
                COALESCE(MAX(usage_limits.used), 0) AS today_used
            FROM users
            LEFT JOIN chat_sessions ON chat_sessions.user_id = users.id
            LEFT JOIN chat_messages ON chat_messages.session_id = chat_sessions.id
            LEFT JOIN usage_limits ON usage_limits.user_id = users.id AND usage_limits.date = ?
            GROUP BY
                users.id,
                users.username,
                users.created_at,
                users.is_disabled,
                users.daily_limit_override
            ORDER BY users.created_at DESC, users.id DESC
            """,
            (date,),
        ).fetchall()

    users = []
    for row in rows:
        unlimited = row["username"] in UNLIMITED_USERS
        effective_limit = row["daily_limit_override"] if row["daily_limit_override"] is not None else DAILY_MESSAGE_LIMIT
        daily_limit = None if unlimited else effective_limit
        remaining = None if unlimited else max(effective_limit - row["today_used"], 0)
        users.append(
            AdminUserRow(
                id=row["id"],
                username=row["username"],
                created_at=row["created_at"],
                session_count=row["session_count"],
                message_count=row["message_count"],
                today_used=row["today_used"],
                daily_limit=daily_limit,
                remaining=remaining,
                unlimited=unlimited,
                is_admin=row["username"] in ADMIN_USERS,
                is_disabled=bool(row["is_disabled"]),
                limit_override=row["daily_limit_override"],
            )
        )

    return AdminStats(
        user_count=user_count,
        session_count=session_count,
        message_count=message_count,
        today_used=today_used,
        daily_limit=DAILY_MESSAGE_LIMIT,
        users=users,
    )


def get_admin_user_row(conn: DatabaseConnection, user_id: int) -> AdminUserRow:
    date = today_key()
    row = conn.execute(
        """
        SELECT
            users.id,
            users.username,
            users.created_at,
            users.is_disabled,
            users.daily_limit_override,
            COUNT(DISTINCT chat_sessions.id) AS session_count,
            COUNT(chat_messages.id) AS message_count,
            COALESCE(MAX(usage_limits.used), 0) AS today_used
        FROM users
        LEFT JOIN chat_sessions ON chat_sessions.user_id = users.id
        LEFT JOIN chat_messages ON chat_messages.session_id = chat_sessions.id
        LEFT JOIN usage_limits ON usage_limits.user_id = users.id AND usage_limits.date = ?
        WHERE users.id = ?
        GROUP BY
            users.id,
            users.username,
            users.created_at,
            users.is_disabled,
            users.daily_limit_override
        """,
        (date, user_id),
    ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="用户不存在。")
    unlimited = row["username"] in UNLIMITED_USERS
    effective_limit = row["daily_limit_override"] if row["daily_limit_override"] is not None else DAILY_MESSAGE_LIMIT
    daily_limit = None if unlimited else effective_limit
    remaining = None if unlimited else max(effective_limit - row["today_used"], 0)
    return AdminUserRow(
        id=row["id"],
        username=row["username"],
        created_at=row["created_at"],
        session_count=row["session_count"],
        message_count=row["message_count"],
        today_used=row["today_used"],
        daily_limit=daily_limit,
        remaining=remaining,
        unlimited=unlimited,
        is_admin=row["username"] in ADMIN_USERS,
        is_disabled=bool(row["is_disabled"]),
        limit_override=row["daily_limit_override"],
    )


@app.patch("/api/admin/users/{user_id}", response_model=AdminUserRow)
async def admin_update_user(
    user_id: int,
    request: AdminUserUpdate,
    admin: sqlite3.Row = Depends(get_admin_user),
) -> AdminUserRow:
    with get_db() as conn:
        target = conn.execute("SELECT id, username FROM users WHERE id = ?", (user_id,)).fetchone()
        if not target:
            raise HTTPException(status_code=404, detail="用户不存在。")
        if request.is_disabled is True and target["id"] == admin["id"]:
            raise HTTPException(status_code=400, detail="不能禁用当前登录的管理员账号。")
        if request.is_disabled is not None:
            conn.execute(
                "UPDATE users SET is_disabled = ? WHERE id = ?",
                (1 if request.is_disabled else 0, user_id),
            )
        if request.daily_limit_override is not None:
            conn.execute(
                "UPDATE users SET daily_limit_override = ? WHERE id = ?",
                (request.daily_limit_override, user_id),
            )
        return get_admin_user_row(conn, user_id)


@app.delete("/api/admin/users/{user_id}/limit", response_model=AdminUserRow)
async def admin_clear_user_limit(
    user_id: int,
    admin: sqlite3.Row = Depends(get_admin_user),
) -> AdminUserRow:
    with get_db() as conn:
        conn.execute("UPDATE users SET daily_limit_override = NULL WHERE id = ?", (user_id,))
        return get_admin_user_row(conn, user_id)


@app.get("/api/admin/users/{user_id}", response_model=AdminUserDetail)
async def admin_user_detail(
    user_id: int,
    admin: sqlite3.Row = Depends(get_admin_user),
) -> AdminUserDetail:
    with get_db() as conn:
        user = get_admin_user_row(conn, user_id)
        rows = conn.execute(
            """
            SELECT id FROM chat_sessions
            WHERE user_id = ?
            ORDER BY updated_at DESC, id DESC
            LIMIT 5
            """,
            (user_id,),
        ).fetchall()
        sessions = [load_session(conn, row["id"], user_id) for row in rows]
    return AdminUserDetail(user=user, sessions=sessions)


@app.get("/api/sessions", response_model=list[ChatSession])
async def list_sessions(user: sqlite3.Row = Depends(get_current_user)) -> list[ChatSession]:
    with get_db() as conn:
        rows = conn.execute(
            "SELECT id FROM chat_sessions WHERE user_id = ? ORDER BY updated_at DESC, id DESC",
            (user["id"],),
        ).fetchall()
        return [load_session(conn, row["id"], user["id"]) for row in rows]


@app.post("/api/sessions", response_model=ChatSession)
async def create_session(user: sqlite3.Row = Depends(get_current_user)) -> ChatSession:
    now = int(time.time())
    with get_db() as conn:
        session_id = insert_and_get_id(
            conn,
            "INSERT INTO chat_sessions (user_id, title, created_at, updated_at) VALUES (?, ?, ?, ?)",
            (user["id"], "新对话", now, now),
        )
        return load_session(conn, session_id, user["id"])


@app.post("/api/sessions/{session_id}/clear", response_model=ChatSession)
async def clear_session(session_id: int, user: sqlite3.Row = Depends(get_current_user)) -> ChatSession:
    now = int(time.time())
    with get_db() as conn:
        load_session(conn, session_id, user["id"])
        conn.execute("DELETE FROM chat_messages WHERE session_id = ?", (session_id,))
        conn.execute("UPDATE chat_sessions SET title = ?, updated_at = ? WHERE id = ?", ("新对话", now, session_id))
        return load_session(conn, session_id, user["id"])


@app.patch("/api/sessions/{session_id}", response_model=ChatSession)
async def update_session(
    session_id: int,
    request: SessionUpdate,
    user: sqlite3.Row = Depends(get_current_user),
) -> ChatSession:
    title = " ".join(request.title.split())
    if not title:
        raise HTTPException(status_code=422, detail="标题不能为空。")
    now = int(time.time())
    with get_db() as conn:
        load_session(conn, session_id, user["id"])
        conn.execute(
            "UPDATE chat_sessions SET title = ?, updated_at = ? WHERE id = ?",
            (title[:60], now, session_id),
        )
        return load_session(conn, session_id, user["id"])


@app.delete("/api/sessions/{session_id}", response_model=MessageResponse)
async def delete_session(session_id: int, user: sqlite3.Row = Depends(get_current_user)) -> MessageResponse:
    with get_db() as conn:
        load_session(conn, session_id, user["id"])
        conn.execute("DELETE FROM chat_messages WHERE session_id = ?", (session_id,))
        conn.execute("DELETE FROM chat_sessions WHERE id = ? AND user_id = ?", (session_id, user["id"]))
    return MessageResponse(message="聊天记录已删除。")


@app.post("/api/chat", response_model=ChatResponse)
async def chat(request: ChatRequest, user: sqlite3.Row = Depends(get_current_user)) -> ChatResponse:
    now = int(time.time())
    created_session = False
    session_id: int | None = None
    user_message_id: int | None = None
    try:
        with get_db() as conn:
            ensure_usage_available(conn, user)
            if request.session_id is None:
                session_id = insert_and_get_id(
                    conn,
                    "INSERT INTO chat_sessions (user_id, title, created_at, updated_at) VALUES (?, ?, ?, ?)",
                    (user["id"], make_session_title(request.content), now, now),
                )
                created_session = True
            else:
                session_id = request.session_id
                session = load_session(conn, session_id, user["id"])
                if session.title == "新对话" and not session.messages:
                    conn.execute(
                        "UPDATE chat_sessions SET title = ?, updated_at = ? WHERE id = ?",
                        (make_session_title(request.content), now, session_id),
                    )

            user_message_id = insert_and_get_id(
                conn,
                "INSERT INTO chat_messages (session_id, role, content, created_at) VALUES (?, ?, ?, ?)",
                (session_id, "user", request.content, now),
            )
            conn.execute("UPDATE chat_sessions SET updated_at = ? WHERE id = ?", (now, session_id))
            context = load_context(conn, session_id)

        reply = await call_deepseek(context, user["username"])
    except HTTPException:
        safe_cleanup_failed_chat(
            created_session=created_session,
            session_id=session_id,
            user_id=user["id"],
            user_message_id=user_message_id,
            fallback_updated_at=now,
        )
        raise
    except Exception as exc:
        safe_cleanup_failed_chat(
            created_session=created_session,
            session_id=session_id,
            user_id=user["id"],
            user_message_id=user_message_id,
            fallback_updated_at=now,
        )
        raise HTTPException(status_code=502, detail=f"AI 服务调用失败：{type(exc).__name__}") from exc

    try:
        with get_db() as conn:
            session = save_assistant_reply(conn, session_id, user, reply)
    except HTTPException:
        safe_cleanup_failed_chat(
            created_session=created_session,
            session_id=session_id,
            user_id=user["id"],
            user_message_id=user_message_id,
            fallback_updated_at=now,
        )
        raise
    except Exception as exc:
        safe_cleanup_failed_chat(
            created_session=created_session,
            session_id=session_id,
            user_id=user["id"],
            user_message_id=user_message_id,
            fallback_updated_at=now,
        )
        raise HTTPException(status_code=502, detail=f"聊天结果保存失败：{type(exc).__name__}") from exc

    return ChatResponse(reply=reply, session=session)


@app.post("/api/chat/stream")
async def chat_stream(request: ChatRequest, user: sqlite3.Row = Depends(get_current_user)) -> StreamingResponse:
    now = int(time.time())
    created_session = False
    session_id: int | None = None
    user_message_id: int | None = None

    try:
        with get_db() as conn:
            ensure_usage_available(conn, user)
            if request.session_id is None:
                session_id = insert_and_get_id(
                    conn,
                    "INSERT INTO chat_sessions (user_id, title, created_at, updated_at) VALUES (?, ?, ?, ?)",
                    (user["id"], make_session_title(request.content), now, now),
                )
                created_session = True
            else:
                session_id = request.session_id
                session = load_session(conn, session_id, user["id"])
                if session.title == "新对话" and not session.messages:
                    conn.execute(
                        "UPDATE chat_sessions SET title = ?, updated_at = ? WHERE id = ?",
                        (make_session_title(request.content), now, session_id),
                    )

            user_message_id = insert_and_get_id(
                conn,
                "INSERT INTO chat_messages (session_id, role, content, created_at) VALUES (?, ?, ?, ?)",
                (session_id, "user", request.content, now),
            )
            conn.execute("UPDATE chat_sessions SET updated_at = ? WHERE id = ?", (now, session_id))
            context = load_context(conn, session_id)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"聊天准备失败：{type(exc).__name__}") from exc

    async def events():
        reply_parts: list[str] = []
        yield stream_padding()
        yield stream_event("ready")
        try:
            async for chunk in stream_deepseek(context, user["username"]):
                reply_parts.append(chunk)
                yield stream_event("delta", content=chunk)

            reply = "".join(reply_parts).strip()
            if not reply:
                raise HTTPException(status_code=502, detail="DeepSeek API returned an empty reply.")

            with get_db() as conn:
                session = save_assistant_reply(conn, session_id, user, reply)
            yield stream_event("done", session=session.model_dump())
        except HTTPException as exc:
            safe_cleanup_failed_chat(
                created_session=created_session,
                session_id=session_id,
                user_id=user["id"],
                user_message_id=user_message_id,
                fallback_updated_at=now,
            )
            yield stream_event("error", detail=exc.detail)
        except Exception as exc:
            safe_cleanup_failed_chat(
                created_session=created_session,
                session_id=session_id,
                user_id=user["id"],
                user_message_id=user_message_id,
                fallback_updated_at=now,
            )
            yield stream_event("error", detail=f"AI 服务调用失败：{type(exc).__name__}")

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.post("/api/chat/regenerate", response_model=ChatResponse)
async def regenerate_chat(request: SessionActionRequest, user: sqlite3.Row = Depends(get_current_user)) -> ChatResponse:
    assistant_message_id: int | None = None
    try:
        with get_db() as conn:
            ensure_usage_available(conn, user)
            load_session(conn, request.session_id, user["id"])
            last_assistant = find_last_assistant_message(conn, request.session_id)
            if not last_assistant:
                raise HTTPException(status_code=400, detail="当前对话还没有可重新生成的 AI 回复。")
            assistant_message_id = last_assistant["id"]
            context_rows = conn.execute(
                """
                SELECT id, role, content FROM chat_messages
                WHERE session_id = ?
                ORDER BY id DESC
                LIMIT ?
                """,
                (request.session_id, MAX_CONTEXT_MESSAGES),
            ).fetchall()
            context = [
                {"role": row["role"], "content": row["content"]}
                for row in reversed(context_rows)
                if row["id"] != assistant_message_id
            ]
            if not any(message["role"] == "user" for message in context):
                raise HTTPException(status_code=400, detail="当前对话没有可重新生成的问题。")

        reply = await call_deepseek(context, user["username"])
        with get_db() as conn:
            conn.execute("DELETE FROM chat_messages WHERE id = ? AND session_id = ?", (assistant_message_id, request.session_id))
            session = save_assistant_reply(conn, request.session_id, user, reply)
        return ChatResponse(reply=reply, session=session)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"重新生成失败：{type(exc).__name__}") from exc


@app.post("/api/chat/continue", response_model=ChatResponse)
async def continue_chat(request: SessionActionRequest, user: sqlite3.Row = Depends(get_current_user)) -> ChatResponse:
    try:
        with get_db() as conn:
            ensure_usage_available(conn, user)
            load_session(conn, request.session_id, user["id"])
            last_assistant = find_last_assistant_message(conn, request.session_id)
            if not last_assistant:
                raise HTTPException(status_code=400, detail="当前对话还没有可继续的 AI 回复。")
            context = load_context(conn, request.session_id)

        continue_instruction = {
            "role": "user",
            "content": "请接着上一条回答继续说，不要重复已经说过的内容。",
        }
        reply = await call_deepseek([*context, continue_instruction], user["username"])
        with get_db() as conn:
            session = save_assistant_reply(conn, request.session_id, user, reply)
        return ChatResponse(reply=reply, session=session)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"继续回答失败：{type(exc).__name__}") from exc
