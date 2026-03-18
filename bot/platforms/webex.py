"""Webex meeting joiner — guest join via public web client.

Flow observed from debug screenshots:
1. Landing page: "Join your Webex meeting" with "Join from this browser" button
2. Cookie consent banner (Accept/Reject)
3. Guest join page: Name*, Email Address fields, Preview, Mute/Stop video, "Join meeting" button
"""

from __future__ import annotations

import logging
import random
import re

from playwright.async_api import Page

from bot.platforms.base import PlatformJoiner
from bot.stealth import random_delay

logger = logging.getLogger(__name__)


class WebexJoiner(PlatformJoiner):
    """Join Webex meetings as a guest via the web client."""

    def parse_meeting_url(self, meeting_input: str) -> str:
        """Convert meeting input to a joinable Webex URL."""
        meeting_input = meeting_input.strip()

        if re.match(r"https?://.*\.webex\.com/", meeting_input):
            return meeting_input

        cleaned = re.sub(r"[\s\-]", "", meeting_input)
        if cleaned.isdigit() and len(cleaned) >= 9:
            return f"https://web.webex.com/meet?meetingId={cleaned}"

        if "." in meeting_input and "/" not in meeting_input:
            return f"https://{meeting_input}"

        return meeting_input

    async def join(self, page: Page, meeting_url: str, display_name: str) -> bool:
        """Join a Webex meeting as a guest."""
        url = self.parse_meeting_url(meeting_url)
        logger.info("Navigating to Webex: %s", url)

        await page.goto(url, wait_until="load", timeout=90000)
        await random_delay(5, 8)

        # Debug state
        logger.info("Page title: %s | URL: %s", await page.title(), page.url)
        await self._save_debug(page, "01_initial_load")

        # Step 1: Dismiss cookie banner
        await self._dismiss_cookies(page)

        # Step 2: Click "Join from this browser" on the landing page
        await self._click_join_from_browser(page)

        # The web client (guest join form) takes significant time to load
        logger.info("Waiting for web client to load after modal click...")
        await random_delay(12, 18)

        await self._save_debug(page, "02_after_modal_click")
        logger.info("Page title: %s | URL: %s", await page.title(), page.url)

        # The form is in a cross-origin iframe (web.webex.com).
        # JS can't cross origins, but Playwright's frame API can.
        # Wait for the iframe content to fully load, then interact.

        # Step 3: Find the guest join iframe and wait for it to load
        guest_frame = await self._wait_for_guest_frame(page)

        if not guest_frame:
            logger.error("Guest join iframe never loaded")
            await self._save_debug(page, "03_no_iframe")
            return False

        await self._save_debug(page, "03_iframe_found")

        # Step 4: Dismiss cookie banner again
        await self._dismiss_cookies(page)

        # Step 5: Fill Name field inside iframe
        await self._fill_input_in_frame(guest_frame, "name", display_name)

        # Step 6: Fill Email field inside iframe
        await self._fill_input_in_frame(guest_frame, "email", "meetscribe@example.com")

        # Step 7: Mute mic and stop video (may be in main page or iframe)
        await self._mute_av_in_frame(guest_frame)
        await self._mute_av_in_frame(page)

        await self._save_debug(page, "04_before_join")

        # Step 8: Click "Join meeting" button inside iframe
        joined = await self._click_join_in_frame(guest_frame)
        if not joined:
            # Fallback: try main page
            joined = await self._click_join_in_frame(page)
        if not joined:
            logger.error("Could not click 'Join meeting' button")
            await self._save_debug(page, "05_join_failed")
            return False

        logger.info("Clicked 'Join meeting', waiting to enter...")
        await random_delay(10, 15)

        # Step 8: Wait to be admitted / meeting to start
        for attempt in range(30):
            if await self.is_in_meeting(page):
                logger.info("Successfully joined Webex meeting!")
                return True
            if attempt % 5 == 0:
                logger.info("Waiting for meeting admission... (attempt %d/30)", attempt + 1)
                await self._save_debug(page, f"05_waiting_{attempt}")
            await random_delay(8, 12)

        logger.error("Timed out waiting to join meeting")
        return False

    async def is_in_meeting(self, page: Page) -> bool:
        """Check if currently in an active Webex meeting."""
        indicators = [
            'button:has-text("Leave")',
            '[aria-label*="Leave" i]',
            '[aria-label*="participant" i]',
            '[data-test="participant-list"]',
            '[data-test*="meeting"]',
            '.meeting-controls',
        ]
        for selector in indicators:
            try:
                el = await page.query_selector(selector)
                if el and await el.is_visible():
                    return True
            except Exception:
                continue

        # Check page text for meeting indicators
        try:
            body_text = await page.evaluate(
                "() => document.body ? document.body.innerText : ''"
            )
            if any(kw in body_text for kw in ["Recording", "Participants", "Leave meeting"]):
                return True
        except Exception:
            pass

        return False

    async def leave_meeting(self, page: Page) -> None:
        """Leave the Webex meeting."""
        logger.info("Leaving Webex meeting")
        for text in ["Leave meeting", "Leave"]:
            try:
                btn = page.get_by_text(text, exact=False).first
                if await btn.is_visible():
                    await btn.click()
                    await random_delay(2, 3)
                    return
            except Exception:
                continue

    # ── Private helpers ───────────────────────────────────────────────

    async def _wait_for_guest_frame(self, page: Page):
        """Wait for the web.webex.com iframe to load with form elements.

        The form is in a cross-origin iframe. Playwright can access it but
        it takes time to load. We poll for up to 30 seconds.
        """
        for attempt in range(15):
            for frame in page.frames:
                url = frame.url
                if "web.webex.com" in url:
                    try:
                        # Wait for at least one visible input
                        el = await frame.wait_for_selector(
                            'input', timeout=3000
                        )
                        if el:
                            # Log what's in the frame
                            text = await frame.evaluate(
                                "() => document.body ? document.body.innerText.substring(0, 1000) : ''"
                            )
                            logger.info("Guest frame loaded: %s", url[:80])
                            logger.info("Guest frame text: %s", text[:500])
                            return frame
                    except Exception:
                        pass

            logger.info("Waiting for guest iframe to load... (attempt %d/15)", attempt + 1)
            await random_delay(2, 3)

        # Log all frames for debugging
        for frame in page.frames:
            logger.info("Available frame: %s", frame.url[:120])

        return None

    async def _dismiss_cookies(self, page: Page) -> None:
        """Dismiss cookie consent banner if present."""
        for text in ["Accept", "Reject", "Accept All"]:
            try:
                btn = page.get_by_role("button", name=text)
                if await btn.count() > 0 and await btn.first.is_visible():
                    await btn.first.click()
                    logger.info("Dismissed cookie banner: clicked '%s'", text)
                    await random_delay(1, 2)
                    return
            except Exception:
                continue

    async def _click_join_from_browser(self, page: Page) -> None:
        """Click 'Join from this browser' on the landing page.

        Observed flow:
        1. Click the "Join from this browser" card on the landing page
        2. A modal dialog appears: "Let's make sure you're on time for your meeting"
           with a black "Join from browser" button
        3. Click that button to proceed to the guest join form
        """
        # Step A: Click the "Join from this browser" card
        card_selectors = [
            'button:has-text("Join from this browser")',
            'div:has-text("Join from this browser")',
        ]
        for sel in card_selectors:
            try:
                el = await page.wait_for_selector(sel, timeout=5000)
                if el and await el.is_visible():
                    await el.click()
                    logger.info("Clicked card: %s", sel)
                    break
            except Exception:
                continue

        await random_delay(2, 4)

        # Step B: A modal appears with "Join from browser" button — click it
        logger.info("Looking for 'Join from browser' modal button...")
        modal_selectors = [
            'button:has-text("Join from browser")',
            'button:text-is("Join from browser")',
            'a:has-text("Join from browser")',
        ]
        for sel in modal_selectors:
            try:
                el = await page.wait_for_selector(sel, timeout=8000)
                if el and await el.is_visible():
                    await el.click()
                    logger.info("Clicked modal button: %s", sel)
                    return
            except Exception:
                continue

        # Fallback: use get_by_role for the modal button
        try:
            btn = page.get_by_role("button", name="Join from browser")
            if await btn.count() > 0 and await btn.first.is_visible():
                await btn.first.click()
                logger.info("Clicked 'Join from browser' via get_by_role")
                return
        except Exception:
            pass

        # Last fallback: click any button with "Join" and "browser" text
        try:
            buttons = await page.query_selector_all("button")
            for btn in buttons:
                text = (await btn.inner_text()).strip().lower()
                if "join" in text and "browser" in text and await btn.is_visible():
                    await btn.click()
                    logger.info("Clicked modal join button by scanning: '%s'", text)
                    return
        except Exception:
            pass

        logger.warning("Could not find 'Join from browser' modal button")

    async def _fill_input_in_frame(self, frame, keyword: str, value: str) -> bool:
        """Fill an input field in a frame by matching keyword in attributes."""
        try:
            inputs = await frame.query_selector_all("input")
            for inp in inputs:
                inp_type = await inp.get_attribute("type") or ""
                inp_name = await inp.get_attribute("name") or ""
                inp_id = await inp.get_attribute("id") or ""
                inp_ph = await inp.get_attribute("placeholder") or ""
                inp_aria = await inp.get_attribute("aria-label") or ""

                attrs = f"{inp_type} {inp_name} {inp_id} {inp_ph} {inp_aria}".lower()

                if keyword.lower() in attrs and await inp.is_visible():
                    await inp.click()
                    await inp.fill("")
                    await inp.type(value, delay=random.randint(50, 120))
                    logger.info("Filled '%s' input: name=%s id=%s ph=%s", keyword, inp_name, inp_id, inp_ph)
                    return True

            # Fallback for "name": first visible text-like input
            if keyword == "name":
                for inp in inputs:
                    inp_type = await inp.get_attribute("type") or "text"
                    if inp_type in ("text", "") and await inp.is_visible():
                        await inp.click()
                        await inp.fill(value)
                        logger.info("Filled first visible text input as '%s'", keyword)
                        return True

        except Exception as e:
            logger.warning("Error filling %s: %s", keyword, e)

        logger.warning("Could not find '%s' input in frame", keyword)
        return False

    async def _mute_av_in_frame(self, frame) -> None:
        """Mute mic/camera in a frame."""
        for text in ["Mute", "Stop video"]:
            try:
                btn = await frame.query_selector(f'button:has-text("{text}")')
                if btn and await btn.is_visible():
                    await btn.click()
                    logger.info("Clicked '%s'", text)
            except Exception:
                continue

        for aria in ["Mute", "Stop video", "mute", "Turn off camera"]:
            try:
                btn = await frame.query_selector(f'button[aria-label*="{aria}" i]')
                if btn and await btn.is_visible():
                    await btn.click()
            except Exception:
                continue

    async def _click_join_in_frame(self, frame) -> bool:
        """Click the Join meeting button in a frame."""
        # Try exact selectors
        for sel in [
            'button:has-text("Join meeting")',
            'button:has-text("Join Meeting")',
            'button:has-text("Join")',
        ]:
            try:
                el = await frame.wait_for_selector(sel, timeout=5000)
                if el and await el.is_visible():
                    await el.click()
                    logger.info("Clicked join via: %s", sel)
                    return True
            except Exception:
                continue

        # Scan all buttons
        try:
            buttons = await frame.query_selector_all("button")
            for btn in buttons:
                text = (await btn.inner_text()).strip().lower()
                if "join" in text and "browser" not in text and await btn.is_visible():
                    await btn.click()
                    logger.info("Clicked join button by scan: '%s'", text)
                    return True
        except Exception:
            pass

        return False

    async def _save_debug(self, page: Page, label: str) -> None:
        """Save a debug screenshot."""
        try:
            path = f"recordings/debug_webex_{label}.png"
            await page.screenshot(path=path, full_page=True)
            logger.info("Debug screenshot: %s", path)
        except Exception as e:
            logger.warning("Could not save debug screenshot %s: %s", label, e)
