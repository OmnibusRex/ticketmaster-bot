"""CapSolver provider using robust async polling and exponential retry."""

from __future__ import annotations

import logging
import os
import time
from typing import Any

import httpx

from ..base import CaptchaChallenge, CaptchaSolution, CaptchaSolver
from ..polling import PollingError, poll_until
from ..retry import retry_async

log = logging.getLogger("ticketmaster-bot")

CAPSOLVER_API = "https://api.capsolver.com"


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

    async def solve(self, challenge: CaptchaChallenge) -> CaptchaSolution | None:
        """Attempt to solve the challenge via CapSolver."""
        if not self.api_key:
            return None

        start = time.perf_counter()

        # Build a minimal task payload. Extend based on challenge_type.
        # For real use, extract websiteKey / websiteURL from the page.
        task_payload: dict[str, Any] = {
            "type": "ReCaptchaV2TaskProxyLess",
            "websiteURL": "https://www.ticketmaster.com",
            "websiteKey": "6Le-wvkSAAAAAPBMRTvw0Q4Muexq9bi0DJwx_mJ-",  # placeholder
        }
        if challenge.challenge_type == "yii_image" and challenge.screenshot_bytes:
            import base64

            task_payload = {
                "type": "ImageToTextTask",
                "body": base64.b64encode(challenge.screenshot_bytes).decode("ascii"),
            }

        async def _do_solve() -> dict[str, Any]:
            async with httpx.AsyncClient(timeout=30.0) as client:
                # createTask
                resp = await client.post(
                    f"{CAPSOLVER_API}/createTask",
                    json={"clientKey": self.api_key, "task": task_payload},
                )
                resp.raise_for_status()
                data = resp.json()
                if data.get("errorId"):
                    raise RuntimeError(data.get("errorDescription", "createTask failed"))
                task_id = data["taskId"]

                # poll
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
