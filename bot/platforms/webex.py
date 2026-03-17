"""Webex meeting joiner — guest join via public web client."""

from __future__ import annotations

import logging
import random
import re

from playwright.async_api import Page, FrameLocator

from bot.platforms.base import PlatformJoiner
from bot.stealth import human_like_click, random_delay

logger = logging.getLogger(__name__)


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

        # Personal room slug
        if "." in meeting_input and "/" not in meeting_input:
            return f"https://{meeting_input}"

        return meeting_input

    async def join(self, page: Page, meeting_url: str, display_name: str) -> bool:
        """Join a Webex meeting as a guest."""
        url = self.parse_meeting_url(meeting_url)
        logger.info("Navigating to Webex: %s", url)

        await page.goto(url, wait_until="load", timeout=90000)

        # Webex is a heavy SPA — give it time to fully render
        logger.info("Waiting for Webex page to fully load...")
        await random_delay(8, 12)

        # Debug: log page title and URL after load
        logger.info("Page title: %s", await page.title())
        logger.info("Page URL: %s", page.url)

        # Debug: dump visible text to help identify the page state
        await self._log_page_state(page)

        # Save a screenshot for debugging
        try:
            await page.screenshot(path="recordings/debug_webex_loaded.png", full_page=True)
            logger.info("Saved initial page screenshot")
        except Exception as e:
            logger.warning("Could not save screenshot: %s", e)

        # Step 1: Look for "Join as a guest" / "Guest" button
        guest_clicked = await self._find_and_click(
            page,
            texts=["Join as a guest", "Join as guest", "Guest", "Use web app"],
            description="guest join button",
        )
        if guest_clicked:
            logger.info("Clicked guest join option")
            await random_delay(3, 6)
            await self._log_page_state(page)

        # Step 2: Try to find and fill the name input
        # Strategy: search all input elements on the page
        name_filled = await self._fill_name_input(page, display_name)
        if not name_filled:
            # Try inside iframes
            for frame in page.frames:
                if frame == page.main_frame:
                    continue
                logger.info("Checking frame: %s", frame.url[:80])
                name_filled = await self._fill_name_input_in_frame(frame, display_name)
                if name_filled:
                    break

        if not name_filled:
            logger.warning("Could not find name input, continuing without it")

        await random_delay(1, 3)

        # Step 3: Mute mic and camera (best effort)
        await self._try_mute(page)

        # Step 4: Click the join/start button
        joined = await self._find_and_click(
            page,
            texts=["Join meeting", "Join", "Start meeting", "Connect"],
            description="join button",
        )

        if not joined:
            # Try inside iframes
            for frame in page.frames:
                if frame == page.main_frame:
                    continue
                joined = await self._find_and_click_in_frame(
                    frame,
                    texts=["Join meeting", "Join", "Start meeting", "Connect"],
                    description="join button (iframe)",
                )
                if joined:
                    break

        if not joined:
            # Last resort: try clicking any green/primary button
            joined = await self._click_primary_button(page)

        if not joined:
            logger.error("Could not find join button")
            try:
                await page.screenshot(
                    path="recordings/debug_no_join_button.png", full_page=True
                )
            except Exception:
                pass
            return False

        logger.info("Clicked join button, waiting for meeting to start...")
        await random_delay(8, 15)

        # Step 5: Wait up to 5 minutes for meeting to load
        for attempt in range(30):
            if await self.is_in_meeting(page):
                logger.info("Successfully joined Webex meeting")
                return True
            if attempt % 5 == 0:
                logger.info("Waiting for meeting... (attempt %d/30)", attempt + 1)
            await random_delay(8, 12)

        logger.error("Timed out waiting to join meeting")
        return False

    async def is_in_meeting(self, page: Page) -> bool:
        """Check if currently in an active Webex meeting."""
        # Check for typical in-meeting indicators
        indicators = [
            '[data-test="participant-list"]',
            '[aria-label*="participant" i]',
            '[aria-label*="Leave" i]',
            'button:has-text("Leave")',
            '[aria-label*="Mute" i]',
            ".meeting-controls",
            "#meeting-container",
            '[class*="meeting"]',
            '[data-test*="meeting"]',
        ]
        for selector in indicators:
            try:
                el = await page.query_selector(selector)
                if el:
                    return True
            except Exception:
                continue

        # Also check page title — Webex typically shows meeting info in title
        title = await page.title()
        if title and any(kw in title.lower() for kw in ["meeting", "webex"]):
            # Check if there's a leave button visible (means we're in)
            try:
                leave = await page.query_selector('button:has-text("Leave")')
                if leave:
                    return True
            except Exception:
                pass

        return False

    async def leave_meeting(self, page: Page) -> None:
        """Leave the Webex meeting."""
        logger.info("Leaving Webex meeting")
        await self._find_and_click(
            page,
            texts=["Leave meeting", "Leave", "End meeting", "End"],
            description="leave button",
        )
        await random_delay(2, 4)

    # ── Helper methods ────────────────────────────────────────────────

    async def _log_page_state(self, page: Page) -> None:
        """Log visible text on page for debugging."""
        try:
            # Get all visible text (truncated)
            text = await page.evaluate(
                "() => document.body ? document.body.innerText.substring(0, 2000) : '(empty)'"
            )
            logger.info("Page text (first 2000 chars):\n%s", text)

            # Count interactive elements
            counts = await page.evaluate("""() => ({
                buttons: document.querySelectorAll('button').length,
                inputs: document.querySelectorAll('input').length,
                links: document.querySelectorAll('a').length,
                iframes: document.querySelectorAll('iframe').length,
            })""")
            logger.info("Page elements: %s", counts)
        except Exception as e:
            logger.warning("Could not read page state: %s", e)

    async def _fill_name_input(self, page: Page, name: str) -> bool:
        """Try to fill name input on the main page."""
        # Strategy 1: labeled inputs
        selectors = [
            'input[placeholder*="name" i]',
            'input[placeholder*="Name" i]',
            'input[aria-label*="name" i]',
            'input[aria-label*="Name" i]',
            'input[id*="name" i]',
            'input[data-test*="name" i]',
            '#guest-name',
        ]
        for sel in selectors:
            try:
                el = await page.wait_for_selector(sel, timeout=3000)
                if el and await el.is_visible():
                    await el.click()
                    await el.fill("")
                    await el.type(name, delay=random.randint(50, 120))
                    logger.info("Filled name input via: %s", sel)
                    return True
            except Exception:
                continue

        # Strategy 2: find any visible text input
        try:
            inputs = await page.query_selector_all('input[type="text"], input:not([type])')
            for inp in inputs:
                if await inp.is_visible():
                    placeholder = await inp.get_attribute("placeholder") or ""
                    aria = await inp.get_attribute("aria-label") or ""
                    logger.info("Found visible input: placeholder=%s, aria=%s", placeholder, aria)
                    await inp.click()
                    await inp.fill("")
                    await inp.type(name, delay=random.randint(50, 120))
                    logger.info("Filled first visible text input with name")
                    return True
        except Exception:
            pass

        return False

    async def _fill_name_input_in_frame(self, frame, name: str) -> bool:
        """Try to fill name input inside an iframe."""
        try:
            inputs = await frame.query_selector_all('input[type="text"], input:not([type])')
            for inp in inputs:
                if await inp.is_visible():
                    await inp.click()
                    await inp.fill("")
                    await inp.type(name, delay=random.randint(50, 120))
                    logger.info("Filled name input in iframe")
                    return True
        except Exception:
            pass
        return False

    async def _find_and_click(
        self, page: Page, texts: list[str], description: str
    ) -> bool:
        """Find and click an element matching any of the given text patterns."""
        for text in texts:
            # Try button
            for tag in ["button", "a", '[role="button"]']:
                selector = f'{tag}:has-text("{text}")'
                try:
                    el = await page.wait_for_selector(selector, timeout=3000)
                    if el and await el.is_visible():
                        await el.click()
                        logger.info("Clicked %s: %s >> %s", description, tag, text)
                        return True
                except Exception:
                    continue

        # Fallback: search by text content with locator API
        for text in texts:
            try:
                locator = page.get_by_text(text, exact=False)
                if await locator.count() > 0:
                    first = locator.first
                    if await first.is_visible():
                        await first.click()
                        logger.info("Clicked %s via get_by_text: %s", description, text)
                        return True
            except Exception:
                continue

        return False

    async def _find_and_click_in_frame(
        self, frame, texts: list[str], description: str
    ) -> bool:
        """Find and click inside an iframe."""
        for text in texts:
            for tag in ["button", "a", '[role="button"]']:
                selector = f'{tag}:has-text("{text}")'
                try:
                    el = await frame.wait_for_selector(selector, timeout=2000)
                    if el and await el.is_visible():
                        await el.click()
                        logger.info("Clicked %s in iframe: %s", description, text)
                        return True
                except Exception:
                    continue
        return False

    async def _click_primary_button(self, page: Page) -> bool:
        """Last resort: click any prominent/primary action button."""
        try:
            buttons = await page.query_selector_all("button")
            for btn in buttons:
                if not await btn.is_visible():
                    continue
                text = (await btn.inner_text()).strip().lower()
                classes = await btn.get_attribute("class") or ""
                # Look for primary/action buttons
                if any(kw in text for kw in ["join", "start", "connect", "enter"]):
                    await btn.click()
                    logger.info("Clicked primary button: '%s'", text)
                    return True
                if any(kw in classes.lower() for kw in ["primary", "action", "cta", "btn-join"]):
                    await btn.click()
                    logger.info("Clicked button with primary class: '%s'", text)
                    return True
        except Exception as e:
            logger.warning("Primary button search failed: %s", e)
        return False

    async def _try_mute(self, page: Page) -> None:
        """Best-effort mute of mic and camera."""
        mute_texts = ["Mute", "mute", "Stop video", "Turn off camera"]
        for text in mute_texts:
            try:
                el = await page.wait_for_selector(
                    f'button[aria-label*="{text}" i]', timeout=2000
                )
                if el and await el.is_visible():
                    await el.click()
            except Exception:
                continue
