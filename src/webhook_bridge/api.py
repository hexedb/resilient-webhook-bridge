from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI, Header, HTTPException, Request, status

from .config import Settings
from .security import InvalidSignature, verify_signature
from .service import DeliveryPolicy, DeliveryWorker
from .store import EventStore

settings = Settings.from_env()
store = EventStore(settings.database_path)
worker = DeliveryWorker(
    store,
    settings.destination_url,
    policy=DeliveryPolicy(max_attempts=settings.max_delivery_attempts),
    timeout=settings.request_timeout_seconds,
)


async def delivery_loop() -> None:
    while True:
        await asyncio.to_thread(worker.process_due)
        await asyncio.sleep(settings.worker_interval_seconds)


@asynccontextmanager
async def lifespan(_: FastAPI):
    task = asyncio.create_task(delivery_loop())
    yield
    task.cancel()
    with suppress(asyncio.CancelledError):
        await task


app = FastAPI(
    title="Resilient Webhook Bridge",
    version="1.0.0",
    description="Verified, idempotent webhook ingestion with durable delivery retries.",
    lifespan=lifespan,
)


@app.get("/health")
def health() -> dict[str, object]:
    return {"status": "ok", "queue": store.stats()}


@app.post("/webhooks/{source}", status_code=status.HTTP_202_ACCEPTED)
async def ingest(
    source: str,
    request: Request,
    x_event_id: str = Header(...),
    x_webhook_timestamp: int = Header(...),
    x_webhook_signature: str = Header(...),
) -> dict[str, object]:
    body = await request.body()
    try:
        verify_signature(
            settings.webhook_secret,
            body,
            x_webhook_timestamp,
            x_webhook_signature,
            max_age_seconds=settings.max_signature_age_seconds,
        )
    except InvalidSignature as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    try:
        payload = json.loads(body)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail="request body must be valid JSON") from exc
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="request body must be a JSON object")
    event_id, created = store.register_event(source, x_event_id, payload)
    return {"accepted": True, "duplicate": not created, "event_id": event_id}

