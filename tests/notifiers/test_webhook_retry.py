"""Retry-policy tests for :class:`WebhookNotifier`.

Drives the notifier's retry loop against a real local FastAPI app booted on
an ephemeral port via ``uvicorn.Server``. No mocks, no monkeypatching of the
HTTP layer - every assertion is grounded in real socket traffic captured by
the test server.

Covers:
* ``[io.notifier-retry-5xx]`` - 503, 503, 200 produces exactly 3 POSTs and
  the third returns 200.
* ``[io.notifier-no-retry-4xx]`` - a 400 response produces exactly 1 POST
  and raises :class:`NotifierError`.
* ``[io.webhook-payload-shape]`` - the body the server receives parses as a
  JSON dict carrying at least ``event``, ``title``, ``message``,
  ``severity``, ``timestamp_iso`` and ``metadata`` keys.
* ``[io.webhook-real-post]`` ``Content-Type`` guarantee - the server sees
  ``application/json`` on each request.
"""

from __future__ import annotations

import asyncio
import json
import socket
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
import pytest
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import Response

from src.notifiers.base import NotifierError, NotifyEvent
from src.notifiers.webhook import WebhookNotifier


def _free_port() -> int:
    """Bind to port 0 and return the kernel-assigned port number."""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


class _SequenceServer:
    """FastAPI app that returns a pre-programmed sequence of status codes.

    Each POST consumes one status code from ``responses``; subsequent
    requests beyond the sequence keep returning the final code so callers
    can decide whether to assert on extra hits.
    """

    def __init__(self, responses: list[int]) -> None:
        self._remaining = list(responses)
        self._last = responses[-1] if responses else 200
        self.requests: list[dict[str, object]] = []
        self.app = FastAPI()

        @self.app.post("/hook")
        async def hook(request: Request) -> Response:
            body = await request.body()
            self.requests.append(
                {
                    "headers": {k.lower(): v for k, v in request.headers.items()},
                    "body": body,
                }
            )
            status = self._remaining.pop(0) if self._remaining else self._last
            return Response(status_code=status)

    @property
    def hits(self) -> int:
        return len(self.requests)


@asynccontextmanager
async def _serve(server: _SequenceServer) -> AsyncIterator[str]:
    """Boot ``server`` on an ephemeral port and yield its base URL."""
    port = _free_port()
    config = uvicorn.Config(
        app=server.app,
        host="127.0.0.1",
        port=port,
        log_level="error",
        lifespan="off",
        access_log=False,
    )
    uvi = uvicorn.Server(config)
    task = asyncio.create_task(uvi.serve())

    # Wait until uvicorn binds the socket.
    base = f"http://127.0.0.1:{port}"
    async with httpx.AsyncClient(timeout=2.0) as probe:
        for _ in range(50):
            if uvi.started:
                break
            await asyncio.sleep(0.05)
        else:  # pragma: no cover - defensive
            raise RuntimeError("uvicorn never started")
        # Also confirm reachable at the socket level.
        for _ in range(50):
            try:
                await probe.get(base + "/__nope__")
                break
            except httpx.HTTPError:
                await asyncio.sleep(0.05)

    try:
        yield base
    finally:
        uvi.should_exit = True
        await asyncio.wait_for(task, timeout=5.0)


def _make_event() -> NotifyEvent:
    return NotifyEvent(
        event_type="cart_success",
        title="Cart ready",
        message="Tickets reserved",
        severity="info",
        metadata={"account": "alice", "section": "100"},
    )


# ---------------------------------------------------------------------------
# [io.notifier-retry-5xx]
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_webhook_retries_5xx_until_success() -> None:
    server = _SequenceServer([503, 503, 200])

    async with _serve(server) as base:
        notifier = WebhookNotifier(
            url=f"{base}/hook",
            timeout=5.0,
            max_attempts=3,
            backoff_base=0.01,
        )
        await notifier.notify(_make_event())

    response = notifier.last_response
    assert response is not None
    assert response.status_code == 200
    assert server.hits == 3, f"expected exactly 3 POSTs (503, 503, 200), got {server.hits}"
    # All requests must declare JSON content type.
    for entry in server.requests:
        headers = entry["headers"]
        assert isinstance(headers, dict)
        assert headers.get("content-type") == "application/json"


# ---------------------------------------------------------------------------
# [io.notifier-no-retry-4xx]
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_webhook_does_not_retry_on_4xx() -> None:
    server = _SequenceServer([400, 400, 400])

    async with _serve(server) as base:
        notifier = WebhookNotifier(
            url=f"{base}/hook",
            timeout=5.0,
            max_attempts=3,
            backoff_base=0.01,
        )
        with pytest.raises(NotifierError) as info:
            await notifier.notify(_make_event())

    assert server.hits == 1, f"4xx must not retry; expected 1 POST, got {server.hits}"
    assert "400" in str(info.value)


@pytest.mark.asyncio
async def test_webhook_raises_after_exhausting_5xx_retries() -> None:
    server = _SequenceServer([503, 503, 503])

    async with _serve(server) as base:
        notifier = WebhookNotifier(
            url=f"{base}/hook",
            timeout=5.0,
            max_attempts=3,
            backoff_base=0.01,
        )
        with pytest.raises(NotifierError):
            await notifier.notify(_make_event())

    assert server.hits == 3


# ---------------------------------------------------------------------------
# [io.webhook-payload-shape]
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_webhook_payload_shape_and_headers() -> None:
    server = _SequenceServer([200])

    async with _serve(server) as base:
        notifier = WebhookNotifier(
            url=f"{base}/hook",
            headers={"X-Test": "webhook-payload"},
            timeout=5.0,
            backoff_base=0.01,
        )
        await notifier.notify(_make_event())

    assert server.hits == 1
    request = server.requests[0]
    headers = request["headers"]
    body = request["body"]
    assert isinstance(headers, dict)
    assert isinstance(body, (bytes, bytearray))
    # Content-Type and custom headers both made it through.
    assert headers.get("content-type") == "application/json"
    assert headers.get("x-test") == "webhook-payload"

    payload = json.loads(body)
    assert isinstance(payload, dict)
    for key in ("event", "title", "message", "severity", "timestamp_iso", "metadata"):
        assert key in payload, f"missing {key!r} in webhook payload"
    assert payload["event"] == "cart_success"
    assert payload["title"] == "Cart ready"
    assert payload["message"] == "Tickets reserved"
    assert payload["severity"] == "info"
    assert isinstance(payload["timestamp_iso"], str) and payload["timestamp_iso"]
    assert payload["metadata"] == {"account": "alice", "section": "100"}


# ---------------------------------------------------------------------------
# Registry wiring
# ---------------------------------------------------------------------------


def test_webhook_notifier_registered() -> None:
    from src.registry import notifiers as notifier_registry

    cls = notifier_registry.get("webhook")
    assert cls is WebhookNotifier


@pytest.mark.asyncio
async def test_webhook_retries_429_and_hides_endpoint_secret(caplog) -> None:
    server = _SequenceServer([429, 200])
    async with _serve(server) as base:
        notifier = WebhookNotifier(f"{base}/hook?token=private-test-token", backoff_base=0)
        with caplog.at_level("INFO"):
            await notifier.notify(_make_event())

    assert server.hits == 2
    assert "429" in caplog.text
    assert "private-test-token" not in caplog.text


@pytest.mark.asyncio
async def test_webhook_rejects_401_without_retrying() -> None:
    server = _SequenceServer([401])
    async with _serve(server) as base:
        notifier = WebhookNotifier(f"{base}/hook", backoff_base=0)
        with pytest.raises(NotifierError, match="401"):
            await notifier.notify(_make_event())

    assert server.hits == 1


@pytest.mark.asyncio
async def test_webhook_retries_timeout() -> None:
    server = _SequenceServer([200])
    hits = []

    @server.app.post("/slow")
    async def slow() -> Response:
        hits.append(True)
        await asyncio.sleep(0.2)
        return Response()

    async with _serve(server) as base:
        notifier = WebhookNotifier(f"{base}/slow", timeout=0.05, max_attempts=2, backoff_base=0)
        with pytest.raises(NotifierError, match="ReadTimeout"):
            await notifier.notify(_make_event())

    assert len(hits) == 2


@pytest.mark.asyncio
async def test_environment_notifier_reports_rejection(monkeypatch) -> None:
    from src.notify import notify

    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    server = _SequenceServer([401])
    async with _serve(server) as base:
        monkeypatch.setenv("DISCORD_WEBHOOK_URL", f"{base}/hook")
        assert await notify("Price alert") == {"discord": False}

    assert server.hits == 1


@pytest.mark.asyncio
async def test_dry_run_skips_notifications_and_build_failure_is_reported(monkeypatch) -> None:
    from src.cli import parse_args
    from src.main import main_async
    from src.registry import vendors

    class UnavailableAdapter:
        def build_runner(self, config, account):
            raise RuntimeError("Runner initialization failed")

    server = _SequenceServer([204])
    vendors.register("unavailable-test-adapter", UnavailableAdapter)
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    try:
        async with _serve(server) as base:
            monkeypatch.setenv("DISCORD_WEBHOOK_URL", f"{base}/hook")
            args = ["--vendor", "unavailable-test-adapter"]
            assert await main_async(parse_args([*args, "--dry-run"])) == 0
            assert server.hits == 0
            assert await main_async(parse_args(args)) == 1
    finally:
        vendors.registry.unregister("unavailable-test-adapter")

    messages = [json.loads(entry["body"])["content"] for entry in server.requests]
    assert len(messages) == 2
    assert "Bot starting" in messages[0]
    assert "Bot crashed" in messages[1]


@pytest.mark.asyncio
async def test_environment_notifier_can_target_one_channel(monkeypatch) -> None:
    from src.notify import notify

    server = _SequenceServer([204])
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    async with _serve(server) as base:
        monkeypatch.setenv("DISCORD_WEBHOOK_URL", f"{base}/hook")
        assert await notify("Price alert", only_channels={"telegram"}) == {}
        assert server.hits == 0
        assert await notify("Price alert", only_channels={"discord"}) == {"discord": True}

    assert server.hits == 1
