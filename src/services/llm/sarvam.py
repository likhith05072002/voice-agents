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
# Clause boundaries \u2014 used ONLY to flush the very first chunk early so TTS can
# start speaking before the full sentence is generated. Later chunks use full
# sentence boundaries so per-chunk TTS prosody stays natural.
CLAUSE_ENDS = SENTENCE_ENDS | {",", ";", ":", "\u2014", "\u2013", "\u0965"}  # em/en dash, double danda
MIN_SENTENCE_LEN = 15
FIRST_CHUNK_MIN_LEN = 8   # first chunk may flush sooner to cut time-to-first-audio

# A '.' after these is an abbreviation, not a sentence end. Splitting there makes
# TTS say "...gold is Rs." <pause> "7800 per gram" (heard on a real test call).
_ABBREVIATIONS = ("rs", "dr", "mr", "mrs", "ms", "st", "no", "vs", "etc", "inc",
                  "ltd", "pvt", "approx")


def extract_json(text: str) -> dict:
    """Pull the first balanced ``{...}`` object out of model text and parse it.

    Models often wrap JSON in prose or ```json fences; this finds the first
    brace-balanced span and parses it, returning {} on any failure."""
    start = text.find("{")
    if start == -1:
        return {}
    depth = 0
    for i in range(start, len(text)):
        c = text[i]
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                try:
                    obj = json.loads(text[start:i + 1])
                    return obj if isinstance(obj, dict) else {}
                except json.JSONDecodeError:
                    return {}
    return {}


def _flush_boundary(buffer: str, is_first: bool) -> bool:
    """Should the buffered text be flushed to TTS now?

    First chunk: flush at a CLAUSE boundary past a short minimum (latency wins).
    Later chunks: flush only at a SENTENCE boundary past the normal minimum
    (prosody wins \u2014 fewer, longer chunks synthesize more naturally).
    """
    stripped = buffer.rstrip()
    if not stripped:
        return False
    ends = CLAUSE_ENDS if is_first else SENTENCE_ENDS
    min_len = FIRST_CHUNK_MIN_LEN if is_first else MIN_SENTENCE_LEN
    if len(stripped) < min_len or stripped[-1] not in ends:
        return False
    if len(stripped) >= 2 and stripped[-1] in (":", ",") and stripped[-2].isdigit():
        return False    # mid-number boundary: "at 7:00", "7,800" — don't split
    if stripped[-1] == ".":
        last_word = stripped[:-1].rsplit(None, 1)[-1].lower() if stripped[:-1].split() else ""
        if last_word in _ABBREVIATIONS:
            return False                     # "Rs." etc. — keep the sentence going
    return True


@dataclass
class SentenceEvent:
    text: str
    is_first: bool
    timestamp: float


class SarvamLLMClient:
    """Streaming LLM client that emits sentences as they complete."""

    def __init__(
        self,
        api_key: str,
        base_url: str = "https://api.sarvam.ai/v1",
        model: str = "sarvam-30b",
        reasoning_effort: str | None = None,
        max_tokens: int = 256,
    ):
        self.api_key = api_key
        self.base_url = base_url
        self.model = model
        # Sarvam enables "thinking" by default (low). Per the docs, sending
        # reasoning_effort: null DISABLES it, cutting first-token latency from
        # ~8s to ~340ms. None here is serialized as JSON null on purpose; a
        # string ("low"/"medium"/"high") selects an explicit thinking level.
        self.reasoning_effort = reasoning_effort
        self.max_tokens = max_tokens
        # Lazily created: constructing an AsyncClient scans the trust store /
        # proxy env (slow on some platforms), so don't pay for it until a real
        # request is made (and never in pure-logic tests).
        self._client: httpx.AsyncClient | None = None
        self._cancel = False

    @property
    def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=30.0)
        return self._client

    async def warmup(self) -> None:
        """Pre-create the HTTP client and warm a pooled TLS connection so the
        FIRST turn doesn't pay client construction + handshake on its critical
        path. Best-effort: fire this during the greeting; a failed request still
        warms the connection pool. Never raises."""
        try:
            await self._http.get(self.base_url, timeout=5.0)
        except Exception:  # noqa: BLE001 — warming is best-effort
            pass

    def _payload(self, messages: list[dict]) -> dict:
        """Build the streaming chat-completions request body.

        ``reasoning_effort`` is ALWAYS included: omitting it re-enables Sarvam's
        default thinking mode and tanks TTFT to ~8s. ``None`` -> JSON ``null``
        (reasoning disabled, lowest latency).
        """
        return {
            "model": self.model,
            "messages": messages,
            "stream": True,
            "max_tokens": self.max_tokens,
            "reasoning_effort": self.reasoning_effort,
        }

    def _complete_payload(self, messages: list[dict], tools: list[dict] | None = None) -> dict:
        """Non-streaming body, used for tool decisions and structured output."""
        body: dict = {
            "model": self.model,
            "messages": messages,
            "stream": False,
            "max_tokens": self.max_tokens,
            "reasoning_effort": self.reasoning_effort,
        }
        if tools:
            body["tools"] = tools
        return body

    async def complete(self, messages: list[dict], tools: list[dict] | None = None):
        """One non-streaming completion. Returns ``(content, tool_calls)``."""
        try:
            resp = await self._http.post(
                f"{self.base_url}/chat/completions",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json=self._complete_payload(messages, tools),
            )
            if resp.status_code != 200:
                logger.error("llm.complete_http_error", status=resp.status_code,
                             body=resp.text[:200])
                return None, []
            msg = resp.json()["choices"][0]["message"]
            return msg.get("content"), msg.get("tool_calls") or []
        except Exception as e:  # noqa: BLE001
            logger.error("llm.complete_error", error=str(e))
            return None, []

    async def complete_json(self, messages: list[dict]) -> dict:
        """Completion coerced to a JSON object (intent classification, slot
        extraction). Defensive: returns {} if the model didn't emit valid JSON."""
        content, _ = await self.complete(messages)
        return extract_json(content or "")

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
            async with self._http.stream(
                "POST",
                f"{self.base_url}/chat/completions",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json=self._payload(messages),
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

                    # Flush at a clause boundary for the first chunk, sentence
                    # boundary thereafter (see _flush_boundary).
                    if _flush_boundary(buffer, is_first):
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

            logger.info("llm.stream_done", chars=len(full_response),
                        tail_chars=len(buffer), cancelled=self._cancel,
                        ms=round((time.perf_counter() - start) * 1000))

        except Exception as e:
            logger.error("llm.stream_error", error=str(e))

        # Signal end of generation
        await sentence_queue.put(None)
        return full_response

    def cancel(self) -> None:
        self._cancel = True

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
