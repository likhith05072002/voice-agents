"""Sarvam-30B Streaming LLM Client with sentence-level output."""

import asyncio
import json
import time
from dataclasses import dataclass

import httpx
import structlog

logger = structlog.get_logger()

# Sentence-ending characters for Indian languages
SENTENCE_ENDS = {".", "?", "!", "\u0964", "\n"}  # \u0964 = Devanagari danda
MIN_SENTENCE_LEN = 15


@dataclass
class SentenceEvent:
    text: str
    is_first: bool
    timestamp: float


class SarvamLLMClient:
    """Streaming LLM client that emits sentences as they complete."""

    def __init__(self, api_key: str, base_url: str = "https://api.sarvam.ai/v1", model: str = "sarvam-30b"):
        self.api_key = api_key
        self.base_url = base_url
        self.model = model
        self._client = httpx.AsyncClient(timeout=30.0)
        self._cancel = False

    async def generate_sentences(
        self,
        messages: list[dict],
        sentence_queue: asyncio.Queue,
    ) -> str:
        """Stream LLM tokens, detect sentence boundaries, put SentenceEvents on queue.
        Returns the full response text."""
        self._cancel = False
        buffer = ""
        full_response = ""
        is_first = True
        start = time.perf_counter()

        try:
            async with self._client.stream(
                "POST",
                f"{self.base_url}/chat/completions",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": self.model,
                    "messages": messages,
                    "stream": True,
                    "max_tokens": 256,
                    "reasoning_effort": None,
                },
            ) as resp:
                if resp.status_code != 200:
                    body = await resp.aread()
                    logger.error("llm.http_error", status=resp.status_code, body=body.decode()[:200])
                    return ""

                async for line in resp.aiter_lines():
                    if self._cancel:
                        break
                    if not line.startswith("data: "):
                        continue
                    data_str = line[6:].strip()
                    if data_str == "[DONE]":
                        break

                    try:
                        chunk = json.loads(data_str)
                    except json.JSONDecodeError:
                        continue

                    choices = chunk.get("choices", [])
                    if not choices:
                        continue
                    content = choices[0].get("delta", {}).get("content")
                    if not content:
                        continue

                    buffer += content
                    full_response += content

                    # Check for sentence boundary
                    if len(buffer) >= MIN_SENTENCE_LEN:
                        stripped = buffer.rstrip()
                        if stripped and stripped[-1] in SENTENCE_ENDS:
                            evt = SentenceEvent(
                                text=buffer,
                                is_first=is_first,
                                timestamp=time.perf_counter(),
                            )
                            await sentence_queue.put(evt)
                            if is_first:
                                logger.info("llm.first_sentence", ms=round((evt.timestamp - start) * 1000))
                            is_first = False
                            buffer = ""

            # Flush remaining buffer
            if buffer.strip() and not self._cancel:
                evt = SentenceEvent(
                    text=buffer,
                    is_first=is_first,
                    timestamp=time.perf_counter(),
                )
                await sentence_queue.put(evt)

        except Exception as e:
            logger.error("llm.stream_error", error=str(e))

        # Signal end of generation
        await sentence_queue.put(None)
        return full_response

    def cancel(self) -> None:
        self._cancel = True

    async def close(self) -> None:
        await self._client.aclose()
