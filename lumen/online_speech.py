"""Consent and resource-bound interfaces for *future* online speech transport.

This module contains no service implementation, HTTP client, endpoint, credential
handling, downloads, or persistent approval. The app's default is unconfigured.
An injected transport is a trusted application-code seam, currently exercised
only by transport mocks. Its output is an opaque byte envelope, not evidence of
working synthesis, a particular voice, or usable audio.

Cancellation/deadlines are cooperative. A future transport MUST apply the
guard's remaining time to every blocking operation, check cancellation between
bounded waits, escape text as literal text (never interpret it as SSML), honor
the exact recipient scope, and close its resources. This wrapper cannot kill
arbitrary blocking Python code or retract bytes already transmitted. A future
real adapter needs its own authorization, service setup, and security review.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
import math
import threading
import time
from typing import Protocol

from .online_voice_catalog import get_online_voice


MAX_TEXT_CHARS = 20_000
MAX_TEXT_UTF8_BYTES = 80_000
MAX_RECIPIENT_SCOPE_CHARS = 256
MAX_CHUNK_BYTES = 64 * 1024
MAX_OUTPUT_BYTES = 16 * 1024 * 1024
MAX_OUTPUT_CHUNKS = 2048
MAX_PENDING_CONSENTS = 16
MAX_TIMEOUT_SECONDS = 120.0
CONSENT_LIFETIME_SECONDS = 300.0


class OnlineSpeechError(ValueError):
    """Public errors contain only static, non-sensitive messages."""


class OnlineSpeechConsentError(OnlineSpeechError):
    pass


class OnlineSpeechUnconfigured(OnlineSpeechError):
    pass


class OnlineSpeechCancelled(OnlineSpeechError):
    pass


class OnlineSpeechTimedOut(OnlineSpeechError):
    pass


def _number(value: object, lower: float, upper: float) -> bool:
    return (type(value) in (float, int) and lower <= value <= upper
            and math.isfinite(value))


@dataclass(frozen=True, slots=True, repr=False)
class OnlineSpeechSettings:
    """Desired settings only; provider support has not been verified live."""

    speed: float = 1.0
    pitch_semitones: float = 0.0
    volume: float = 1.0
    output_format: str = 'wav'

    def __post_init__(self):
        if not _number(self.speed, 0.5, 2.0):
            raise OnlineSpeechError('Speech speed must be finite and between 0.5 and 2.0.')
        if not _number(self.pitch_semitones, -12.0, 12.0):
            raise OnlineSpeechError('Speech pitch must be finite and between -12 and 12 semitones.')
        if not _number(self.volume, 0.0, 1.0):
            raise OnlineSpeechError('Speech volume must be finite and between 0 and 1.')
        if type(self.output_format) is not str or self.output_format != 'wav':
            raise OnlineSpeechError('Only the WAV output request format is supported.')

    def __repr__(self):
        return 'OnlineSpeechSettings(<redacted>)'


@dataclass(frozen=True, slots=True, repr=False)
class OnlineSpeechRequest:
    """Exact draft snapshot. Access ``text`` deliberately for consent display.

    ``recipient_scope`` is the particular service resource/account destination
    shown to the user, not an endpoint or credential. It must be verified by a
    future transport; catalog metadata alone does not configure a recipient.
    New/edited snapshots require new consent, even if later edited back.
    """

    text: str
    provider: str
    voice_id: str
    recipient_scope: str
    settings: OnlineSpeechSettings = field(default_factory=OnlineSpeechSettings)

    def __post_init__(self):
        _validate_request(self)

    def __repr__(self):
        return 'OnlineSpeechRequest(<redacted>)'


def _validate_request(request: OnlineSpeechRequest) -> None:
    if type(request) is not OnlineSpeechRequest:
        raise OnlineSpeechError('An immutable online speech request is required.')
    text = request.text
    if (type(text) is not str or not text.strip() or len(text) > MAX_TEXT_CHARS
            or '\x00' in text):
        raise OnlineSpeechError('Enter nonempty bounded text without NUL characters.')
    invalid_encoding = False
    try:
        encoded_size = len(text.encode('utf-8'))
    except UnicodeError:
        invalid_encoding = True
        encoded_size = 0
    if invalid_encoding or encoded_size > MAX_TEXT_UTF8_BYTES:
        raise OnlineSpeechError('Text exceeds the UTF-8 limit or contains unsupported Unicode.')
    # Catalog errors must not echo attacker-controlled IDs or text.
    valid_voice = False
    if type(request.provider) is str and type(request.voice_id) is str:
        try:
            get_online_voice(request.provider, request.voice_id)
            valid_voice = True
        except ValueError:
            pass
    if not valid_voice:
        raise OnlineSpeechError('Choose an explicitly cataloged provider and voice.')
    scope = request.recipient_scope
    if (type(scope) is not str or not scope.strip()
            or len(scope) > MAX_RECIPIENT_SCOPE_CHARS
            or any(ord(char) < 32 or ord(char) == 127 for char in scope)):
        raise OnlineSpeechError('Specify a bounded recipient resource/account scope without control characters.')
    if type(request.settings) is not OnlineSpeechSettings:
        raise OnlineSpeechError('Immutable online speech settings are required.')
    request.settings.__post_init__()


def _fingerprint(request: OnlineSpeechRequest) -> bytes:
    """An in-memory binding, never an external identifier or logged digest."""
    settings = request.settings
    content = (request.text, request.provider, request.voice_id,
               request.recipient_scope, settings.speed, settings.pitch_semitones,
               settings.volume, settings.output_format)
    return hashlib.sha256(json.dumps(content, ensure_ascii=True,
                                     separators=(',', ':')).encode('ascii')).digest()


class OnlineSpeechConsent:
    """Opaque one-use capability; only a gate-issued instance is recognized."""

    __slots__ = ()

    def __repr__(self):
        return 'OnlineSpeechConsent(<redacted>)'

    def __reduce__(self):
        raise OnlineSpeechConsentError('Online speech consent cannot be persisted or copied.')


class SpeechRequestGuard:
    """Cooperative cancellation/deadline budget for one dispatch attempt."""

    __slots__ = ('_deadline', '_cancel')

    def __init__(self, timeout_seconds: float, cancel: threading.Event | None = None):
        if not _number(timeout_seconds, 0.01, MAX_TIMEOUT_SECONDS):
            raise OnlineSpeechError('Choose a finite supported speech timeout.')
        if cancel is not None and type(cancel) is not threading.Event:
            raise OnlineSpeechError('A threading cancellation event is required.')
        self._deadline = time.monotonic() + timeout_seconds
        self._cancel = cancel

    def __repr__(self):
        return 'SpeechRequestGuard(<redacted>)'

    @property
    def remaining_seconds(self) -> float:
        if self._cancel is not None and self._cancel.is_set():
            raise OnlineSpeechCancelled('Online speech request cancelled.')
        remaining = self._deadline - time.monotonic()
        if remaining <= 0:
            raise OnlineSpeechTimedOut('Online speech request timed out.')
        return remaining

    def check(self) -> None:
        self.remaining_seconds


class SpeechByteStream(Protocol):
    def __next__(self) -> bytes: ...

    def close(self) -> None: ...


class OnlineSpeechTransport(Protocol):
    """Future bounded adapter contract, not a shipped transport implementation.

    Identity must refer to the exact authorized destination, remain immutable,
    and be checked again by the adapter before transmission. Each yielded chunk
    must be nonempty bytes no larger than MAX_CHUNK_BYTES. The iterable must
    provide ``close()`` to release resources, including after error/cancel.
    No redirects, silent fallback, retries, logging of text, text rewriting,
    credential persistence, or alternate recipients are authorized by this
    interface. The guard budget applies to creation, iteration, and cleanup.
    """

    provider: str
    recipient_scope: str

    def iter_bytes(self, request: OnlineSpeechRequest,
                   guard: SpeechRequestGuard) -> SpeechByteStream: ...


@dataclass(frozen=True, slots=True, repr=False)
class OnlineSpeechPayload:
    """Bounded opaque response; audio validity/voice quality are not established."""

    data: bytes

    def __post_init__(self):
        if type(self.data) is not bytes or not 0 < len(self.data) <= MAX_OUTPUT_BYTES:
            raise OnlineSpeechError('Online speech returned an invalid response size or type.')

    def __repr__(self):
        return 'OnlineSpeechPayload(<redacted>)'


@dataclass(frozen=True, slots=True, repr=False)
class _Approval:
    request: OnlineSpeechRequest
    fingerprint: bytes
    expires: float
    transport: object


class OnlineSpeechGate:
    """Fail-closed dispatch seam with bounded, in-memory, one-request consent.

    No transport is configured by default. Injecting one is a developer seam,
    not proof of service authorization; no real transport is shipped here.
    """

    def __init__(self, transport: OnlineSpeechTransport | None = None):
        self._transport = transport
        self._approvals: dict[OnlineSpeechConsent, _Approval] = {}
        self._lock = threading.Lock()

    def __repr__(self):
        return 'OnlineSpeechGate(<redacted>)'

    def approve_once(self, request: OnlineSpeechRequest, *,
                     user_confirmed: bool) -> OnlineSpeechConsent:
        """Record an approval already obtained for this exact displayed draft.

        The caller MUST first disclose the full original text, provider, voice,
        recipient resource/account and settings, and obtain explicit consent.
        This method is not a confirmation UI and must never run automatically.
        Consent cannot authorize setup, costs, credentials, or legal agreements.
        """
        if user_confirmed is not True:
            raise OnlineSpeechConsentError('Explicit consent is required for this exact request.')
        _validate_request(request)
        now = time.monotonic()
        with self._lock:
            self._approvals = {key: value for key, value in self._approvals.items()
                               if value.expires > now}
            if len(self._approvals) >= MAX_PENDING_CONSENTS:
                raise OnlineSpeechConsentError('Too many pending online speech approvals.')
            consent = OnlineSpeechConsent()
            self._approvals[consent] = _Approval(
                request, _fingerprint(request),
                now + CONSENT_LIFETIME_SECONDS, self._transport)
        return consent

    def revoke_all(self) -> None:
        """Forget pending approvals. In-flight requests use their cancel event."""
        with self._lock:
            self._approvals.clear()

    def dispatch(self, request: OnlineSpeechRequest, consent: OnlineSpeechConsent,
                 *, timeout_seconds: float = 30.0,
                 cancel: threading.Event | None = None) -> OnlineSpeechPayload:
        """Consume once, validate, then invoke only an explicitly injected seam.

        Every recognized attempt consumes consent, even on mismatch, timeout,
        cancellation, unconfigured service, or transport failure. No retry or
        fallback is performed. Returned bytes are not saved to disk.
        """
        if type(consent) is not OnlineSpeechConsent:
            raise OnlineSpeechConsentError('A fresh approval from this gate is required.')
        with self._lock:
            approval = self._approvals.pop(consent, None)
        if approval is None:
            raise OnlineSpeechConsentError('Approval is missing, revoked, or already consumed.')
        _validate_request(request)
        if (approval.expires <= time.monotonic()
                or approval.request is not request
                or approval.fingerprint != _fingerprint(request)
                or approval.transport is not self._transport):
            raise OnlineSpeechConsentError('Approval expired or the request changed; confirm again.')
        guard = SpeechRequestGuard(timeout_seconds, cancel)
        guard.check()
        transport = self._transport
        if transport is None:
            raise OnlineSpeechUnconfigured('No authorized online speech transport is configured.')

        # Suppress transport exceptions entirely, including their context and
        # potentially text-bearing custom messages, before exposing static errors.
        stream = None
        payload = None
        failure = None
        try:
            if (type(transport.provider) is not str
                    or transport.provider != request.provider
                    or type(transport.recipient_scope) is not str
                    or transport.recipient_scope != request.recipient_scope):
                failure = 'scope'
            else:
                guard.check()
                stream = transport.iter_bytes(request, guard)
                if not callable(getattr(stream, 'close', None)):
                    raise TypeError
                chunks = []
                total = 0
                count = 0
                while True:
                    guard.check()
                    try:
                        chunk = next(stream)
                    except StopIteration:
                        break
                    guard.check()
                    count += 1
                    if (type(chunk) is not bytes or not chunk
                            or len(chunk) > MAX_CHUNK_BYTES
                            or count > MAX_OUTPUT_CHUNKS):
                        failure = 'response'
                        break
                    total += len(chunk)
                    if total > MAX_OUTPUT_BYTES:
                        failure = 'response'
                        break
                    chunks.append(chunk)
                if failure is None:
                    guard.check()
                    if not chunks:
                        failure = 'response'
                    else:
                        payload = OnlineSpeechPayload(b''.join(chunks))
        except OnlineSpeechCancelled:
            failure = 'cancel'
        except OnlineSpeechTimedOut:
            failure = 'timeout'
        except Exception:
            failure = 'transport'
        finally:
            if stream is not None:
                try:
                    stream.close()
                except Exception:
                    failure = failure or 'transport'
        # Raising outside the handlers avoids retaining a sensitive exception as
        # __context__; neither its str/repr nor traceback is copied into errors.
        if failure == 'scope':
            raise OnlineSpeechConsentError('Transport recipient/provider differs from the approved request.')
        if failure == 'cancel':
            raise OnlineSpeechCancelled('Online speech request cancelled.')
        if failure == 'timeout':
            raise OnlineSpeechTimedOut('Online speech request timed out.')
        if failure == 'response':
            raise OnlineSpeechError('Online speech returned an invalid or oversized response.')
        if failure is not None:
            raise OnlineSpeechError('Online speech transport failed; no automatic retry was performed.')
        guard.check()
        return payload
