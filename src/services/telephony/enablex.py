"""EnableX Voice API telephony handler.

Handles incoming calls from EnableX:
1. Receives webhook when call comes in
2. Accepts call and starts media streaming
3. Receives/sends audio over WebSocket

Audio format: mu-law, 8kHz, mono, base64-encoded in JSON.
"""

import asyncio
import base64
import hashlib
import json

import httpx
import structlog

logger = structlog.get_logger()


class EnableXClient:
    """REST client for EnableX Voice API call control."""

    def __init__(self, app_id: str, app_key: str):
        self.app_id = app_id
        self.app_key = app_key
        self.base_url = "https://api.enablex.io/voice/v1"
        self._auth = base64.b64encode(f"{app_id}:{app_key}".encode()).decode()
        self._client = httpx.AsyncClient(timeout=10.0)

    @property
    def headers(self) -> dict:
        return {
            "Authorization": f"Basic {self._auth}",
            "Content-Type": "application/json",
        }

    async def accept_call(self, voice_id: str) -> dict:
        resp = await self._client.put(
            f"{self.base_url}/call/{voice_id}/accept",
            headers=self.headers,
        )
        return resp.json()

    async def start_stream(self, call_id: str, ws_url: str) -> dict:
        resp = await self._client.put(
            f"{self.base_url}/call/{call_id}/stream",
            headers=self.headers,
            json={"stream_dest": ws_url},
        )
        return resp.json()

    async def hangup(self, call_id: str) -> dict:
        resp = await self._client.put(
            f"{self.base_url}/call/{call_id}/hangup",
            headers=self.headers,
        )
        return resp.json()

    async def close(self) -> None:
        await self._client.aclose()

    def decrypt_webhook(self, encrypted_body: str, algorithm: str = "aes-128-ecb") -> dict:
        """Decrypt EnableX webhook payload using app_id as key."""
        try:
            from Crypto.Cipher import AES
            key = self.app_id[:16].encode()
            cipher = AES.new(key, AES.MODE_ECB)
            decrypted = cipher.decrypt(bytes.fromhex(encrypted_body))
            # Remove padding
            pad_len = decrypted[-1]
            decrypted = decrypted[:-pad_len]
            return json.loads(decrypted.decode())
        except ImportError:
            # Fallback: try md5-based decryption
            logger.warning("enablex.crypto_not_available", msg="pycryptodome not installed")
            return json.loads(encrypted_body)
        except Exception as e:
            logger.error("enablex.decrypt_error", error=str(e))
            return {}
