"""Webex meeting joiner — guest join via public web client."""

from __future__ import annotations

import logging
import re

from playwright.async_api import Page

from bot.platforms.base import PlatformJoiner
from bot.stealth import human_like_click, random_delay

logger = logging.getLogger(__name__)

# Selectors with fallbacks for Webex web client
_NAME_INPUT_SELECTORS = [
    'input[placeholder*="name" i]',
    'input[aria-label*="name" i]',
    'input[data-test="guest-name"]',
    "#guest-name",
]

_JOIN_BUTTON_SELECTORS = [
    'button:has-text("Join meeting")',
    'button:has-text("Join")',
    'button[data-test="join-meeting-button"]',
    'button[aria-label*="Join" i]',
]

_GUEST_JOIN_SELECTORS = [
    'button:has-text("Join as a guest")',
    'button:has-text("Guest")',
    'a:has-text("Join as a guest")',
]

_LEAVE_BUTTON_SELECTORS = [
    'button[aria-label*="Leave" i]',
    'button:has-text("Leave meeting")',
    'button:has-text("Leave")',
    'button[data-test="leave-meeting"]',
]

_MUTE_MIC_SELECTORS = [
    'button[aria-label*="mute" i]',
    'button[aria-label*="Mute" i]',
    'button[data-test="mute-audio"]',
]

_MUTE_VIDEO_SELECTORS = [
    'button[aria-label*="Stop video" i]',
    'button[aria-label*="video" i]',
    'button[data-test="mute-video"]',
]


class WebexJoiner(PlatformJoiner):
    """Join Webex meetings as a guest via the web client."""

    def parse_meeting_url(self, meeting_input: str) -> str:
        """Convert meeting input to a joinable Webex URL."""
        meeting_input = meeting_input.strip()

        # Already a full URL
        if re.match(r"https?://.*\.webex\.com/", meeting_input):
            return meeting_input

        # Numeric meeting ID
        cleaned = re.sub(r"[\s\-]", "", meeting_input)
        if cleaned.isdigit() and len(cleaned) >= 9:
            return f"https://web.webex.com/meet?meetingId={cleaned}"

        # Assume it's a personal room slug: user@site or just a path
        if "." in meeting_input and "/" not in meeting_input:
            return f"https://{meeting_input}"

        return meeting_input

    async def join(self, page: Page, meeting_url: str, display_name: str) -> bool:
        """Join a Webex meeting as a guest."""
        url = self.parse_meeting_url(meeting_url)
        logger.info("Navigating to Webex: %s", url)

        await page.goto(url, wait_until="domcontentloaded", timeout=60000)
        await random_delay(3, 6)

        # Step 1: Click "Join as a guest" if present
        if await self._try_click_selectors(page, _GUEST_JOIN_SELECTORS):
            logger.info("Clicked 'Join as a guest'")
            await random_delay(2, 4)

        # Step 2: Enter display name
        name_filled = False
        for selector in _NAME_INPUT_SELECTORS:
            try:
                input_el = await page.wait_for_selector(selector, timeout=5000)
                if input_el:
                    await input_el.clear()
                    await input_el.type(display_name, delay=random_delay_ms())
                    name_filled = True
                    logger.info("Entered display name: %s", display_name)
                    break
            except Exception:
                continue

        if not name_filled:
            logger.warning("Could not find name input field, proceeding anyway")

        await random_delay(1, 3)

        # Step 3: Mute microphone and camera
        await self._try_click_selectors(page, _MUTE_MIC_SELECTORS)
        await self._try_click_selectors(page, _MUTE_VIDEO_SELECTORS)

        # Step 4: Click Join
        joined = await self._try_click_selectors(page, _JOIN_BUTTON_SELECTORS)
        if not joined:
            logger.error("Could not find join button")
            return False

        logger.info("Clicked join button, waiting for meeting to load...")
        await random_delay(5, 10)

        # Step 5: Verify we're in the meeting
        for _ in range(30):  # Wait up to 5 minutes
            if await self.is_in_meeting(page):
                logger.info("Successfully joined Webex meeting")
                return True
            await random_delay(8, 12)

        logger.error("Timed out waiting to join meeting")
        return False

    async def is_in_meeting(self, page: Page) -> bool:
        """Check if currently in an active Webex meeting."""
        indicators = [
            '[data-test="participant-list"]',
            '[aria-label*="participant" i]',
            '[aria-label*="Leave" i]',
            'button:has-text("Leave")',
            ".meeting-controls",
            "#meeting-container",
        ]
        for selector in indicators:
            try:
                el = await page.query_selector(selector)
                if el:
                    return True
            except Exception:
                continue
        return False

    async def leave_meeting(self, page: Page) -> None:
        """Leave the Webex meeting."""
        logger.info("Leaving Webex meeting")
        await self._try_click_selectors(page, _LEAVE_BUTTON_SELECTORS)
        await random_delay(2, 4)

        # Confirm leave if dialog appears
        confirm_selectors = [
            'button:has-text("Leave meeting")',
            'button:has-text("Leave")',
            'button:has-text("End")',
        ]
        await self._try_click_selectors(page, confirm_selectors)

    async def _try_click_selectors(self, page: Page, selectors: list[str]) -> bool:
        """Try clicking the first matching selector. Returns True if clicked."""
        for selector in selectors:
            try:
                el = await page.wait_for_selector(selector, timeout=3000)
                if el:
                    await human_like_click(page, selector)
                    return True
            except Exception:
                continue
        return False


def random_delay_ms() -> int:
    """Return a random typing delay in milliseconds."""
    import random
    return random.randint(50, 150)
