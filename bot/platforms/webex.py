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

        # The Webex web client uses Shadow DOM / Web Components.
        # Standard selectors can't see inside shadow roots.
        # We use JavaScript to pierce the shadow DOM and interact with elements.

        # Step 3: Dismiss cookie banner again (may reappear)
        await self._dismiss_cookies(page)

        # Step 4: Fill Name field (pierce shadow DOM if needed)
        name_filled = await self._fill_name_js(page, display_name)

        # Step 5: Fill Email field
        await self._fill_email_js(page)

        # Step 6: Mute mic and stop video
        await self._mute_av_js(page)

        await self._save_debug(page, "03_before_join")

        # Step 7: Click "Join meeting" button
        joined = await self._click_join_meeting_js(page)
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

    async def _fill_name_js(self, page: Page, name: str) -> bool:
        """Fill Name field using JS that pierces shadow DOM and iframes."""
        result = await page.evaluate("""(name) => {
            // Helper: recursively find inputs across shadow DOM and iframes
            function findInputs(root) {
                let inputs = [];
                // Direct inputs
                inputs.push(...root.querySelectorAll('input'));
                // Shadow DOM
                root.querySelectorAll('*').forEach(el => {
                    if (el.shadowRoot) {
                        inputs.push(...findInputs(el.shadowRoot));
                    }
                });
                return inputs;
            }

            // Search main document
            let allInputs = findInputs(document);

            // Also search all iframes
            document.querySelectorAll('iframe').forEach(iframe => {
                try {
                    let iframeDoc = iframe.contentDocument || iframe.contentWindow.document;
                    if (iframeDoc) {
                        allInputs.push(...findInputs(iframeDoc));
                    }
                } catch(e) {} // cross-origin will fail
            });

            // Log what we found
            let info = allInputs.map(inp => ({
                type: inp.type,
                name: inp.name,
                id: inp.id,
                placeholder: inp.placeholder,
                ariaLabel: inp.getAttribute('aria-label'),
                visible: inp.offsetParent !== null
            }));
            console.log('Found inputs:', JSON.stringify(info));

            // Find the name input
            for (let inp of allInputs) {
                let attrs = (inp.name + inp.id + inp.placeholder + (inp.getAttribute('aria-label') || '')).toLowerCase();
                if (attrs.includes('name') && !attrs.includes('email') && inp.offsetParent !== null) {
                    inp.focus();
                    inp.value = '';
                    // Use native input setter to trigger React state
                    let nativeInputValueSetter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
                    nativeInputValueSetter.call(inp, name);
                    inp.dispatchEvent(new Event('input', { bubbles: true }));
                    inp.dispatchEvent(new Event('change', { bubbles: true }));
                    return { filled: true, info: info };
                }
            }

            // Fallback: first visible text input
            for (let inp of allInputs) {
                if ((inp.type === 'text' || inp.type === '') && inp.offsetParent !== null) {
                    inp.focus();
                    let nativeInputValueSetter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
                    nativeInputValueSetter.call(inp, name);
                    inp.dispatchEvent(new Event('input', { bubbles: true }));
                    inp.dispatchEvent(new Event('change', { bubbles: true }));
                    return { filled: true, fallback: true, info: info };
                }
            }

            return { filled: false, info: info };
        }""", name)

        logger.info("Name fill result: %s", result)
        if result and result.get("filled"):
            logger.info("Filled Name field via JS")
            return True
        logger.warning("Could not find Name input via JS")
        return False

    async def _fill_email_js(self, page: Page) -> None:
        """Fill Email field using JS that pierces shadow DOM."""
        await page.evaluate("""(email) => {
            function findInputs(root) {
                let inputs = [];
                inputs.push(...root.querySelectorAll('input'));
                root.querySelectorAll('*').forEach(el => {
                    if (el.shadowRoot) inputs.push(...findInputs(el.shadowRoot));
                });
                return inputs;
            }

            let allInputs = findInputs(document);
            document.querySelectorAll('iframe').forEach(iframe => {
                try {
                    let doc = iframe.contentDocument || iframe.contentWindow.document;
                    if (doc) allInputs.push(...findInputs(doc));
                } catch(e) {}
            });

            for (let inp of allInputs) {
                let attrs = (inp.type + inp.name + inp.id + inp.placeholder + (inp.getAttribute('aria-label') || '')).toLowerCase();
                if (attrs.includes('email') && inp.offsetParent !== null) {
                    inp.focus();
                    let setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
                    setter.call(inp, email);
                    inp.dispatchEvent(new Event('input', { bubbles: true }));
                    inp.dispatchEvent(new Event('change', { bubbles: true }));
                    return true;
                }
            }
            return false;
        }""", "meetscribe@example.com")
        logger.info("Attempted to fill Email field via JS")

    async def _mute_av_js(self, page: Page) -> None:
        """Mute mic and camera using JS that pierces shadow DOM."""
        await page.evaluate("""() => {
            function findButtons(root) {
                let buttons = [];
                buttons.push(...root.querySelectorAll('button'));
                root.querySelectorAll('*').forEach(el => {
                    if (el.shadowRoot) buttons.push(...findButtons(el.shadowRoot));
                });
                return buttons;
            }

            let allButtons = findButtons(document);
            document.querySelectorAll('iframe').forEach(iframe => {
                try {
                    let doc = iframe.contentDocument || iframe.contentWindow.document;
                    if (doc) allButtons.push(...findButtons(doc));
                } catch(e) {}
            });

            for (let btn of allButtons) {
                let text = (btn.textContent + (btn.getAttribute('aria-label') || '')).toLowerCase();
                if ((text.includes('mute') || text.includes('stop video')) && btn.offsetParent !== null) {
                    btn.click();
                }
            }
        }""")
        logger.info("Attempted to mute AV via JS")

    async def _click_join_meeting_js(self, page: Page) -> bool:
        """Click 'Join meeting' button using JS that pierces shadow DOM."""
        result = await page.evaluate("""() => {
            function findButtons(root) {
                let buttons = [];
                buttons.push(...root.querySelectorAll('button, [role="button"], input[type="submit"]'));
                root.querySelectorAll('*').forEach(el => {
                    if (el.shadowRoot) buttons.push(...findButtons(el.shadowRoot));
                });
                return buttons;
            }

            let allButtons = findButtons(document);
            document.querySelectorAll('iframe').forEach(iframe => {
                try {
                    let doc = iframe.contentDocument || iframe.contentWindow.document;
                    if (doc) allButtons.push(...findButtons(doc));
                } catch(e) {}
            });

            // Log all visible buttons
            let info = allButtons.filter(b => b.offsetParent !== null).map(b => ({
                text: b.textContent.trim().substring(0, 50),
                ariaLabel: b.getAttribute('aria-label'),
                tag: b.tagName
            }));
            console.log('Visible buttons:', JSON.stringify(info));

            // Find "Join meeting" button
            for (let btn of allButtons) {
                let text = (btn.textContent || '').trim().toLowerCase();
                let aria = (btn.getAttribute('aria-label') || '').toLowerCase();
                if ((text === 'join meeting' || aria === 'join meeting') && btn.offsetParent !== null) {
                    btn.click();
                    return { clicked: true, text: text, info: info };
                }
            }

            // Fallback: any button containing "join" (but not "join from browser")
            for (let btn of allButtons) {
                let text = (btn.textContent || '').trim().toLowerCase();
                if (text.includes('join') && !text.includes('browser') && !text.includes('app') && btn.offsetParent !== null) {
                    btn.click();
                    return { clicked: true, text: text, fallback: true, info: info };
                }
            }

            return { clicked: false, info: info };
        }""")

        logger.info("Join meeting JS result: %s", result)
        if result and result.get("clicked"):
            logger.info("Clicked 'Join meeting' via JS")
            return True
        return False

    async def _save_debug(self, page: Page, label: str) -> None:
        """Save a debug screenshot."""
        try:
            path = f"recordings/debug_webex_{label}.png"
            await page.screenshot(path=path, full_page=True)
            logger.info("Debug screenshot: %s", path)
        except Exception as e:
            logger.warning("Could not save debug screenshot %s: %s", label, e)
