"""Telnyx webhook signature verification (Ed25519).

Telnyx signs each webhook with Ed25519 over ``f"{timestamp}|{raw_body}"`` and
sends the signature in the ``telnyx-signature-ed25519`` header (base64) plus the
signing timestamp in ``telnyx-timestamp``. Verify against your account's public
signing key (Telnyx portal -> public key), set via ``TELNYX_PUBLIC_KEY``.

Security posture:
  - If a public key IS configured, an invalid/missing signature is REJECTED.
  - If no key is configured (local dev), verification is skipped with a warning
    rather than blocking — production deployments must set the key.
"""

import base64

import structlog

logger = structlog.get_logger()


def verify_telnyx_signature(
    *,
    public_key_b64: str,
    signature_b64: str,
    timestamp: str,
    payload: bytes,
) -> bool:
    """Return True if the request is allowed to proceed.

    Returns True when no key is configured (dev mode, logged), or when the
    signature is cryptographically valid. Returns False only when a key is set
    and the signature does not verify.
    """
    if not public_key_b64:
        logger.warning(
            "telnyx.signature.unverified",
            reason="TELNYX_PUBLIC_KEY not set — set it in production",
        )
        return True

    if not signature_b64 or not timestamp:
        return False

    try:
        from nacl.signing import VerifyKey
        from nacl.exceptions import BadSignatureError
    except ImportError:
        # Dependency missing but a key was configured: fail closed.
        logger.error("telnyx.signature.pynacl_missing")
        return False

    try:
        verify_key = VerifyKey(base64.b64decode(public_key_b64))
        signed = f"{timestamp}|".encode() + payload
        verify_key.verify(signed, base64.b64decode(signature_b64))
        return True
    except (BadSignatureError, ValueError, Exception) as e:  # noqa: BLE001
        logger.warning("telnyx.signature.invalid", error=str(e))
        return False
