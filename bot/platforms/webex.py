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
        await random_delay(8, 12)  # Web client takes time to load

        await self._save_debug(page, "02_after_join_browser")
        logger.info("Page URL after join-from-browser: %s", page.url)

        # Step 3: Dismiss cookie banner again (may reappear on new page)
        await self._dismiss_cookies(page)

        # Step 4: Fill in Name field
        await self._fill_name(page, display_name)

        # Step 5: Fill in Email (optional but may help avoid issues)
        await self._fill_email(page)

        # Step 6: Mute mic and stop video
        await self._mute_av(page)

        await self._save_debug(page, "03_before_join")

        # Step 7: Click "Join meeting" button
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
        """Click 'Join from this browser' on the landing page."""
        selectors = [
            'button:has-text("Join from this browser")',
            'button:has-text("Join from browser")',
            'div:has-text("Join from this browser")',
            'button:has-text("Use web app")',
        ]
        for sel in selectors:
            try:
                el = await page.wait_for_selector(sel, timeout=5000)
                if el and await el.is_visible():
                    await el.click()
                    logger.info("Clicked: %s", sel)
                    return
            except Exception:
                continue

        # Fallback: use get_by_text
        try:
            loc = page.get_by_text("Join from this browser", exact=False).first
            if await loc.is_visible():
                await loc.click()
                logger.info("Clicked 'Join from this browser' via get_by_text")
                return
        except Exception:
            pass

        logger.warning("Could not find 'Join from this browser' — may already be on join page")

    async def _fill_name(self, page: Page, name: str) -> None:
        """Fill in the Name field on the guest join form."""
        # The form has a "Name *" label — try label-based lookup first
        try:
            name_input = page.get_by_label("Name", exact=False).first
            if await name_input.is_visible():
                await name_input.click()
                await name_input.fill("")
                await name_input.type(name, delay=random.randint(50, 120))
                logger.info("Filled Name field via label")
                return
        except Exception:
            pass

        # Fallback: try placeholder/attribute selectors
        selectors = [
            'input[placeholder*="name" i]',
            'input[aria-label*="name" i]',
            'input[id*="name" i]',
            'input[name*="name" i]',
        ]
        for sel in selectors:
            try:
                el = await page.wait_for_selector(sel, timeout=3000)
                if el and await el.is_visible():
                    await el.click()
                    await el.fill("")
                    await el.type(name, delay=random.randint(50, 120))
                    logger.info("Filled Name field via: %s", sel)
                    return
            except Exception:
                continue

        # Last resort: find the first visible text input
        try:
            inputs = await page.query_selector_all('input[type="text"], input:not([type])')
            for inp in inputs:
                if await inp.is_visible():
                    await inp.click()
                    await inp.fill(name)
                    logger.info("Filled first visible text input as Name")
                    return
        except Exception:
            pass

        logger.warning("Could not find Name input field")

    async def _fill_email(self, page: Page) -> None:
        """Fill in Email field with a disposable address (optional)."""
        try:
            email_input = page.get_by_label("Email", exact=False).first
            if await email_input.is_visible():
                await email_input.click()
                await email_input.fill("")
                await email_input.type(
                    "meetscribe@example.com", delay=random.randint(50, 100)
                )
                logger.info("Filled Email field")
                return
        except Exception:
            pass

        # Fallback
        selectors = [
            'input[placeholder*="email" i]',
            'input[type="email"]',
            'input[aria-label*="email" i]',
        ]
        for sel in selectors:
            try:
                el = await page.wait_for_selector(sel, timeout=2000)
                if el and await el.is_visible():
                    await el.click()
                    await el.fill("meetscribe@example.com")
                    logger.info("Filled Email field via: %s", sel)
                    return
            except Exception:
                continue

    async def _mute_av(self, page: Page) -> None:
        """Click Mute and Stop Video buttons."""
        for label in ["Mute", "Stop video"]:
            try:
                btn = page.get_by_text(label, exact=False).first
                if await btn.is_visible():
                    await btn.click()
                    logger.info("Clicked '%s'", label)
                    await random_delay(0.5, 1)
            except Exception:
                continue

        # Also try aria-label versions
        for aria in ["Mute", "Stop video", "Turn off camera"]:
            try:
                btn = await page.query_selector(f'button[aria-label*="{aria}" i]')
                if btn and await btn.is_visible():
                    await btn.click()
            except Exception:
                continue

    async def _click_join_meeting(self, page: Page) -> bool:
        """Click the 'Join meeting' button on the guest join form."""
        # Exact match first
        selectors = [
            'button:has-text("Join meeting")',
            'button:has-text("Join Meeting")',
            'input[value*="Join" i]',
            'button:has-text("Join")',
        ]
        for sel in selectors:
            try:
                el = await page.wait_for_selector(sel, timeout=5000)
                if el and await el.is_visible():
                    await el.click()
                    logger.info("Clicked join via: %s", sel)
                    return True
            except Exception:
                continue

        # get_by_role
        try:
            btn = page.get_by_role("button", name="Join meeting")
            if await btn.count() > 0 and await btn.first.is_visible():
                await btn.first.click()
                logger.info("Clicked 'Join meeting' via get_by_role")
                return True
        except Exception:
            pass

        # Broad text match
        try:
            loc = page.get_by_text("Join meeting", exact=False).first
            if await loc.is_visible():
                await loc.click()
                logger.info("Clicked 'Join meeting' via get_by_text")
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
