from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Protocol

from .store import Delivery, EventStore


class Transport(Protocol):
    def send(self, url: str, body: bytes, headers: dict[str, str], timeout: float) -> int: ...


class UrlLibTransport:
    def send(self, url: str, body: bytes, headers: dict[str, str], timeout: float) -> int:
        request = urllib.request.Request(url, data=body, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return int(response.status)
        except urllib.error.HTTPError as exc:
            return int(exc.code)


@dataclass(frozen=True, slots=True)
class DeliveryPolicy:
    max_attempts: int = 8
    base_delay_seconds: float = 2.0
    max_delay_seconds: float = 300.0

    def delay_for_attempt(self, attempt: int) -> float:
        return min(self.max_delay_seconds, self.base_delay_seconds * (2**attempt))


class DeliveryWorker:
    def __init__(
        self,
        store: EventStore,
        destination_url: str,
        *,
        transport: Transport | None = None,
        policy: DeliveryPolicy | None = None,
        timeout: float = 10.0,
    ) -> None:
        self.store = store
        self.destination_url = destination_url
        self.transport = transport or UrlLibTransport()
        self.policy = policy or DeliveryPolicy()
        self.timeout = timeout

    def process_due(self, *, now: float | None = None, limit: int = 50) -> int:
        current = time.time() if now is None else now
        processed = 0
        for delivery in self.store.due_deliveries(now=current, limit=limit):
            self._deliver(delivery, current)
            processed += 1
        return processed

    def _deliver(self, delivery: Delivery, current: float) -> None:
        envelope: dict[str, Any] = {
            "source": delivery.source,
            "event_id": delivery.external_id,
            "payload": delivery.payload,
        }
        body = json.dumps(envelope, separators=(",", ":")).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "Idempotency-Key": f"{delivery.source}:{delivery.external_id}",
            "User-Agent": "resilient-webhook-bridge/1.0",
        }
        try:
            code = self.transport.send(self.destination_url, body, headers, self.timeout)
            if 200 <= code < 300:
                self.store.mark_delivered(delivery.id, code, now=current)
                return
            self._retry(delivery, current, f"destination returned HTTP {code}", code)
        except Exception as exc:  # noqa: BLE001 - worker must survive third-party transports
            self._retry(delivery, current, f"{type(exc).__name__}: {exc}", None)

    def _retry(self, delivery: Delivery, current: float, error: str, code: int | None) -> None:
        next_attempt = delivery.attempts + 1
        terminal = next_attempt >= self.policy.max_attempts or (code is not None and 400 <= code < 500 and code != 429)
        self.store.mark_failed(
            delivery.id,
            error,
            next_attempt_at=current + self.policy.delay_for_attempt(delivery.attempts),
            terminal=terminal,
            response_code=code,
        )
