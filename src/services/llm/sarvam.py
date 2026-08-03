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
        # Dotted abbreviations in ANY script: "ಐ.ಒ.ಟಿ." (IoT), "यू.आई." (UI).
        # Splitting at these dots chops one word into several TTS utterances —
        # heard live as stuttered letter-by-letter audio. An internal dot or a
        # very short final token means abbreviation, not sentence end.
        if "." in last_word or len(last_word.replace(".", "")) <= 2:
            return False
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
        model: str = "sarvam-105b",
        reasoning_effort: str | None = None,
        max_tokens: int = 256,
        temperature: float = 0.3,
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
        # Receptionists state facts; they don't improvise. Default sampling
        # invented services/products on live calls ("vocal review service",
        # "AcmeCloud") whenever STT garbled a word.
        self.temperature = temperature
        # Lazily created: constructing an AsyncClient scans the trust store /
        # proxy env (slow on some platforms), so don't pay for it until a real
        # request is made (and never in pure-logic tests).
        self._client: httpx.AsyncClient | None = None
        self._active: object | None = None   # per-call cancellation token

    # A dead Sarvam backend accepts the TCP connection and then never sends a
    # byte (2026-08-03 incident: 1/9 valid requests answered). Waiting the full
    # read-timeout is 30s of dead air on a live call — give up early, retry once.
    FIRST_LINE_TIMEOUT_S = 8.0

    @property
    def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(30.0, connect=5.0))
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

        ``reasoning_effort`` is included ONLY when set to a string. Sending
        JSON null used to disable thinking (their documented low-latency
        mode) — since 2026-08-03 a null makes Sarvam's gateway HANG FOREVER
        (validator now only accepts low/medium/high; A/B curl proven: null =
        0 bytes in 35s, field omitted = 200 in 1.8s). NEVER send null.
        """
        body: dict = {
            "model": self.model,
            "messages": messages,
            "stream": True,
            "max_tokens": self.max_tokens,
            "temperature": self.temperature,
        }
        if self.reasoning_effort:
            body["reasoning_effort"] = self.reasoning_effort
        return body

    def _complete_payload(self, messages: list[dict], tools: list[dict] | None = None) -> dict:
        """Non-streaming body, used for tool decisions and structured output."""
        body: dict = {
            "model": self.model,
            "messages": messages,
            "stream": False,
            "max_tokens": self.max_tokens,
            "temperature": self.temperature,
        }
        if self.reasoning_effort:
            body["reasoning_effort"] = self.reasoning_effort
        if tools:
            body["tools"] = tools
        return body

    async def complete(self, messages: list[dict], tools: list[dict] | None = None):
        """One non-streaming completion. Returns ``(content, tool_calls)``.

        Bounded + one retry: this sits on a live call's critical path, so a
        hung backend must cost seconds, not the full read-timeout. A retry
        usually lands on a different backend when the provider is flapping."""
        for attempt in (1, 2):
            try:
                resp = await self._http.post(
                    f"{self.base_url}/chat/completions",
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "Content-Type": "application/json",
                    },
                    json=self._complete_payload(messages, tools),
                    timeout=12.0,
                )
                if resp.status_code != 200:
                    logger.error("llm.complete_http_error", status=resp.status_code,
                                 body=resp.text[:200])
                    return None, []
                msg = resp.json()["choices"][0]["message"]
                return msg.get("content"), msg.get("tool_calls") or []
            except Exception as e:  # noqa: BLE001
                if attempt == 1:
                    logger.warning("llm.complete_retry", error=str(e))
                    continue
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
        Returns the full response text.

        Retries ONCE, but only when the first attempt produced NOTHING — a
        retry after sentences were already queued would speak the answer's
        opening twice. During a flapping incident (backends accept the
        connection, never send a byte) the retry frequently lands on a
        healthy backend and saves the turn."""
        # Per-call cancellation token. A shared boolean reset at call entry can
        # REVIVE a previous, not-yet-dead stream (cancel A -> B starts -> flag
        # cleared -> A's loop resumes burning tokens). With a token, cancel()
        # kills the current stream and a new call supersedes any older one.
        token = object()
        self._active = token
        full_response = ""
        try:
            for attempt in (1, 2):
                full_response, emitted = await self._stream_once(
                    messages, sentence_queue, token)
                # `emitted` is the safety gate, not text length: only a turn
                # where NOTHING reached the speaker may be retried.
                if emitted or self._active is not token:
                    break
                if attempt == 1:
                    logger.warning("llm.stream_retry")
        finally:
            # ALWAYS signal end of generation — an early `return` on HTTP error
            # used to skip this and leave the consumer blocked forever (turn
            # stuck in THINKING until the caller spoke again).
            sentence_queue.put_nowait(None)
        return full_response

    async def _stream_once(
        self,
        messages: list[dict],
        sentence_queue: asyncio.Queue,
        token: object,
    ) -> tuple[str, bool]:
        """One streaming attempt. Returns (full_text, any_sentence_emitted).
        Never raises; a failure returns partial text so the caller can decide
        whether a retry is safe."""
        buffer = ""
        full_response = ""
        is_first = True
        emitted = False
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
                    return "", emitted

                # Watchdog: until the first line arrives, wait at most
                # FIRST_LINE_TIMEOUT_S — a dead backend accepts the socket
                # and sends nothing; the full read-timeout (30s) is a caller
                # hanging up. After bytes flow, the normal timeout governs.
                lines = resp.aiter_lines().__aiter__()
                got_line = False
                while True:
                    try:
                        if got_line:
                            line = await lines.__anext__()
                        else:
                            line = await asyncio.wait_for(
                                lines.__anext__(), self.FIRST_LINE_TIMEOUT_S)
                            got_line = True
                    except StopAsyncIteration:
                        break
                    except asyncio.TimeoutError:
                        logger.warning("llm.first_line_timeout",
                                       s=self.FIRST_LINE_TIMEOUT_S)
                        return "", emitted
                    if self._active is not token:   # cancelled or superseded
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
                    # NOTE: thinking deltas arrive as `reasoning_content`;
                    # only `content` is spoken.
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
                        emitted = True
                        if is_first:
                            logger.info("llm.first_sentence", ms=round((evt.timestamp - start) * 1000))
                        is_first = False
                        buffer = ""

            # Flush remaining buffer
            if buffer.strip() and self._active is token:
                evt = SentenceEvent(
                    text=buffer,
                    is_first=is_first,
                    timestamp=time.perf_counter(),
                )
                await sentence_queue.put(evt)
                emitted = True

            logger.info("llm.stream_done", chars=len(full_response),
                        tail_chars=len(buffer), cancelled=self._active is not token,
                        ms=round((time.perf_counter() - start) * 1000))
        except Exception as e:  # noqa: BLE001
            logger.error("llm.stream_error", error=str(e))
            # Keep any partial text: those sentences were already SPOKEN, and
            # history must record them (retry is blocked by emitted anyway).
            return full_response, emitted
        return full_response, emitted

    def cancel(self) -> None:
        self._active = None

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
