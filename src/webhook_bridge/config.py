from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Settings:
    webhook_secret: str
    destination_url: str
    database_path: str = "webhook_bridge.db"
    max_signature_age_seconds: int = 300
    worker_interval_seconds: float = 2.0
    request_timeout_seconds: float = 10.0
    max_delivery_attempts: int = 8

    @classmethod
    def from_env(cls) -> Settings:
        return cls(
            webhook_secret=os.getenv("WEBHOOK_SECRET", "development-secret-change-me"),
            destination_url=os.getenv("DESTINATION_URL", "http://localhost:9000/events"),
            database_path=os.getenv("DATABASE_PATH", "webhook_bridge.db"),
            max_signature_age_seconds=int(os.getenv("MAX_SIGNATURE_AGE_SECONDS", "300")),
            worker_interval_seconds=float(os.getenv("WORKER_INTERVAL_SECONDS", "2")),
            request_timeout_seconds=float(os.getenv("REQUEST_TIMEOUT_SECONDS", "10")),
            max_delivery_attempts=int(os.getenv("MAX_DELIVERY_ATTEMPTS", "8")),
        )
