"""API routes and dashboard views for MeetScribe."""

from __future__ import annotations

import json
import logging
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from api.database import (
    create_bot,
    create_transcript,
    delete_transcript,
    get_bot,
    get_transcript,
    list_transcripts,
    update_bot_status,
)
from api.models import (
    BotJoinRequest,
    BotStatus,
    BotStatusEnum,
    Platform,
    Transcript,
    TranscriptListItem,
    TranscriptSegment,
)
from config import settings

logger = logging.getLogger(__name__)

router = APIRouter()

_BASE_DIR = Path(__file__).resolve().parent.parent
templates = Jinja2Templates(directory=str(_BASE_DIR / "dashboard" / "templates"))


# ─── Background task: bot + transcribe pipeline ──────────────────────────

async def _run_bot_pipeline(bot_id: str, platform: str, meeting_url: str, display_name: str) -> None:
    """Background task: join meeting, record, transcribe, save."""
    try:
        # Phase 1: Recording
        await update_bot_status(bot_id, BotStatusEnum.RECORDING)

        from bot.engine import BotEngine

        engine = BotEngine(platform, meeting_url, display_name)
        result = await engine.run()

        if result.status != "completed" or not result.audio_path:
            await update_bot_status(
                bot_id, BotStatusEnum.FAILED, error=result.error or "Recording failed"
            )
            return

        await update_bot_status(
            bot_id, BotStatusEnum.TRANSCRIBING, audio_path=result.audio_path
        )

        # Phase 2: Transcription
        from transcriber.whisper_engine import WhisperTranscriber
        from transcriber.diarizer import diarize_by_silence, merge_short_segments

        transcriber = WhisperTranscriber()
        transcription = await transcriber.transcribe(result.audio_path)

        # Phase 3: Diarization
        diarized = diarize_by_silence(transcription)
        diarized = merge_short_segments(diarized)

        segments = [
            TranscriptSegment(
                speaker=seg.speaker,
                start_time=seg.start,
                end_time=seg.end,
                text=seg.text,
            )
            for seg in diarized
        ]

        full_text = transcription.full_text

        # Save transcript
        await create_transcript(
            bot_id=bot_id,
            full_text=full_text,
            segments=segments,
            duration_seconds=result.duration_seconds,
        )

        await update_bot_status(bot_id, BotStatusEnum.COMPLETED)
        logger.info("Pipeline complete for bot %s", bot_id)

    except Exception as e:
        logger.error("Pipeline failed for bot %s: %s", bot_id, e, exc_info=True)
        await update_bot_status(bot_id, BotStatusEnum.FAILED, error=str(e))


# ─── API Endpoints ────────────────────────────────────────────────────────

@router.post("/api/bot/join", response_model=BotStatus)
async def api_join_meeting(
    req: BotJoinRequest,
    background_tasks: BackgroundTasks,
) -> BotStatus:
    """Deploy the bot to join a meeting."""
    bot = await create_bot(
        platform=req.platform,
        meeting_url=req.meeting_url,
        display_name=req.display_name,
    )
    background_tasks.add_task(
        _run_bot_pipeline,
        bot.id,
        req.platform.value,
        req.meeting_url,
        req.display_name,
    )
    logger.info("Bot %s deployed for %s meeting: %s", bot.id, req.platform, req.meeting_url)
    return bot


@router.get("/api/bot/{bot_id}", response_model=BotStatus)
async def api_get_bot_status(bot_id: str) -> BotStatus:
    """Get current bot status."""
    bot = await get_bot(bot_id)
    if not bot:
        raise HTTPException(status_code=404, detail="Bot not found")
    return bot


@router.get("/api/transcripts", response_model=list[TranscriptListItem])
async def api_list_transcripts(
    q: str | None = Query(None, description="Search query"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> list[TranscriptListItem]:
    """List transcripts with optional search."""
    return await list_transcripts(limit=limit, offset=offset, query=q)


@router.get("/api/transcripts/{transcript_id}", response_model=Transcript)
async def api_get_transcript(transcript_id: str) -> Transcript:
    """Get a single transcript with all segments."""
    transcript = await get_transcript(transcript_id)
    if not transcript:
        raise HTTPException(status_code=404, detail="Transcript not found")
    return transcript


@router.delete("/api/transcripts/{transcript_id}")
async def api_delete_transcript(transcript_id: str) -> dict:
    """Delete a transcript."""
    deleted = await delete_transcript(transcript_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Transcript not found")
    return {"deleted": True}


@router.get("/api/transcripts/{transcript_id}/download")
async def api_download_transcript(
    transcript_id: str,
    format: str = Query("md", regex="^(md|json|txt)$"),
) -> PlainTextResponse | JSONResponse:
    """Download transcript in various formats."""
    transcript = await get_transcript(transcript_id)
    if not transcript:
        raise HTTPException(status_code=404, detail="Transcript not found")

    if format == "json":
        return JSONResponse(
            content=transcript.model_dump(mode="json"),
            headers={"Content-Disposition": f'attachment; filename="transcript_{transcript_id}.json"'},
        )

    if format == "md":
        from transcriber.diarizer import DiarizedSegment, format_transcript_markdown

        diarized = tuple(
            DiarizedSegment(
                start=s.start_time, end=s.end_time, text=s.text, speaker=s.speaker
            )
            for s in transcript.segments
        )
        content = format_transcript_markdown(diarized)
        return PlainTextResponse(
            content=content,
            media_type="text/markdown",
            headers={"Content-Disposition": f'attachment; filename="transcript_{transcript_id}.md"'},
        )

    # Plain text
    return PlainTextResponse(
        content=transcript.full_text,
        headers={"Content-Disposition": f'attachment; filename="transcript_{transcript_id}.txt"'},
    )


# ─── Dashboard HTML Routes ───────────────────────────────────────────────

@router.get("/", response_class=HTMLResponse)
async def dashboard_home(request: Request):
    """Dashboard home — transcript list."""
    transcripts = await list_transcripts(limit=50)
    return templates.TemplateResponse(
        "index.html",
        {"request": request, "transcripts": transcripts},
    )


@router.get("/join", response_class=HTMLResponse)
async def dashboard_join(request: Request):
    """Bot deploy form."""
    return templates.TemplateResponse(
        "join.html",
        {"request": request, "default_name": settings.display_name},
    )


@router.get("/transcript/{transcript_id}", response_class=HTMLResponse)
async def dashboard_transcript_detail(request: Request, transcript_id: str):
    """Transcript detail view."""
    transcript = await get_transcript(transcript_id)
    if not transcript:
        raise HTTPException(status_code=404, detail="Transcript not found")
    return templates.TemplateResponse(
        "detail.html",
        {"request": request, "transcript": transcript},
    )
