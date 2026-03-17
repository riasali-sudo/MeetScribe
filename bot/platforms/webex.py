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

        # The guest join form is inside an iframe (web.webex.com).
        # Find the right frame to interact with.
        target_frame = await self._find_guest_join_frame(page)

        if target_frame:
            logger.info("Found guest join iframe — interacting inside frame")
        else:
            logger.warning("No guest iframe found, falling back to main page")
            target_frame = page

        # Log frame state
        try:
            text = await target_frame.evaluate(
                "() => document.body ? document.body.innerText.substring(0, 3000) : '(empty)'"
            )
            logger.info("Frame text:\n%s", text)
        except Exception as e:
            logger.warning("Could not read frame state: %s", e)

        # Step 3: Dismiss cookie banner again (may reappear)
        await self._dismiss_cookies(page)

        # Step 4: Fill in Name field (inside iframe)
        await self._fill_name(target_frame, display_name)

        # Step 5: Fill in Email (inside iframe)
        await self._fill_email(target_frame)

        # Step 6: Mute mic and stop video (may be in main page or iframe)
        await self._mute_av(page)
        await self._mute_av(target_frame)

        await self._save_debug(page, "03_before_join")

        # Step 7: Click "Join meeting" button (inside iframe)
        joined = await self._click_join_meeting(target_frame)
        if not joined:
            # Also try main page
            joined = await self._click_join_meeting(page)
        if not joined:
            logger.error("Could not click 'Join meeting' button")
            await self._save_debug(page, "04_join_failed")
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

    async def _find_guest_join_frame(self, page: Page):
        """Find the iframe containing the guest join form.

        The Webex guest join form lives inside an iframe from web.webex.com.
        Returns the Frame object if found, None otherwise.
        """
        for frame in page.frames:
            url = frame.url
            logger.info("Frame URL: %s", url[:120])

            # The guest join form iframe
            if "web.webex.com" in url or "guest" in url.lower():
                # Verify it has inputs (the form)
                try:
                    input_count = await frame.evaluate(
                        "() => document.querySelectorAll('input').length"
                    )
                    logger.info("Frame %s has %d inputs", url[:60], input_count)
                    if input_count > 0:
                        return frame
                except Exception:
                    continue

        # Fallback: find any frame with visible input elements
        for frame in page.frames:
            if frame == page.main_frame:
                continue
            try:
                has_inputs = await frame.evaluate(
                    "() => document.querySelectorAll('input[type=\"text\"], input[type=\"email\"], input:not([type])').length > 0"
                )
                if has_inputs:
                    logger.info("Found frame with inputs: %s", frame.url[:80])
                    return frame
            except Exception:
                continue

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

    async def _fill_name(self, target, name: str) -> None:
        """Fill in the Name field. target can be Page or Frame."""
        # Try attribute-based selectors (work on both Page and Frame)
        selectors = [
            'input[placeholder*="name" i]',
            'input[aria-label*="name" i]',
            'input[id*="name" i]',
            'input[name*="name" i]',
        ]
        for sel in selectors:
            try:
                el = await target.wait_for_selector(sel, timeout=3000)
                if el and await el.is_visible():
                    await el.click()
                    await el.fill("")
                    await el.type(name, delay=random.randint(50, 120))
                    logger.info("Filled Name field via: %s", sel)
                    return
            except Exception:
                continue

        # Last resort: first visible text input in the frame
        try:
            inputs = await target.query_selector_all(
                'input[type="text"], input:not([type])'
            )
            for inp in inputs:
                if await inp.is_visible():
                    await inp.click()
                    await inp.fill(name)
                    logger.info("Filled first visible text input as Name")
                    return
        except Exception:
            pass

        logger.warning("Could not find Name input field")

    async def _fill_email(self, target) -> None:
        """Fill in Email field. target can be Page or Frame."""
        selectors = [
            'input[type="email"]',
            'input[placeholder*="email" i]',
            'input[aria-label*="email" i]',
            'input[id*="email" i]',
            'input[name*="email" i]',
        ]
        for sel in selectors:
            try:
                el = await target.wait_for_selector(sel, timeout=2000)
                if el and await el.is_visible():
                    await el.click()
                    await el.fill("meetscribe@example.com")
                    logger.info("Filled Email field via: %s", sel)
                    return
            except Exception:
                continue

    async def _mute_av(self, target) -> None:
        """Click Mute and Stop Video buttons. target can be Page or Frame."""
        for aria in ["Mute", "Stop video", "Turn off camera", "mute"]:
            try:
                btn = await target.query_selector(f'button[aria-label*="{aria}" i]')
                if btn and await btn.is_visible():
                    await btn.click()
                    logger.info("Clicked AV button: %s", aria)
            except Exception:
                continue

        # Also try text-based selectors
        for sel in [
            'button:has-text("Mute")',
            'button:has-text("Stop video")',
        ]:
            try:
                el = await target.query_selector(sel)
                if el and await el.is_visible():
                    await el.click()
            except Exception:
                continue

    async def _click_join_meeting(self, target) -> bool:
        """Click 'Join meeting' button. target can be Page or Frame."""
        selectors = [
            'button:has-text("Join meeting")',
            'button:has-text("Join Meeting")',
            'button:has-text("Join")',
            'input[value*="Join" i]',
            '[role="button"]:has-text("Join")',
        ]
        for sel in selectors:
            try:
                el = await target.wait_for_selector(sel, timeout=5000)
                if el and await el.is_visible():
                    await el.click()
                    logger.info("Clicked join via: %s", sel)
                    return True
            except Exception:
                continue

        # Scan all buttons
        try:
            buttons = await target.query_selector_all("button")
            for btn in buttons:
                text = (await btn.inner_text()).strip().lower()
                if "join" in text and await btn.is_visible():
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
