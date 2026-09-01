import json
import tempfile
import unittest
from pathlib import Path

from webhook_bridge.service import DeliveryPolicy, DeliveryWorker
from webhook_bridge.store import EventStore


class FakeTransport:
    def __init__(self, responses: list[int]) -> None:
        self.responses = responses
        self.calls: list[tuple[bytes, dict[str, str]]] = []

    def send(self, url: str, body: bytes, headers: dict[str, str], timeout: float) -> int:
        self.calls.append((body, headers))
        return self.responses.pop(0)


class DeliveryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.store = EventStore(Path(self.temp.name) / "events.db")

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_duplicate_event_creates_one_delivery(self) -> None:
        first_id, first_created = self.store.register_event("crm", "evt-1", {"name": "Ada"}, now=10)
        second_id, second_created = self.store.register_event("crm", "evt-1", {"name": "Ada"}, now=11)
        self.assertEqual(first_id, second_id)
        self.assertTrue(first_created)
        self.assertFalse(second_created)
        self.assertEqual(len(self.store.due_deliveries(now=20)), 1)

    def test_successful_delivery_preserves_idempotency_key(self) -> None:
        self.store.register_event("crm", "evt-9", {"lead": 9}, now=100)
        transport = FakeTransport([204])
        worker = DeliveryWorker(self.store, "https://example.test", transport=transport)
        self.assertEqual(worker.process_due(now=100), 1)
        body, headers = transport.calls[0]
        self.assertEqual(headers["Idempotency-Key"], "crm:evt-9")
        self.assertEqual(json.loads(body)["payload"], {"lead": 9})
        self.assertEqual(self.store.stats()["delivered"], 1)

    def test_server_error_is_retried_with_backoff(self) -> None:
        self.store.register_event("crm", "evt-2", {"lead": 2}, now=100)
        transport = FakeTransport([503, 200])
        worker = DeliveryWorker(
            self.store,
            "https://example.test",
            transport=transport,
            policy=DeliveryPolicy(base_delay_seconds=2),
        )
        worker.process_due(now=100)
        self.assertEqual(self.store.stats()["pending"], 1)
        self.assertEqual(worker.process_due(now=101), 0)
        self.assertEqual(worker.process_due(now=102), 1)
        self.assertEqual(self.store.stats()["delivered"], 1)

    def test_non_retryable_client_error_goes_to_dead_letter(self) -> None:
        self.store.register_event("crm", "evt-3", {"lead": 3}, now=100)
        worker = DeliveryWorker(self.store, "https://example.test", transport=FakeTransport([422]))
        worker.process_due(now=100)
        self.assertEqual(self.store.stats()["dead"], 1)


if __name__ == "__main__":
    unittest.main()

