"""CapSolver provider using robust async polling and exponential retry."""

from __future__ import annotations

import base64
import logging
import os
import re
import time
from typing import Any

import httpx

from ..base import CaptchaChallenge, CaptchaSolution, CaptchaSolver
from ..polling import PollingError, poll_until
from ..retry import retry_async

log = logging.getLogger("ticketmaster-bot")

CAPSOLVER_API = "https://api.capsolver.com"

# Common reCAPTCHA sitekey patterns found in page source / data attributes.
_SITEKEY_RE = re.compile(
    r"(?:data-sitekey|sitekey|grecaptcha\.render\([^)]*['\"]sitekey['\"]\s*:\s*['\"])"
    r"[\s\"':=]+['\"]([0-9A-Za-z_-]{20,})['\"]",
    re.IGNORECASE,
)


class CapSolverSolver(CaptchaSolver):
    """Solve captchas via CapSolver API (reCAPTCHA, image, etc.)."""

    name = "capsolver"

    def __init__(
        self,
        api_key: str | None = None,
        timeout_s: float = 120.0,
        poll_interval_s: float = 3.0,
        max_retries: int = 3,
    ) -> None:
        self.api_key = api_key or os.environ.get("CAPSOLVER_API_KEY", "")
        self.timeout_s = timeout_s
        self.poll_interval_s = poll_interval_s
        self.max_retries = max_retries
        if not self.api_key:
            log.warning("CapSolverSolver constructed without CAPSOLVER_API_KEY")

    @classmethod
    def supports(cls) -> set[str]:
        return {"recaptcha", "yii_image"}

    async def _extract_sitekey(self, challenge: CaptchaChallenge) -> tuple[str | None, str | None]:
        """Try to pull websiteKey and websiteURL from the live page."""
        page = challenge.page
        if page is None:
            return None, None

        try:
            url = page.url
        except Exception:
            url = None

        sitekey: str | None = None
        try:
            # 1. data-sitekey attribute (most common)
            el = page.locator("[data-sitekey]").first
            if await el.count() > 0:
                sitekey = await el.get_attribute("data-sitekey")
        except Exception:
            pass

        if not sitekey:
            try:
                # 2. Fall back to scanning page content
                content = await page.content()
                m = _SITEKEY_RE.search(content)
                if m:
                    sitekey = m.group(1)
            except Exception:
                pass

        return sitekey, url

    async def solve(self, challenge: CaptchaChallenge) -> CaptchaSolution | None:
        """Attempt to solve the challenge via CapSolver."""
        if not self.api_key:
            return None

        start = time.perf_counter()

        task_payload: dict[str, Any]

        if challenge.challenge_type == "yii_image" and challenge.screenshot_bytes:
            task_payload = {
                "type": "ImageToTextTask",
                "body": base64.b64encode(challenge.screenshot_bytes).decode("ascii"),
            }
        else:
            # reCAPTCHA path — try to extract sitekey from the page
            sitekey, page_url = await self._extract_sitekey(challenge)
            if not sitekey:
                log.warning("capsolver: could not extract reCAPTCHA sitekey from page")
                return None
            task_payload = {
                "type": "ReCaptchaV2TaskProxyLess",
                "websiteURL": page_url or "https://www.ticketmaster.com",
                "websiteKey": sitekey,
            }
            log.info("capsolver: using sitekey=%s… from %s", sitekey[:8], page_url)

        async def _do_solve() -> dict[str, Any]:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.post(
                    f"{CAPSOLVER_API}/createTask",
                    json={"clientKey": self.api_key, "task": task_payload},
                )
                resp.raise_for_status()
                data = resp.json()
                if data.get("errorId"):
                    raise RuntimeError(data.get("errorDescription", "createTask failed"))
                task_id = data["taskId"]

                async def check() -> dict[str, Any] | None:
                    r = await client.post(
                        f"{CAPSOLVER_API}/getTaskResult",
                        json={"clientKey": self.api_key, "taskId": task_id},
                    )
                    r.raise_for_status()
                    result = r.json()
                    if result.get("status") == "ready":
                        return result
                    if result.get("errorId"):
                        raise RuntimeError(result.get("errorDescription", "getTaskResult error"))
                    return None

                return await poll_until(
                    check,
                    timeout_s=self.timeout_s,
                    interval_s=self.poll_interval_s,
                    description=f"capsolver:{task_id}",
                )

        try:
            final = await retry_async(
                _do_solve,
                max_attempts=self.max_retries,
                base_delay_s=2.0,
                max_delay_s=15.0,
                description="capsolver solve",
            )
        except (PollingError, Exception) as exc:
            log.warning("capsolver solve failed: %s", exc)
            return None

        solution = final.get("solution", {})
        token = solution.get("gRecaptchaResponse") or solution.get("text")
        if not token:
            return None

        latency_ms = int((time.perf_counter() - start) * 1000)
        return CaptchaSolution(
            text=str(token),
            confidence=None,
            provider=self.name,
            latency_ms=latency_ms,
        )


__all__ = ["CapSolverSolver"]
