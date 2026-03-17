"""Pydantic models for MeetScribe API."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class Platform(str, Enum):
    WEBEX = "webex"
    ZOOM = "zoom"
    GOOGLE_MEET = "google_meet"


class BotStatusEnum(str, Enum):
    JOINING = "joining"
    RECORDING = "recording"
    TRANSCRIBING = "transcribing"
    COMPLETED = "completed"
    FAILED = "failed"


class BotJoinRequest(BaseModel):
    meeting_url: str = Field(..., description="Meeting URL or meeting ID")
    display_name: str = Field(default="MeetScribe", description="Bot display name")
    platform: Platform = Field(..., description="Meeting platform")


class BotStatus(BaseModel):
    id: str
    status: BotStatusEnum
    platform: Platform
    meeting_url: str
    display_name: str
    created_at: datetime
    error: Optional[str] = None


class TranscriptSegment(BaseModel):
    speaker: str
    start_time: float
    end_time: float
    text: str


class Transcript(BaseModel):
    id: str
    bot_id: str
    segments: list[TranscriptSegment]
    full_text: str
    created_at: datetime
    platform: Platform
    meeting_url: str
    duration_seconds: float


class TranscriptListItem(BaseModel):
    id: str
    bot_id: str
    meeting_url: str
    platform: Platform
    created_at: datetime
    duration_seconds: float
    snippet: str
