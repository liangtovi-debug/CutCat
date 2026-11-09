"""One-use proof provenance and atomic owner-thread commits, without media I/O."""
from copy import deepcopy
from dataclasses import replace
import threading
from unittest.mock import patch

import pytest

from lumen.model import Clip, Editor, Media, Project
from lumen.video_transition_preflight import (VideoTransitionCertificate,
    validate_video_transition_request)


def editor():
    return Editor(Project(media=[Media('m', '/tmp/example.mp4', 'Example', 20, 1920, 1080, True, True)],
        clips=[Clip('a', 'm', 0, 10, 30, 60), Clip('b', 'm', 0, 70, 120, 60)]))


@pytest.fixture
def source_proofs():
    with patch('lumen.video_transition_sources.prepare_transition_source_certificate', return_value=()) as prepare:
        with patch('lumen.video_transition_sources.verify_transition_source_identities') as verify:
            yield prepare, verify


def proof(e, **kwargs):
    request = e.prepare_video_transition('a', 'b', 10, **kwargs)
    return validate_video_transition_request(request)


def test_worker_proof_then_owner_commit_no_second_decode(source_proofs):
    e = editor()
    request = e.prepare_video_transition('a', 'b', 10)
    before = deepcopy(e.project)
    result = []
    thread = threading.Thread(target=lambda: result.append(validate_video_transition_request(request)))
    thread.start(); thread.join()
    assert e.project == before and e.revision == 0
    transition = e.commit_video_transition(result[0])
    assert transition.id == request.transition.id and e.revision == 1
    prepare, verify = source_proofs
    assert prepare.call_count == 1 and verify.call_count == 1
    with pytest.raises(ValueError):
        e.commit_video_transition(result[0])
    assert e.revision == 1
    assert e.undo() and e.project == before


@pytest.mark.parametrize('change', ['revision', 'session', 'untracked', 'source'])
def test_stale_proofs_never_change_history(source_proofs, change):
    e = editor(); certificate = proof(e)
    if change == 'revision':
        e.update_clip('a', gain_db=-1)
        e.undo()
    elif change == 'session':
        e.project = deepcopy(e.project)
    elif change == 'untracked':
        e.project.name = 'Externally changed'
    else:
        source_proofs[1].side_effect = ValueError('source changed')
    before = deepcopy(e.project)
    state = (e.revision, len(e._undo), len(e._redo))
    with pytest.raises(ValueError):
        e.commit_video_transition(certificate)
    assert e.project == before
    assert state == (e.revision, len(e._undo), len(e._redo))


def test_forged_copied_mismatched_and_reused_certificates(source_proofs):
    e = editor(); certificate = proof(e)
    for forged in (True, object(), replace(certificate),
                   VideoTransitionCertificate(certificate.request, certificate.candidate_fingerprint, (), certificate._token)):
        with pytest.raises(ValueError):
            e.commit_video_transition(forged)
    other = editor()
    with pytest.raises(ValueError):
        other.commit_video_transition(certificate)
    with pytest.raises(ValueError):
        e.commit_video_transition(certificate)
    assert e.revision == other.revision == 0


def test_update_noop_and_mismatched_candidate(source_proofs):
    e = editor(); first = e.commit_video_transition(proof(e))
    cert = proof(e, transition_id=first.id)
    e.commit_video_transition(cert)
    assert e.revision == 1
    request = e.prepare_video_transition('a', 'b', 12, first.id)
    cert = validate_video_transition_request(request)
    e.commit_video_transition(cert)
    assert e.revision == 2 and e.project.video_transitions[0].duration_frames == 12
    e.undo()
    request = e.prepare_video_transition('a', 'b', 12, first.id)
    # Validating a snapshot alone cannot authorize a different operation.
    altered = replace(request, transition=replace(request.transition, duration_frames=14))
    with pytest.raises(ValueError):
        validate_video_transition_request(altered)
    assert e.can_redo


def test_cancel_and_failure_leave_model_untouched(source_proofs):
    e = editor(); before = deepcopy(e.project)
    event = threading.Event(); event.set()
    with pytest.raises(ValueError, match='cancelled'):
        validate_video_transition_request(e.prepare_video_transition('a', 'b', 10), cancel=event)
    source_proofs[0].side_effect = ValueError('decode failure')
    with pytest.raises(ValueError, match='decode failure'):
        proof(e)
    assert e.project == before and not e.can_undo and e.revision == 0


def test_request_and_commit_require_owner_thread(source_proofs):
    e = editor(); cert = proof(e); errors = []
    def worker():
        for operation in (lambda: e.prepare_video_transition('a', 'b', 10), lambda: e.commit_video_transition(cert)):
            try:
                operation()
            except ValueError as exc:
                errors.append(str(exc))
    thread = threading.Thread(target=worker); thread.start(); thread.join()
    assert len(errors) == 2 and e.revision == 0
    e.commit_video_transition(cert)
    assert e.revision == 1
