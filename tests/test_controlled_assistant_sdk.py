"""Actual optional SDK loop to owned gate: generating is never approval."""
from copy import deepcopy
import json
import socket

import pytest
from lumen.agents_adapter import sdk_available, make_local_fake_model, AgentsProposalAdapter
from lumen.controlled_assistant import ProposalGate, ProposalError
from lumen.model import Caption, Clip, Editor, Media, Project

pytestmark = pytest.mark.skipif(not sdk_available(), reason='optional openai-agents==0.24.0 is not installed')


@pytest.mark.parametrize('edit_before_apply', [False, True], ids=['apply-and-undo', 'stale-after-sdk'])
def test_real_sdk_candidate_passes_owned_gate_without_edit_capabilities(monkeypatch, edit_before_apply):
    def blocked(*args, **kwargs):
        pytest.fail('Optional local fake SDK test attempted network access')
    monkeypatch.setattr(socket.socket, 'connect', blocked)
    monkeypatch.setattr(socket, 'getaddrinfo', blocked)
    monkeypatch.setenv('OPENAI_API_KEY', 'synthetic-not-a-real-key')
    e = Editor(Project(media=[Media('m', '/tmp/synthetic.wav', 'Synthetic', 2, has_video=False, has_audio=True)],
                       clips=[Clip('shared-id', 'm', 2, 0, 0, 30)],
                       captions=[Caption('shared-id', 2, 10, 'Original caption')]))
    g = ProposalGate(e)
    request = g.begin(caption_ids=('shared-id',), clip_ids=('shared-id',), adapter='agents-sdk-local-fake',
                      provenance=(('adapter_version', 'openai-agents-0.24.0'),))
    model = make_local_fake_model(json.dumps({'schema_version': 1, 'commands': [
        {'op': 'caption.replace_text', 'target_id': 'shared-id', 'text': 'Reviewed caption'},
        {'op': 'clip.set_gain_db', 'target_id': 'shared-id', 'gain_db': -6}]}))
    adapter = AgentsProposalAdapter(model=model, test_mode=True)
    before = deepcopy(e.project)
    candidate = adapter.generate_candidate_sync(request.payload_json)
    proposal = g.prepare(request, candidate)
    assert e.project == before and not e.can_undo and e.revision == 0
    if edit_before_apply:
        e.update_caption('shared-id', text='New manual edit')
        e.undo()  # Equal content never revives old model output.
        with pytest.raises(ProposalError): g.approve(proposal)
        assert e.project == before and e.revision == 2
        return
    with pytest.raises(ProposalError):
        g.apply(proposal, None, caption_ids=('shared-id',), clip_ids=('shared-id',))
    receipt = g.apply(proposal, g.approve(proposal), caption_ids=('shared-id',), clip_ids=('shared-id',))
    assert receipt.changed and e.revision == 1 and len(e._undo) == 1
    assert e.project.captions[0].text == 'Reviewed caption' and e.project.clips[0].gain_db == -6
    assert e.undo() and e.project == before
