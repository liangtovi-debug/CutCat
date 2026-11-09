"""Pure-stdlib interface tests using transport mocks, never real synthesis.

Mock bytes are deliberately not WAV audio and establish no service access,
voice quality, availability, provider behavior, or real-world audio result.

python -m unittest discover -s tests -p test_online_speech.py -v
"""
from __future__ import annotations

import ast
from concurrent.futures import ThreadPoolExecutor
import copy
from dataclasses import FrozenInstanceError, replace
import io
import logging
from pathlib import Path
import pickle
import threading
import traceback
import unittest
from unittest.mock import patch

from lumen import online_speech as speech


TEXT = '  原文……「不要改写」\r\n<break time="1s"/> & e\u0301  '
MOCK_SCOPE = 'transport-mock-resource'
MOCK_BYTES = b'transport-mock bytes; not synthesized audio'


def request(**changes):
    values = dict(text=TEXT, provider='azure', voice_id='zh-TW-HsiaoChenNeural',
                  recipient_scope=MOCK_SCOPE)
    values.update(changes)
    return speech.OnlineSpeechRequest(**values)


class TransportMock:
    """In-memory fixture only, with no service or audio inference."""

    provider = 'azure'
    recipient_scope = MOCK_SCOPE

    def __init__(self, chunks=(MOCK_BYTES,), before_yield=None, error=None):
        self.chunks = chunks
        self.before_yield = before_yield
        self.error = error
        self.calls = 0
        self.received = None
        self.closed = False

    def iter_bytes(self, draft, guard):
        self.calls += 1
        self.received = draft
        try:
            if self.error:
                raise self.error
            for chunk in self.chunks:
                if self.before_yield:
                    self.before_yield(guard)
                guard.check()
                yield chunk
        finally:
            self.closed = True


class OnlineSpeechTests(unittest.TestCase):
    def approved(self, transport=None, draft=None):
        transport = TransportMock() if transport is None else transport
        draft = request() if draft is None else draft
        gate = speech.OnlineSpeechGate(transport)
        consent = gate.approve_once(draft, user_confirmed=True)
        return gate, draft, consent, transport

    def assert_spent(self, gate, draft, consent):
        with self.assertRaises(speech.OnlineSpeechConsentError):
            gate.dispatch(draft, consent)

    def test_exact_original_text_and_settings_reach_transport_mock(self):
        gate, draft, consent, mock = self.approved()
        response = gate.dispatch(draft, consent)
        self.assertIs(mock.received, draft)
        self.assertEqual(mock.received.text, TEXT)
        self.assertEqual(mock.received.text.encode('utf-8'), TEXT.encode('utf-8'))
        self.assertIs(mock.received.settings, draft.settings)
        self.assertEqual(response.data, MOCK_BYTES)
        self.assertTrue(mock.closed)
        self.assert_spent(gate, draft, consent)
        self.assertEqual(mock.calls, 1)

    def test_snapshots_and_payload_are_immutable_and_redacted(self):
        draft = request(recipient_scope='private-recipient-scope')
        for obj, field, value in ((draft, 'text', 'changed'),
                                  (draft.settings, 'speed', 1.5),
                                  (speech.OnlineSpeechPayload(MOCK_BYTES), 'data', b'x')):
            with self.subTest(field=field):
                with self.assertRaises(FrozenInstanceError):
                    setattr(obj, field, value)
                self.assertNotIn(TEXT, repr(obj))
                self.assertNotIn('private-recipient-scope', repr(obj))
                self.assertNotIn(MOCK_BYTES.decode(), repr(obj))
                self.assertIn('<redacted>', repr(obj))

    def test_approval_requires_literal_explicit_true(self):
        gate = speech.OnlineSpeechGate()
        for value in (False, None, 1, 'true', [], object()):
            with self.subTest(value=type(value).__name__):
                with self.assertRaises(speech.OnlineSpeechConsentError):
                    gate.approve_once(request(), user_confirmed=value)
        with self.assertRaises(TypeError):
            gate.approve_once(request())

    def test_unconfigured_default_fails_closed_and_consumes_consent(self):
        draft = request()
        gate = speech.OnlineSpeechGate()
        consent = gate.approve_once(draft, user_confirmed=True)
        with self.assertRaises(speech.OnlineSpeechUnconfigured):
            gate.dispatch(draft, consent)
        self.assert_spent(gate, draft, consent)

    def test_new_or_edited_snapshot_invalidates_approval(self):
        for changes in ({}, {'text': TEXT + ' '}, {'text': TEXT.replace('\r\n', '\n')},
                        {'text': TEXT.replace('e\u0301', '\u00e9')},
                        {'voice_id': 'zh-TW-HsiaoYuNeural'},
                        {'recipient_scope': 'other-resource'},
                        {'settings': speech.OnlineSpeechSettings(speed=1.1)},
                        {'settings': speech.OnlineSpeechSettings(pitch_semitones=1)},
                        {'settings': speech.OnlineSpeechSettings(volume=0.8)},
                        {'provider': 'doubao',
                         'voice_id': 'zh_female_wanwanxiaohe_moon_bigtts'}):
            with self.subTest(changed_fields=tuple(changes)):
                gate, draft, consent, mock = self.approved()
                changed = replace(draft, **changes)
                with self.assertRaises(speech.OnlineSpeechConsentError):
                    gate.dispatch(changed, consent)
                self.assertEqual(mock.calls, 0)
                self.assert_spent(gate, draft, consent)

    def test_edit_then_revert_still_requires_new_approval(self):
        gate, draft, consent, mock = self.approved()
        reverted = replace(replace(draft, text='different'), text=TEXT)
        with self.assertRaises(speech.OnlineSpeechConsentError):
            gate.dispatch(reverted, consent)
        self.assertEqual(mock.calls, 0)

    def test_copied_snapshot_cannot_reuse_approval(self):
        for copier in (copy.copy, copy.deepcopy):
            with self.subTest(copier=copier.__name__):
                gate, draft, consent, mock = self.approved()
                cloned = copier(draft)
                self.assertIsNot(cloned, draft)
                self.assertEqual(cloned, draft)
                with self.assertRaises(speech.OnlineSpeechConsentError):
                    gate.dispatch(cloned, consent)
                self.assertEqual(mock.calls, 0)
                self.assert_spent(gate, draft, consent)

    def test_binding_rechecks_all_content_even_if_frozen_flag_is_bypassed(self):
        # Defends against accidental low-level mutation; not a sandbox against
        # hostile code that can inspect private state in the same interpreter.
        for field, value in (('text', TEXT + 'changed'),
                             ('voice_id', 'zh-TW-HsiaoYuNeural'),
                             ('recipient_scope', 'other-resource')):
            with self.subTest(field=field):
                gate, draft, consent, mock = self.approved()
                object.__setattr__(draft, field, value)
                with self.assertRaises(speech.OnlineSpeechConsentError):
                    gate.dispatch(draft, consent)
                self.assertEqual(mock.calls, 0)
        gate, draft, consent, mock = self.approved()
        object.__setattr__(draft.settings, 'volume', 0.9)
        with self.assertRaises(speech.OnlineSpeechConsentError):
            gate.dispatch(draft, consent)
        self.assertEqual(mock.calls, 0)

    def test_wrong_transport_recipient_or_provider_is_never_called(self):
        for field, value in (('provider', 'doubao'),
                             ('recipient_scope', 'wrong-resource')):
            with self.subTest(field=field):
                gate, draft, consent, mock = self.approved()
                setattr(mock, field, value)
                with self.assertRaises(speech.OnlineSpeechConsentError):
                    gate.dispatch(draft, consent)
                self.assertEqual(mock.calls, 0)
                self.assert_spent(gate, draft, consent)

    def test_changing_transport_invalidates_existing_consent(self):
        gate, draft, consent, old_mock = self.approved()
        new_mock = TransportMock()
        gate._transport = new_mock
        with self.assertRaises(speech.OnlineSpeechConsentError):
            gate.dispatch(draft, consent)
        self.assertEqual(old_mock.calls, 0)
        self.assertEqual(new_mock.calls, 0)

    def test_unissued_or_foreign_gate_consent_cannot_authorize(self):
        gate, draft, consent, mock = self.approved()
        other_gate = speech.OnlineSpeechGate(mock)
        for token in (None, {}, object(), speech.OnlineSpeechConsent(), consent):
            with self.subTest(token_type=type(token).__name__):
                with self.assertRaises(speech.OnlineSpeechConsentError):
                    other_gate.dispatch(draft, token)
        self.assertEqual(mock.calls, 0)
        self.assertEqual(gate.dispatch(draft, consent).data, MOCK_BYTES)

    def test_concurrent_replay_only_one_transport_call(self):
        gate, draft, consent, mock = self.approved()
        barrier = threading.Barrier(2)

        def dispatch():
            barrier.wait(timeout=2)
            try:
                return gate.dispatch(draft, consent).data
            except speech.OnlineSpeechConsentError:
                return None

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: dispatch(), range(2)))
        self.assertEqual(results.count(MOCK_BYTES), 1)
        self.assertEqual(results.count(None), 1)
        self.assertEqual(mock.calls, 1)

    def test_revocation_and_expiry(self):
        gate, draft, consent, mock = self.approved()
        gate.revoke_all()
        self.assert_spent(gate, draft, consent)
        with patch.object(speech.time, 'monotonic', return_value=10.0):
            gate, draft, consent, mock = self.approved()
        with patch.object(speech.time, 'monotonic', return_value=311.0):
            self.assert_spent(gate, draft, consent)
        self.assertEqual(mock.calls, 0)

    def test_pending_approval_storage_is_bounded_and_expired_entries_pruned(self):
        gate = speech.OnlineSpeechGate()
        draft = request()
        with patch.object(speech.time, 'monotonic', return_value=10.0):
            for _ in range(speech.MAX_PENDING_CONSENTS):
                gate.approve_once(draft, user_confirmed=True)
            with self.assertRaises(speech.OnlineSpeechConsentError):
                gate.approve_once(draft, user_confirmed=True)
        with patch.object(speech.time, 'monotonic', return_value=311.0):
            consent = gate.approve_once(draft, user_confirmed=True)
        self.assertEqual(len(gate._approvals), 1)
        self.assertNotIn(TEXT, repr(gate._approvals))
        self.assertNotIn(TEXT, repr(consent))
        self.assertNotIn(TEXT, repr(gate))

    def test_consent_cannot_be_serialized(self):
        gate, draft, consent, mock = self.approved()
        with self.assertRaises(speech.OnlineSpeechConsentError):
            pickle.dumps(consent)
        self.assertEqual(mock.calls, 0)

    def test_precancelled_request_does_not_call_transport(self):
        gate, draft, consent, mock = self.approved()
        cancel = threading.Event()
        cancel.set()
        with self.assertRaises(speech.OnlineSpeechCancelled):
            gate.dispatch(draft, consent, cancel=cancel)
        self.assertEqual(mock.calls, 0)
        self.assert_spent(gate, draft, consent)

    def test_cancel_during_transport_closes_without_returning_partial_bytes(self):
        cancel = threading.Event()
        mock = TransportMock(before_yield=lambda _: cancel.set())
        gate, draft, consent, _ = self.approved(mock)
        with self.assertRaises(speech.OnlineSpeechCancelled):
            gate.dispatch(draft, consent, cancel=cancel)
        self.assertTrue(mock.closed)
        self.assert_spent(gate, draft, consent)

    def test_cooperative_deadline_budget(self):
        now = [100.0]
        with patch.object(speech.time, 'monotonic', side_effect=lambda: now[0]):
            guard = speech.SpeechRequestGuard(2.0)
            self.assertEqual(guard.remaining_seconds, 2.0)
            now[0] = 101.75
            self.assertEqual(guard.remaining_seconds, 0.25)
            now[0] = 102.0
            with self.assertRaises(speech.OnlineSpeechTimedOut):
                guard.check()

    def test_transport_timeout_is_redacted_consumed_and_closed(self):
        now = [100.0]
        mock = TransportMock(before_yield=lambda _: now.__setitem__(0, 102.0))
        with patch.object(speech.time, 'monotonic', side_effect=lambda: now[0]):
            gate, draft, consent, _ = self.approved(mock)
            with self.assertRaises(speech.OnlineSpeechTimedOut):
                gate.dispatch(draft, consent, timeout_seconds=1.0)
        self.assertTrue(mock.closed)
        self.assert_spent(gate, draft, consent)

    def test_guard_after_each_yield_rejects_nonchecking_transport_mock(self):
        cancel = threading.Event()

        class NoncheckingTransportMock(TransportMock):
            def iter_bytes(self, draft, guard):
                try:
                    yield b'first partial transport-mock chunk'
                    cancel.set()
                    yield b'second transport-mock chunk'
                finally:
                    self.closed = True

        mock = NoncheckingTransportMock()
        gate, draft, consent, _ = self.approved(mock)
        with self.assertRaises(speech.OnlineSpeechCancelled):
            gate.dispatch(draft, consent, cancel=cancel)
        self.assertTrue(mock.closed)

    def test_cancel_and_deadline_are_checked_after_cleanup(self):
        cancel = threading.Event()

        class CancellingCleanupTransportMock(TransportMock):
            def iter_bytes(self, draft, guard):
                try:
                    yield MOCK_BYTES
                finally:
                    cancel.set()

        gate, draft, consent, _ = self.approved(CancellingCleanupTransportMock())
        with self.assertRaises(speech.OnlineSpeechCancelled):
            gate.dispatch(draft, consent, cancel=cancel)

    def test_errors_do_not_retain_text_or_exception_context_or_logs(self):
        for error in (RuntimeError(TEXT), speech.OnlineSpeechCancelled(TEXT),
                      speech.OnlineSpeechTimedOut(TEXT)):
            with self.subTest(error_type=type(error).__name__):
                mock = TransportMock(error=error)
                gate, draft, consent, _ = self.approved(mock)
                logged = io.StringIO()
                handler = logging.StreamHandler(logged)
                logging.getLogger().addHandler(handler)
                try:
                    try:
                        gate.dispatch(draft, consent)
                    except speech.OnlineSpeechError as exc:
                        self.assertNotIn(TEXT, str(exc))
                        self.assertNotIn(TEXT, repr(exc))
                        self.assertNotIn(TEXT, ''.join(traceback.format_exception(exc)))
                        self.assertIsNone(exc.__context__)
                        self.assertIsNone(exc.__cause__)
                    else:
                        self.fail('Transport mock error must fail closed.')
                finally:
                    logging.getLogger().removeHandler(handler)
                self.assertEqual(logged.getvalue(), '')
                self.assertEqual(mock.calls, 1)
                self.assertTrue(mock.closed)
                self.assert_spent(gate, draft, consent)

    def test_transport_creation_and_cleanup_errors_are_redacted(self):
        class CreationErrorTransportMock(TransportMock):
            def iter_bytes(self, draft, guard):
                raise RuntimeError(TEXT)

        class CleanupErrorTransportMock(TransportMock):
            def iter_bytes(self, draft, guard):
                try:
                    yield MOCK_BYTES
                    yield b'another transport-mock chunk'
                finally:
                    raise RuntimeError(TEXT)

        for mock in (CreationErrorTransportMock(), CleanupErrorTransportMock()):
            gate, draft, consent, _ = self.approved(mock)
            with self.assertRaises(speech.OnlineSpeechError) as caught:
                gate.dispatch(draft, consent)
            self.assertNotIn(TEXT, str(caught.exception))
            self.assertIsNone(caught.exception.__context__)

    def test_response_type_and_chunk_bounds(self):
        for chunks in ((), (b'',), ('private text is not bytes',), (bytearray(b'x'),),
                       (b'x' * (speech.MAX_CHUNK_BYTES + 1),)):
            with self.subTest(chunk_types=tuple(type(c).__name__ for c in chunks)):
                mock = TransportMock(chunks=chunks)
                gate, draft, consent, _ = self.approved(mock)
                with self.assertRaises(speech.OnlineSpeechError):
                    gate.dispatch(draft, consent)
                self.assertTrue(mock.closed)
                self.assert_spent(gate, draft, consent)

    def test_total_output_and_iteration_bounds(self):
        for limits, chunks in (({'MAX_OUTPUT_BYTES': 8}, (b'12345', b'67890')),
                               ({'MAX_OUTPUT_CHUNKS': 3}, (b'1',) * 4)):
            with self.subTest(limits=limits):
                mock = TransportMock(chunks=chunks)
                gate, draft, consent, _ = self.approved(mock)
                with patch.multiple(speech, **limits):
                    with self.assertRaises(speech.OnlineSpeechError):
                        gate.dispatch(draft, consent)
                self.assertTrue(mock.closed)

    def test_exact_resource_boundaries_are_accepted(self):
        draft = request(text='\U0001f431' * speech.MAX_TEXT_CHARS,
                        recipient_scope='r' * speech.MAX_RECIPIENT_SCOPE_CHARS)
        self.assertEqual(len(draft.text.encode('utf-8')), speech.MAX_TEXT_UTF8_BYTES)
        chunks = (b'1234', b'5678')
        mock = TransportMock(chunks)
        mock.recipient_scope = draft.recipient_scope
        gate, draft, consent, _ = self.approved(mock, draft)
        with patch.multiple(speech, MAX_OUTPUT_BYTES=8,
                            MAX_CHUNK_BYTES=4, MAX_OUTPUT_CHUNKS=2):
            self.assertEqual(gate.dispatch(draft, consent).data, b'12345678')

    def test_invalid_request_values_have_static_redacted_errors(self):
        cases = ({'text': ''}, {'text': ' \n\t'}, {'text': None},
                 {'text': 'x\x00y'}, {'text': '\ud800'},
                 {'text': 'x' * (speech.MAX_TEXT_CHARS + 1)},
                 {'provider': TEXT}, {'voice_id': TEXT},
                 {'provider': 'azure', 'voice_id': 'zh_female_wanwanxiaohe_moon_bigtts'},
                 {'recipient_scope': ''}, {'recipient_scope': '\n'},
                 {'recipient_scope': 'r' * (speech.MAX_RECIPIENT_SCOPE_CHARS + 1)},
                 {'settings': {'speed': 1.0}})
        for changes in cases:
            with self.subTest(changed_fields=tuple(changes)):
                with self.assertRaises(speech.OnlineSpeechError) as caught:
                    request(**changes)
                self.assertNotIn(TEXT, str(caught.exception))
                self.assertIsNone(caught.exception.__context__)

    def test_utf8_resource_limit_is_checked_without_normalizing(self):
        with patch.object(speech, 'MAX_TEXT_UTF8_BYTES', 7):
            self.assertEqual(request(text='中文').text, '中文')
            with self.assertRaises(speech.OnlineSpeechError):
                request(text='中文界')

    def test_invalid_settings(self):
        for field, values in (
                ('speed', (0, 2.1, True, None, '1', float('nan'), float('inf'), 10**1000)),
                ('pitch_semitones', (-13, 13, False, float('nan'))),
                ('volume', (-0.1, 1.1, True, float('inf'))),
                ('output_format', ('mp3', TEXT, None))):
            for value in values:
                with self.subTest(field=field, value_type=type(value).__name__):
                    with self.assertRaises(speech.OnlineSpeechError) as caught:
                        speech.OnlineSpeechSettings(**{field: value})
                    self.assertNotIn(TEXT, str(caught.exception))

    def test_timeout_and_cancel_parameter_bounds_fail_before_mock_call(self):
        for value in (0, -1, 0.009, 121, float('inf'), float('nan'), True, '30', 10**1000):
            with self.subTest(value_type=type(value).__name__):
                gate, draft, consent, mock = self.approved()
                with self.assertRaises(speech.OnlineSpeechError):
                    gate.dispatch(draft, consent, timeout_seconds=value)
                self.assertEqual(mock.calls, 0)
                self.assert_spent(gate, draft, consent)
        gate, draft, consent, mock = self.approved()
        with self.assertRaises(speech.OnlineSpeechError):
            gate.dispatch(draft, consent, cancel=object())
        self.assertEqual(mock.calls, 0)

    def test_stream_without_cleanup_contract_is_rejected(self):
        class NoCleanupTransportMock(TransportMock):
            def iter_bytes(self, draft, guard):
                return iter((MOCK_BYTES,))

        gate, draft, consent, _ = self.approved(NoCleanupTransportMock())
        with self.assertRaises(speech.OnlineSpeechError):
            gate.dispatch(draft, consent)

    def test_interface_imports_no_network_filesystem_or_credential_library(self):
        tree = ast.parse(Path(speech.__file__).read_text(encoding='utf-8'))
        imports = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                imports.add(node.module)
        self.assertEqual(imports, {'__future__', 'dataclasses', 'hashlib', 'json',
                                   'math', 'threading', 'time', 'typing',
                                   'online_voice_catalog'})


if __name__ == '__main__':
    unittest.main()
