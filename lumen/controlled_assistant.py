"""CutCat-owned, offline editing proposal boundary. No I/O or model capabilities.

Adapters get RequestSnapshot.payload_json only. The owning UI holds this gate,
shows its immutable diff, and issues a one-use approval after local confirmation.
This is an application trust boundary, not a Python sandbox for hostile plugins.
"""
from __future__ import annotations

from collections import deque
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
import json
import math
from uuid import uuid4

from .model import Editor, Project, normalize_caption_text

SCHEMA_VERSION = 1
MAX_BYTES = 65536
MAX_COMMANDS = 8
MAX_DEPTH = 8


class ProposalError(ValueError):
    """A candidate cannot change the project. Error messages omit user content."""


def _canonical(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def project_digest(project: Project) -> str:
    return sha256(_canonical(project.to_dict()).encode('utf-8')).hexdigest()


def _text(value: object) -> str:
    if type(value) is not str:
        raise ProposalError('Replacement text must be text')
    try:
        value = normalize_caption_text(value)
        if len(value) > 1000 or len(value.encode('utf-8')) > 2048:
            raise ValueError()
    except (ValueError, UnicodeError):
        raise ProposalError('Replacement text is empty, invalid or exceeds its safety limit') from None
    return value


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ProposalError('Duplicate JSON key')
        result[key] = value
    return result


def _constant(_):
    raise ProposalError('Non-finite JSON number')


def decode_candidate(raw: str) -> tuple['Command', ...]:
    """Strict schema + domain decoding without repair, coercion or dispatch."""
    if type(raw) is not str:
        raise ProposalError('Candidate must be a JSON string')
    try:
        if len(raw) > MAX_BYTES or len(raw.encode('utf-8')) > MAX_BYTES:
            raise ProposalError('Candidate exceeds the safety limit')
        value = json.loads(raw, object_pairs_hook=_unique, parse_constant=_constant)
        def depth(item, level=0):
            if level > MAX_DEPTH:
                raise ProposalError('Candidate nesting exceeds the safety limit')
            if isinstance(item, dict):
                for key, child in item.items():
                    key.encode('utf-8'); depth(child, level + 1)
            elif isinstance(item, list):
                for child in item:
                    depth(child, level + 1)
            elif isinstance(item, str):
                item.encode('utf-8')
        depth(value)
    except (UnicodeError, ValueError, RecursionError) as exc:
        if isinstance(exc, ProposalError):
            raise
        raise ProposalError('Invalid candidate JSON') from None
    if (type(value) is not dict or set(value) != {'schema_version', 'commands'}
            or type(value['schema_version']) is not int or value['schema_version'] != SCHEMA_VERSION
            or type(value['commands']) is not list or not 1 <= len(value['commands']) <= MAX_COMMANDS):
        raise ProposalError('Candidate schema or command count is invalid')
    commands = []
    targets = set()
    for item in value['commands']:
        if type(item) is not dict:
            raise ProposalError('Command must be an object')
        op = item.get('op')
        if type(op) is not str or op not in ('caption.replace_text', 'clip.set_gain_db'):
            raise ProposalError('Unsupported editing command')
        field = 'text' if op == 'caption.replace_text' else 'gain_db'
        if set(item) != {'op', 'target_id', field}:
            raise ProposalError('Unknown or missing command property')
        target = item['target_id']
        if type(target) is not str or not target or len(target) > 128 or any(ord(c) < 32 for c in target):
            raise ProposalError('Invalid target ID')
        if (op, target) in targets:
            raise ProposalError('Duplicate target and field')
        targets.add((op, target))
        if field == 'text':
            result = _text(item[field])
        else:
            result = item[field]
            if type(result) not in (int, float) or not -96 <= result <= 24 or not math.isfinite(result):
                raise ProposalError('Gain must be a finite number in -96..24 dB')
            result = float(result)
        commands.append(Command(op, target, result))
    return tuple(commands)


@dataclass(frozen=True)
class Command:
    op: str
    target_id: str
    value: str | float


@dataclass(frozen=True)
class Change:
    op: str
    target_id: str
    before: str | float
    after: str | float


@dataclass(frozen=True)
class RequestSnapshot:
    request_id: str
    session_id: str
    revision: int
    caption_ids: tuple[str, ...]
    clip_ids: tuple[str, ...]
    before_digest: str
    adapter: str
    payload_json: str
    provenance: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class Proposal:
    proposal_id: str
    request_id: str
    session_id: str
    revision: int
    commands: tuple[Command, ...]
    diff: tuple[Change, ...]
    digest: str
    adapter: str
    provenance: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class Approval:
    token: str
    proposal_id: str
    digest: str


@dataclass(frozen=True)
class Receipt:
    proposal_id: str
    digest: str
    status: str
    changed: bool
    revision: int
    timestamp_utc: str
    adapter: str = ''
    base_revision: int = 0
    source_digest: str = ''
    provenance: tuple[tuple[str, str], ...] = ()


class ProposalGate:
    """One selected-object request/review at a time, with a bounded local log."""
    def __init__(self, editor: Editor):
        self.editor = editor
        self._request = None
        self._before = None
        self._proposal = None
        self._approval = None
        self._receipts = deque(maxlen=64)

    @property
    def receipts(self) -> tuple[Receipt, ...]:
        return tuple(self._receipts)

    def cancel(self) -> None:
        self.editor._assert_owner()
        self._request = self._before = self._proposal = self._approval = None

    def begin(self, *, caption_ids=(), clip_ids=(), adapter='manual-offline', provenance=()) -> RequestSnapshot:
        self.editor._assert_owner()
        self.cancel()
        if adapter not in ('manual-offline', 'local-proofreader', 'agents-sdk-local-fake'):
            raise ProposalError('Unsupported adapter for this offline milestone')
        try:
            provenance = tuple(provenance)
            names = {'runtime_sha256', 'model_sha256', 'prompt_version', 'adapter_version'}
            if (len(provenance) > 4 or len({pair[0] for pair in provenance}) != len(provenance)
                    or any(type(pair) is not tuple or len(pair) != 2 or pair[0] not in names
                           or type(pair[1]) is not str or len(pair[1]) > 128 for pair in provenance)):
                raise ValueError()
        except (ValueError, TypeError, IndexError):
            raise ProposalError('Invalid local provenance') from None
        captions, clips = self._selection(caption_ids, clip_ids)
        with self.editor._lock:
            before = deepcopy(self.editor.project)
            before.validate()
            selected_captions = {c.id: c for c in before.captions}
            selected_clips = {c.id: c for c in before.clips}
            media = {m.id: m for m in before.media}
            if not set(captions) <= selected_captions.keys() or not set(clips) <= selected_clips.keys():
                raise ProposalError('Selected target no longer exists')
            if any(not media[selected_clips[c].media_id].has_audio for c in clips):
                raise ProposalError('Volume target must have audio')
            for caption_id in captions:
                _text(selected_captions[caption_id].text)
            payload = {'schema_version': 1,
                       'captions': [{'id': c, 'text': selected_captions[c].text} for c in captions],
                       'clips': [{'id': c, 'gain_db': selected_clips[c].gain_db} for c in clips]}
            request = RequestSnapshot(uuid4().hex, self.editor.session_id, self.editor.revision,
                                      captions, clips, project_digest(before), adapter, _canonical(payload), provenance)
            self._request, self._before = request, before
            return request

    @staticmethod
    def _selection(caption_ids, clip_ids):
        if isinstance(caption_ids, str) or isinstance(clip_ids, str):
            raise ProposalError('Selection must contain target IDs')
        try:
            captions, clips = tuple(caption_ids), tuple(clip_ids)
        except TypeError:
            raise ProposalError('Selection must contain target IDs') from None
        if (not 1 <= len(captions) + len(clips) <= MAX_COMMANDS
                or any(type(c) is not str or not c or len(c) > 128 for c in captions + clips)
                or len(set(captions)) != len(captions) or len(set(clips)) != len(clips)):
            raise ProposalError('Select one to eight unique existing targets')
        return tuple(sorted(captions)), tuple(sorted(clips))

    def _current_request(self, request):
        self.editor._assert_owner()
        if request is None or request is not self._request:
            raise ProposalError('Request was cancelled or replaced')
        if (request.session_id != self.editor.session_id or request.revision != self.editor.revision
                or request.before_digest != project_digest(self.editor.project)):
            raise ProposalError('Project changed; create a new preview')

    def prepare(self, request: RequestSnapshot, raw: str) -> Proposal:
        """Build a disposable validated after-project; preview never commits."""
        self._current_request(request)
        # A failed revision cannot leave an old approval or preview usable.
        self._proposal = self._approval = None
        commands = decode_candidate(raw)
        candidate, diff = self._project_candidate(request, commands)
        candidate.validate()
        identity = {'request_id': request.request_id, 'session_id': request.session_id,
                    'revision': request.revision, 'before_digest': request.before_digest,
                    'adapter': request.adapter, 'provenance': request.provenance,
                    'commands': [dict(op=c.op, target_id=c.target_id, value=c.value) for c in commands],
                    'after_digest': project_digest(candidate)}
        digest = sha256(_canonical(identity).encode('utf-8')).hexdigest()
        proposal = Proposal(uuid4().hex, request.request_id, request.session_id, request.revision,
                            commands, diff, digest, request.adapter, request.provenance)
        self._proposal = proposal
        return proposal

    def _project_candidate(self, request, commands):
        candidate = deepcopy(self._before)
        captions = {c.id: c for c in candidate.captions}
        clips = {c.id: c for c in candidate.clips}
        media = {m.id: m for m in candidate.media}
        diff = []
        for command in commands:
            if command.op == 'caption.replace_text':
                if command.target_id not in request.caption_ids:
                    raise ProposalError('Caption is outside the reviewed selection')
                target = captions[command.target_id]
                if request.adapter == 'local-proofreader':
                    from .subtitle_ai import _parse_output
                    try:
                        _parse_output(json.dumps({'text': command.value}, ensure_ascii=False).encode('utf-8'), target.text)
                    except ValueError:
                        raise ProposalError('Local proofreading changed protected text facts or structure') from None
                diff.append(Change(command.op, command.target_id, target.text, command.value))
                target.text = command.value
            elif command.op == 'clip.set_gain_db':
                if command.target_id not in request.clip_ids or request.adapter == 'local-proofreader':
                    raise ProposalError('Clip is outside the reviewed selection')
                target = clips[command.target_id]
                if not media[target.media_id].has_audio:
                    raise ProposalError('Volume target must have audio')
                diff.append(Change(command.op, command.target_id, target.gain_db, command.value))
                target.gain_db = command.value
            else:
                raise ProposalError('Unsupported editing command')
        candidate.validate()
        return candidate, tuple(diff)

    def check_current(self, proposal: Proposal, *, caption_ids, clip_ids) -> None:
        self._current_request(self._request)
        if proposal is not self._proposal:
            raise ProposalError('Preview was cancelled or revised')
        captions, clips = self._selection(caption_ids, clip_ids)
        if (captions, clips) != (self._request.caption_ids, self._request.clip_ids):
            raise ProposalError('Selection changed; create a new preview')

    def approve(self, proposal: Proposal) -> Approval:
        """UI-only: call after the user confirms this exact displayed diff."""
        self._current_request(self._request)
        if proposal is not self._proposal:
            raise ProposalError('Preview was cancelled or revised')
        if self._approval is not None:
            raise ProposalError('Preview already has an approval')
        self._approval = Approval(uuid4().hex, proposal.proposal_id, proposal.digest)
        return self._approval

    def apply(self, proposal: Proposal, approval: Approval, *, caption_ids, clip_ids) -> Receipt:
        self.editor._assert_owner()
        with self.editor._lock:
            self.check_current(proposal, caption_ids=caption_ids, clip_ids=clip_ids)
            if (approval is not self._approval or approval is None
                    or approval.proposal_id != proposal.proposal_id or approval.digest != proposal.digest):
                raise ProposalError('Exact local approval is required')
            # Consume before attempting the transaction. Errors require a fresh review.
            self._approval = None
            try:
                candidate, _ = self._project_candidate(self._request, proposal.commands)
                changed = candidate != self.editor.project
                def change():
                    # Never call live public mutators in a loop; one snapshot commit.
                    self.editor._project = candidate
                if changed:
                    self.editor._commit(change)
                receipt = Receipt(proposal.proposal_id, proposal.digest, 'applied' if changed else 'no-op',
                                  changed, self.editor.revision, datetime.now(timezone.utc).isoformat(),
                                  proposal.adapter, self._request.revision, self._request.before_digest,
                                  proposal.provenance)
                self._receipts.append(receipt)
                return receipt
            finally:
                self.cancel()
