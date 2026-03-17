"""Lightweight speaker diarization using silence-gap heuristics.

For CPU-only deployments, pyannote.audio is too heavy. This module provides
a simple heuristic: consecutive speech segments separated by >2s silence
are assigned to different speakers. This is NOT true diarization but
provides basic speaker change detection for meeting transcripts.

For better diarization, enable pyannote with a GPU by setting
DIARIZE_ENGINE=pyannote in environment variables.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from transcriber.whisper_engine import Segment, TranscriptionResult

logger = logging.getLogger(__name__)

SILENCE_THRESHOLD_S = 2.0  # Seconds of silence to suggest speaker change
MAX_SPEAKERS = 10


@dataclass(frozen=True)
class DiarizedSegment:
    """A transcript segment with speaker label."""

    start: float
    end: float
    text: str
    speaker: str


def diarize_by_silence(
    result: TranscriptionResult,
    silence_threshold: float = SILENCE_THRESHOLD_S,
) -> tuple[DiarizedSegment, ...]:
    """Assign speaker labels based on silence gaps between segments.

    Heuristic: when silence between consecutive segments exceeds the
    threshold, increment the speaker counter. Cycles through speakers
    up to MAX_SPEAKERS.
    """
    if not result.segments:
        return ()

    diarized = []
    current_speaker_idx = 0

    for i, seg in enumerate(result.segments):
        if i > 0:
            gap = seg.start - result.segments[i - 1].end
            if gap >= silence_threshold:
                current_speaker_idx = (current_speaker_idx + 1) % MAX_SPEAKERS

        diarized.append(
            DiarizedSegment(
                start=seg.start,
                end=seg.end,
                text=seg.text,
                speaker=f"Speaker {current_speaker_idx + 1}",
            )
        )

    speaker_count = len({s.speaker for s in diarized})
    logger.info(
        "Silence-based diarization: %d segments, %d detected speakers",
        len(diarized),
        speaker_count,
    )

    return tuple(diarized)


def merge_short_segments(
    segments: tuple[DiarizedSegment, ...],
    min_duration: float = 0.5,
) -> tuple[DiarizedSegment, ...]:
    """Merge very short segments with the same speaker into longer ones."""
    if not segments:
        return ()

    merged = []
    current = segments[0]

    for seg in segments[1:]:
        same_speaker = seg.speaker == current.speaker
        short = (current.end - current.start) < min_duration

        if same_speaker or short:
            # Merge into current
            current = DiarizedSegment(
                start=current.start,
                end=seg.end,
                text=f"{current.text} {seg.text}",
                speaker=current.speaker if not short else seg.speaker,
            )
        else:
            merged.append(current)
            current = seg

    merged.append(current)
    return tuple(merged)


def format_transcript_markdown(segments: tuple[DiarizedSegment, ...]) -> str:
    """Format diarized segments as a readable Markdown transcript."""
    lines = []
    current_speaker = None

    for seg in segments:
        if seg.speaker != current_speaker:
            current_speaker = seg.speaker
            timestamp = _format_time(seg.start)
            lines.append(f"\n**{seg.speaker}** [{timestamp}]")

        lines.append(f"> {seg.text}")

    return "\n".join(lines).strip()


def _format_time(seconds: float) -> str:
    """Format seconds as HH:MM:SS."""
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    if h > 0:
        return f"{h:02d}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"
