"""
Make an outbound call via EnableX to test the voice AI pipeline.
===============================================================
Calls YOUR phone number from +911169040030.
When you answer, the AI assistant (Lakshmi) starts talking.

Usage: python scripts/make_call.py +91XXXXXXXXXX

The server must be running (python -m uvicorn src.main:app --port 8000)
and the tunnel must be active.
"""

import asyncio
import base64
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent / ".env")

import httpx


async def make_call(to_number: str):
    app_id = os.getenv("ENABLEX_APP_ID")
    app_key = os.getenv("ENABLEX_APP_KEY")
    public_url = os.getenv("PUBLIC_URL")

    if not all([app_id, app_key, public_url]):
        print("ERROR: Set ENABLEX_APP_ID, ENABLEX_APP_KEY, PUBLIC_URL in .env")
        return

    auth = base64.b64encode(f"{app_id}:{app_key}".encode()).decode()
    webhook_url = f"{public_url}/webhook/enablex"

    print(f"Making outbound call:")
    print(f"  From: +911169040030")
    print(f"  To:   {to_number}")
    print(f"  Webhook: {webhook_url}")
    print()

    async with httpx.AsyncClient(timeout=30.0) as client:
        # Step 1: Initiate the call
        resp = await client.post(
            "https://api.enablex.io/voice/v1/calls",
            headers={
                "Authorization": f"Basic {auth}",
                "Content-Type": "application/json",
            },
            json={
                "from": "+911169040030",
                "to": to_number,
                "action_on_connect": "command",
                "call_handler": "client",
                "status_callback_url": webhook_url,
            },
        )

        print(f"Response: {resp.status_code}")
        print(f"Body: {resp.text[:500]}")

        if resp.status_code in (200, 201, 202):
            data = resp.json()
            voice_id = data.get("voice_id") or data.get("call_id") or data.get("callId")
            print(f"\nCall initiated! Voice ID: {voice_id}")
            print(f"Answer your phone at {to_number}!")
            print("The AI assistant will start talking once you answer.")
            print("\nWatching for call events... (Ctrl+C to stop)")

            # Keep running to see events
            try:
                while True:
                    await asyncio.sleep(1)
            except KeyboardInterrupt:
                print("\nDone.")
        else:
            print(f"\nCall failed. Check your EnableX credentials and number.")
            # Try alternative endpoints
            for endpoint in [
                "https://api.enablex.io/voice/v1/call",
                "https://api.enablex.io/voice/v2/calls",
            ]:
                resp2 = await client.post(
                    endpoint,
                    headers={
                        "Authorization": f"Basic {auth}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "from": "+911169040030",
                        "to": to_number,
                        "status_callback_url": webhook_url,
                    },
                )
                print(f"  Tried {endpoint}: {resp2.status_code} - {resp2.text[:200]}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python scripts/make_call.py +91XXXXXXXXXX")
        print("Example: python scripts/make_call.py +919876543210")
        sys.exit(1)

    to_number = sys.argv[1]
    if not to_number.startswith("+"):
        to_number = "+91" + to_number

    asyncio.run(make_call(to_number))
