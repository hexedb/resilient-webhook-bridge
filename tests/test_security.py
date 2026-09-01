import unittest

from webhook_bridge.security import InvalidSignature, sign_payload, verify_signature


class SignatureTests(unittest.TestCase):
    def test_valid_signature(self) -> None:
        signature = sign_payload("secret", b'{"ok":true}', 1000)
        verify_signature("secret", b'{"ok":true}', 1000, signature, now=1001)

    def test_modified_payload_is_rejected(self) -> None:
        signature = sign_payload("secret", b"original", 1000)
        with self.assertRaises(InvalidSignature):
            verify_signature("secret", b"modified", 1000, signature, now=1000)

    def test_stale_signature_is_rejected(self) -> None:
        signature = sign_payload("secret", b"payload", 1000)
        with self.assertRaises(InvalidSignature):
            verify_signature("secret", b"payload", 1000, signature, max_age_seconds=30, now=1031)


if __name__ == "__main__":
    unittest.main()

