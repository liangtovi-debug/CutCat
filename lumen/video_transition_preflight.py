"""One-use, immutable cross-dissolve source proofs for asynchronous UI edits.

Only worker validation issues certificates. Owner-thread commits verify exact
session/revision/project/operation identity and cheap original-file identities.
This is not a file lock: external mutation after the final identity check remains
possible. Preview/export always independently hash and decode their own snapshots.
"""
from dataclasses import dataclass
import hashlib
import json
import threading
from uuid import uuid4
from weakref import WeakValueDictionary

from .video_transition import VideoTransition

STALE_PROOF = 'Video transition validation is stale; validate this edit again'
INVALID_PROOF = 'Video transition validation certificate is invalid or already used'


def project_json(project):
    return json.dumps(project.to_dict(), sort_keys=True, separators=(',', ':'), ensure_ascii=True)


def fingerprint(serialized):
    return hashlib.sha256(serialized.encode('utf-8')).hexdigest()


@dataclass(frozen=True)
class VideoTransitionRequest:
    session_id: str
    revision: int
    before_fingerprint: str
    candidate_json: str
    transition: VideoTransition
    operation: str


@dataclass(frozen=True, eq=False)
class VideoTransitionCertificate:
    request: VideoTransitionRequest
    candidate_fingerprint: str
    source_identities: tuple
    _token: str


_issued = WeakValueDictionary()
_lock = threading.Lock()


def validate_video_transition_request(request, timeout=30, cancel=None):
    """Worker-only expensive proof over a detached serialized project snapshot."""
    from .model import Project
    from .video_transition_sources import prepare_transition_source_certificate
    if type(request) is not VideoTransitionRequest or request.operation not in ('add', 'update'):
        raise ValueError(INVALID_PROOF)
    candidate = Project.from_dict(json.loads(request.candidate_json))
    if request.transition not in candidate.video_transitions:
        raise ValueError(INVALID_PROOF)
    identities = prepare_transition_source_certificate(candidate, timeout=timeout, cancel=cancel)
    if cancel is not None and (cancel() if callable(cancel) else cancel.is_set()):
        raise ValueError('Video transition source validation cancelled')
    certificate = VideoTransitionCertificate(request, fingerprint(project_json(candidate)),
                                              tuple(identities), uuid4().hex)
    with _lock:
        _issued[certificate._token] = certificate
    return certificate


def consume_video_transition_certificate(certificate, editor, candidate):
    """Owner-thread one-use verification; never hash/decode source file content."""
    from .video_transition_sources import verify_transition_source_identities
    if type(certificate) is not VideoTransitionCertificate:
        raise ValueError(INVALID_PROOF)
    with _lock:
        if _issued.get(certificate._token) is not certificate:
            raise ValueError(INVALID_PROOF)
        # Every attempted commit consumes the proof, including a stale attempt.
        del _issued[certificate._token]
    request = certificate.request
    if (request.session_id != editor.session_id or request.revision != editor.revision
            or request.before_fingerprint != fingerprint(project_json(editor.project))
            or certificate.candidate_fingerprint != fingerprint(project_json(candidate))):
        raise ValueError(STALE_PROOF)
    verify_transition_source_identities(certificate.source_identities)
