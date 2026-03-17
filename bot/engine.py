"""Bot engine — orchestrates browser launch, meeting join, and audio recording."""

from __future__ import annotations

import asyncio
import logging
import signal
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from playwright.async_api import async_playwright

from bot.platforms.base import PlatformJoiner
from bot.platforms.google_meet import GoogleMeetJoiner
from bot.platforms.webex import WebexJoiner
from bot.platforms.zoom import ZoomJoiner
from bot.recorder import AudioRecorder
from bot.stealth import apply_stealth, get_realistic_user_agent, random_delay
from config import settings

logger = logging.getLogger(__name__)

_PLATFORM_JOINERS: dict[str, type[PlatformJoiner]] = {
    "webex": WebexJoiner,
    "zoom": ZoomJoiner,
    "google_meet": GoogleMeetJoiner,
}

MAX_MEETING_DURATION = 6 * 60 * 60  # 6 hours
POLL_INTERVAL = 30  # seconds


@dataclass(frozen=True)
class BotResult:
    """Immutable result from a bot session."""

    audio_path: str
    duration_seconds: float
    platform: str
    meeting_url: str
    status: str
    error: str | None = None


class BotEngine:
    """Orchestrates headless meeting join and audio recording."""

    def __init__(
        self,
        platform: str,
        meeting_url: str,
        display_name: str | None = None,
    ) -> None:
        if platform not in _PLATFORM_JOINERS:
            raise ValueError(
                f"Unsupported platform: {platform}. "
                f"Choose from: {', '.join(_PLATFORM_JOINERS)}"
            )

        self._platform = platform
        self._meeting_url = meeting_url
        self._display_name = display_name or settings.display_name
        self._joiner = _PLATFORM_JOINERS[platform]()
        self._recorder = AudioRecorder()
        self._shutdown_requested = False

    async def run(self) -> BotResult:
        """Run the full bot lifecycle: launch → join → record → leave."""
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        audio_filename = f"{self._platform}_{timestamp}.wav"
        audio_path = str(settings.recordings_dir / audio_filename)

        # Ensure output directory exists
        settings.recordings_dir.mkdir(parents=True, exist_ok=True)

        # Setup graceful shutdown
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, self._request_shutdown)

        async with async_playwright() as pw:
            browser = await pw.chromium.launch(
                headless=True,
                args=[
                    "--no-sandbox",
                    "--disable-setuid-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-gpu",
                    "--use-fake-ui-for-media-stream",  # Auto-allow mic/camera
                    "--use-fake-device-for-media-stream",
                    "--autoplay-policy=no-user-gesture-required",
                    f"--user-agent={get_realistic_user_agent()}",
                ],
            )

            context = await browser.new_context(
                viewport={"width": 1920, "height": 1080},
                user_agent=get_realistic_user_agent(),
                locale="en-US",
                timezone_id="America/New_York",
                permissions=["microphone", "camera"],
            )

            page = await context.new_page()

            try:
                # Apply anti-detection
                await apply_stealth(page)

                # Join meeting
                logger.info(
                    "Joining %s meeting: %s as '%s'",
                    self._platform,
                    self._meeting_url,
                    self._display_name,
                )
                joined = await self._joiner.join(
                    page, self._meeting_url, self._display_name
                )

                if not joined:
                    # Save debug screenshot
                    debug_path = str(
                        settings.recordings_dir / "debug_failed_join.png"
                    )
                    try:
                        await page.screenshot(path=debug_path, full_page=True)
                        logger.info("Debug screenshot saved: %s", debug_path)
                    except Exception as ss_err:
                        logger.warning("Could not save screenshot: %s", ss_err)

                    return BotResult(
                        audio_path="",
                        duration_seconds=0,
                        platform=self._platform,
                        meeting_url=self._meeting_url,
                        status="failed",
                        error="Failed to join meeting",
                    )

                # Start recording
                await self._recorder.start(audio_path)
                logger.info("Recording started")

                # Monitor meeting until it ends or timeout
                elapsed = 0.0
                while (
                    not self._shutdown_requested
                    and elapsed < MAX_MEETING_DURATION
                ):
                    await asyncio.sleep(POLL_INTERVAL)
                    elapsed += POLL_INTERVAL

                    if not await self._joiner.is_in_meeting(page):
                        logger.info("Meeting has ended")
                        break

                    if elapsed % 300 < POLL_INTERVAL:
                        logger.info(
                            "Still recording... (%.0f min)", elapsed / 60
                        )

                # Stop recording
                final_path = await self._recorder.stop()
                duration = self._recorder.duration_seconds

                # Leave meeting gracefully
                try:
                    await self._joiner.leave_meeting(page)
                except Exception as e:
                    logger.warning("Error leaving meeting: %s", e)

                logger.info(
                    "Bot session complete. Duration: %.1f min, Audio: %s",
                    duration / 60,
                    final_path,
                )

                return BotResult(
                    audio_path=final_path,
                    duration_seconds=duration,
                    platform=self._platform,
                    meeting_url=self._meeting_url,
                    status="completed",
                )

            except Exception as e:
                logger.error("Bot engine error: %s", e, exc_info=True)
                await self._recorder.cleanup()
                return BotResult(
                    audio_path=audio_path if Path(audio_path).exists() else "",
                    duration_seconds=self._recorder.duration_seconds,
                    platform=self._platform,
                    meeting_url=self._meeting_url,
                    status="failed",
                    error=str(e),
                )
            finally:
                await context.close()
                await browser.close()

    def _request_shutdown(self) -> None:
        """Signal the bot to stop recording and leave."""
        logger.info("Shutdown requested")
        self._shutdown_requested = True


async def run_bot_cli(platform: str, meeting_url: str, display_name: str) -> BotResult:
    """CLI entry point for running the bot."""
    engine = BotEngine(platform, meeting_url, display_name)
    return await engine.run()


if __name__ == "__main__":
    import argparse

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    parser = argparse.ArgumentParser(description="MeetScribe Bot Engine")
    parser.add_argument("--platform", required=True, choices=list(_PLATFORM_JOINERS))
    parser.add_argument("--meeting-url", required=True)
    parser.add_argument("--display-name", default=settings.display_name)
    args = parser.parse_args()

    result = asyncio.run(
        run_bot_cli(args.platform, args.meeting_url, args.display_name)
    )
    print(f"Result: {result}")
    sys.exit(0 if result.status == "completed" else 1)
