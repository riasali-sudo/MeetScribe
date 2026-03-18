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

        # Step 5: Fill Name and Email fields inside iframe
        # The form has two visible text inputs: Name (1st) and Email (2nd)
        await self._fill_guest_form(guest_frame, display_name, "meetscribe@example.com")

        # Step 7: Mute mic and stop video (may be in main page or iframe)
        await self._mute_av_in_frame(guest_frame)
        await self._mute_av_in_frame(page)

        await self._save_debug(page, "04_before_join")

        # Debug: log all frames
        for i, frame in enumerate(page.frames):
            logger.info("Frame[%d]: url=%s", i, frame.url[:120])

        # Step 8: Click "Join meeting" button — search ALL frames and main page
        joined = await self._click_join_button_anywhere(page, guest_frame)
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

    async def _fill_guest_form(self, frame, name: str, email: str) -> None:
        """Fill the Name and Email fields in the guest join form.

        The Webex guest form has two visible inputs in order:
        1. Name (required)
        2. Email Address (required)
        We find all visible inputs and fill them positionally.
        """
        try:
            # Log all inputs for debugging
            all_inputs = await frame.query_selector_all("input")
            visible_inputs = []
            for inp in all_inputs:
                is_visible = await inp.is_visible()
                inp_type = await inp.get_attribute("type") or ""
                inp_name = await inp.get_attribute("name") or ""
                inp_id = await inp.get_attribute("id") or ""
                inp_ph = await inp.get_attribute("placeholder") or ""
                inp_aria = await inp.get_attribute("aria-label") or ""
                logger.info(
                    "Input: type=%s name=%s id=%s placeholder=%s aria=%s visible=%s",
                    inp_type, inp_name, inp_id, inp_ph, inp_aria, is_visible,
                )
                if is_visible and inp_type not in ("hidden", "checkbox", "radio", "submit"):
                    visible_inputs.append(inp)

            logger.info("Found %d visible inputs in guest frame", len(visible_inputs))

            # Fill Name (first visible input)
            if len(visible_inputs) >= 1:
                await visible_inputs[0].click()
                await visible_inputs[0].fill("")
                await visible_inputs[0].type(name, delay=random.randint(50, 120))
                logger.info("Filled Name field (1st visible input)")

            # Fill Email (second visible input)
            if len(visible_inputs) >= 2:
                await visible_inputs[1].click()
                await visible_inputs[1].fill("")
                await visible_inputs[1].type(email, delay=random.randint(50, 120))
                logger.info("Filled Email field (2nd visible input)")
            else:
                logger.warning("Only %d visible inputs found, expected at least 2", len(visible_inputs))

        except Exception as e:
            logger.warning("Error filling guest form: %s", e)

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

    async def _click_join_button_anywhere(self, page: Page, guest_frame) -> bool:
        """Search ALL frames and the main page for the 'Join meeting' button.

        The Webex UI sometimes places the button in the main page even though
        the form inputs are inside a cross-origin iframe.
        """
        await random_delay(2, 3)  # Let form validation settle

        # Try each frame context: guest iframe first, then main page, then all frames
        contexts = [("guest_frame", guest_frame), ("main_page", page)]
        for frame in page.frames:
            if frame != page.main_frame and frame != guest_frame:
                contexts.append((f"frame:{frame.url[:60]}", frame))

        for ctx_name, ctx in contexts:
            logger.info("Searching for join button in: %s", ctx_name)
            result = await self._click_join_in_context(ctx, ctx_name)
            if result:
                return True

        return False

    async def _click_join_in_context(self, frame, ctx_name: str) -> bool:
        """Try to click the Join meeting button in a single frame/page context."""
        # Log all clickable elements for debugging
        try:
            for tag in ["button", "a", "[role='button']"]:
                elements = await frame.query_selector_all(tag)
                for el in elements:
                    text = (await el.inner_text()).strip()
                    is_vis = await el.is_visible()
                    if text and ("join" in text.lower() or is_vis):
                        disabled = await el.get_attribute("disabled")
                        tag_name = await el.evaluate("e => e.tagName")
                        logger.info(
                            "[%s] Element: tag=%s text='%s' disabled=%s visible=%s",
                            ctx_name, tag_name, text[:60], disabled, is_vis,
                        )
        except Exception:
            pass

        # Strategy 1: CSS selectors for button-like elements
        for sel in [
            'button:has-text("Join meeting")',
            'button:has-text("Join Meeting")',
            'a:has-text("Join meeting")',
            '[role="button"]:has-text("Join meeting")',
            'button:has-text("Join")',
            'a:has-text("Join")',
        ]:
            try:
                el = await frame.wait_for_selector(sel, timeout=3000)
                if el and await el.is_visible():
                    try:
                        await el.click(timeout=3000)
                        logger.info("[%s] Clicked join via: %s", ctx_name, sel)
                        return True
                    except Exception:
                        await el.click(force=True)
                        logger.info("[%s] Force-clicked join via: %s", ctx_name, sel)
                        return True
            except Exception:
                continue

        # Strategy 2: get_by_role (Playwright's recommended approach)
        try:
            btn = frame.get_by_role("button", name="Join meeting")
            if await btn.count() > 0:
                first = btn.first
                if await first.is_visible():
                    await first.click()
                    logger.info("[%s] Clicked via get_by_role('button', 'Join meeting')", ctx_name)
                    return True
                else:
                    await first.click(force=True)
                    logger.info("[%s] Force-clicked via get_by_role", ctx_name)
                    return True
        except Exception:
            pass

        # Strategy 3: get_by_text
        try:
            el = frame.get_by_text("Join meeting", exact=True).first
            if await el.is_visible():
                await el.click()
                logger.info("[%s] Clicked via get_by_text('Join meeting')", ctx_name)
                return True
        except Exception:
            pass

        # Strategy 4: JavaScript click — finds ANY element with matching text
        try:
            clicked = await frame.evaluate("""() => {
                const texts = ['Join meeting', 'Join Meeting'];
                for (const text of texts) {
                    // Check buttons, anchors, and role=button elements
                    const all = document.querySelectorAll('button, a, [role="button"], input[type="submit"]');
                    for (const el of all) {
                        if (el.textContent.trim().includes(text) || el.value === text) {
                            el.click();
                            return text;
                        }
                    }
                    // TreeWalker to find text nodes anywhere
                    const walker = document.createTreeWalker(
                        document.body, NodeFilter.SHOW_TEXT,
                        { acceptNode: n => n.textContent.trim().includes(text) ? NodeFilter.FILTER_ACCEPT : NodeFilter.FILTER_REJECT }
                    );
                    let node = walker.nextNode();
                    while (node) {
                        let target = node.parentElement;
                        if (target) {
                            target.click();
                            return 'text-node:' + text;
                        }
                        node = walker.nextNode();
                    }
                }
                return null;
            }""")
            if clicked:
                logger.info("[%s] JS-clicked join button: %s", ctx_name, clicked)
                return True
        except Exception as e:
            logger.info("[%s] JS click failed: %s", ctx_name, e)

        # Strategy 5: Scan all visible elements with "join" text
        try:
            elements = await frame.query_selector_all("button, a, [role='button'], div[tabindex]")
            for el in elements:
                text = (await el.inner_text()).strip().lower()
                if "join" in text and "browser" not in text and "mobile" not in text:
                    await el.click(force=True)
                    logger.info("[%s] Force-clicked by scan: '%s'", ctx_name, text)
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
