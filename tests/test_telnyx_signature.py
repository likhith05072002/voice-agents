import base64

import pytest

from src.security.telnyx import verify_telnyx_signature


def test_no_key_allows_dev_mode():
    # No configured key -> skip verification (dev), allow through.
    assert verify_telnyx_signature(
        public_key_b64="", signature_b64="", timestamp="", payload=b"{}"
    ) is True


def test_invalid_signature_rejected_when_key_set():
    nacl_signing = pytest.importorskip("nacl.signing")
    key = nacl_signing.SigningKey.generate()
    pub = base64.b64encode(bytes(key.verify_key)).decode()
    assert verify_telnyx_signature(
        public_key_b64=pub,
        signature_b64=base64.b64encode(b"not-a-real-signature").decode(),
        timestamp="123",
        payload=b'{"event":"x"}',
    ) is False


def test_valid_signature_accepted():
    nacl_signing = pytest.importorskip("nacl.signing")
    key = nacl_signing.SigningKey.generate()
    pub = base64.b64encode(bytes(key.verify_key)).decode()
    timestamp = "1700000000"
    payload = b'{"event":"call.answered"}'
    signed = key.sign(f"{timestamp}|".encode() + payload).signature
    assert verify_telnyx_signature(
        public_key_b64=pub,
        signature_b64=base64.b64encode(signed).decode(),
        timestamp=timestamp,
        payload=payload,
    ) is True
