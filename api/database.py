"""SQLite database layer for MeetScribe using aiosqlite."""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Optional

import aiosqlite

from api.models import (
    BotStatus,
    BotStatusEnum,
    Platform,
    Transcript,
    TranscriptListItem,
    TranscriptSegment,
)
from config import settings

logger = logging.getLogger(__name__)

_DB_PATH = settings.database_path


async def init_db() -> None:
    """Initialize database tables."""
    async with aiosqlite.connect(_DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS bots (
                id TEXT PRIMARY KEY,
                status TEXT NOT NULL DEFAULT 'joining',
                platform TEXT NOT NULL,
                meeting_url TEXT NOT NULL,
                display_name TEXT NOT NULL,
                audio_path TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                error TEXT
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS transcripts (
                id TEXT PRIMARY KEY,
                bot_id TEXT NOT NULL,
                full_text TEXT NOT NULL,
                segments_json TEXT NOT NULL,
                duration_seconds REAL NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                FOREIGN KEY (bot_id) REFERENCES bots(id)
            )
        """)
        await db.commit()
    logger.info("Database initialized at %s", _DB_PATH)


async def create_bot(
    platform: Platform,
    meeting_url: str,
    display_name: str,
) -> BotStatus:
    """Create a new bot record and return its status."""
    bot_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()

    async with aiosqlite.connect(_DB_PATH) as db:
        await db.execute(
            """INSERT INTO bots (id, status, platform, meeting_url, display_name, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (bot_id, BotStatusEnum.JOINING.value, platform.value, meeting_url, display_name, now, now),
        )
        await db.commit()

    return BotStatus(
        id=bot_id,
        status=BotStatusEnum.JOINING,
        platform=platform,
        meeting_url=meeting_url,
        display_name=display_name,
        created_at=datetime.fromisoformat(now),
    )


async def update_bot_status(
    bot_id: str,
    status: BotStatusEnum,
    error: Optional[str] = None,
    audio_path: Optional[str] = None,
) -> None:
    """Update bot status (creates new row conceptually; mutates DB record)."""
    now = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(_DB_PATH) as db:
        if audio_path:
            await db.execute(
                "UPDATE bots SET status=?, updated_at=?, error=?, audio_path=? WHERE id=?",
                (status.value, now, error, audio_path, bot_id),
            )
        else:
            await db.execute(
                "UPDATE bots SET status=?, updated_at=?, error=? WHERE id=?",
                (status.value, now, error, bot_id),
            )
        await db.commit()


async def get_bot(bot_id: str) -> Optional[BotStatus]:
    """Retrieve bot status by ID."""
    async with aiosqlite.connect(_DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM bots WHERE id=?", (bot_id,)) as cursor:
            row = await cursor.fetchone()
            if not row:
                return None
            return BotStatus(
                id=row["id"],
                status=BotStatusEnum(row["status"]),
                platform=Platform(row["platform"]),
                meeting_url=row["meeting_url"],
                display_name=row["display_name"],
                created_at=datetime.fromisoformat(row["created_at"]),
                error=row["error"],
            )


async def create_transcript(
    bot_id: str,
    full_text: str,
    segments: list[TranscriptSegment],
    duration_seconds: float,
) -> str:
    """Store a transcript and return its ID."""
    transcript_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    segments_json = json.dumps([s.model_dump() for s in segments])

    async with aiosqlite.connect(_DB_PATH) as db:
        await db.execute(
            """INSERT INTO transcripts (id, bot_id, full_text, segments_json, duration_seconds, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (transcript_id, bot_id, full_text, segments_json, duration_seconds, now),
        )
        await db.commit()

    return transcript_id


async def get_transcript(transcript_id: str) -> Optional[Transcript]:
    """Retrieve a single transcript with its segments."""
    async with aiosqlite.connect(_DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """SELECT t.*, b.platform, b.meeting_url
               FROM transcripts t JOIN bots b ON t.bot_id = b.id
               WHERE t.id=?""",
            (transcript_id,),
        ) as cursor:
            row = await cursor.fetchone()
            if not row:
                return None
            segments = [TranscriptSegment(**s) for s in json.loads(row["segments_json"])]
            return Transcript(
                id=row["id"],
                bot_id=row["bot_id"],
                segments=segments,
                full_text=row["full_text"],
                created_at=datetime.fromisoformat(row["created_at"]),
                platform=Platform(row["platform"]),
                meeting_url=row["meeting_url"],
                duration_seconds=row["duration_seconds"],
            )


async def list_transcripts(
    limit: int = 50,
    offset: int = 0,
    query: Optional[str] = None,
) -> list[TranscriptListItem]:
    """List transcripts with optional full-text search."""
    async with aiosqlite.connect(_DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        if query:
            sql = """SELECT t.id, t.bot_id, t.full_text, t.duration_seconds, t.created_at,
                            b.meeting_url, b.platform
                     FROM transcripts t JOIN bots b ON t.bot_id = b.id
                     WHERE t.full_text LIKE ?
                     ORDER BY t.created_at DESC LIMIT ? OFFSET ?"""
            params = (f"%{query}%", limit, offset)
        else:
            sql = """SELECT t.id, t.bot_id, t.full_text, t.duration_seconds, t.created_at,
                            b.meeting_url, b.platform
                     FROM transcripts t JOIN bots b ON t.bot_id = b.id
                     ORDER BY t.created_at DESC LIMIT ? OFFSET ?"""
            params = (limit, offset)

        async with db.execute(sql, params) as cursor:
            rows = await cursor.fetchall()
            return [
                TranscriptListItem(
                    id=row["id"],
                    bot_id=row["bot_id"],
                    meeting_url=row["meeting_url"],
                    platform=Platform(row["platform"]),
                    created_at=datetime.fromisoformat(row["created_at"]),
                    duration_seconds=row["duration_seconds"],
                    snippet=row["full_text"][:200],
                )
                for row in rows
            ]


async def delete_transcript(transcript_id: str) -> bool:
    """Delete a transcript. Returns True if deleted."""
    async with aiosqlite.connect(_DB_PATH) as db:
        cursor = await db.execute("DELETE FROM transcripts WHERE id=?", (transcript_id,))
        await db.commit()
        return cursor.rowcount > 0
