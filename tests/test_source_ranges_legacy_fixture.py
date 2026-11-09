"""Test-only schema construction never weakens production legacy fences."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path

import pytest

from lumen.model import Project, VERSION
from source_ranges_legacy_fixture import strip_source_ranges_field


@pytest.mark.parametrize('version',range(1,38))
def test_explicit_legacy_fixture_removes_only_new_metadata(version):
    payload={'version':version,'source_ranges':[],'sentinel':{'keep':[1,2]}}
    before=deepcopy(payload)
    assert strip_source_ranges_field(payload) is payload
    assert payload == {key:value for key,value in before.items() if key!='source_ranges'}
    assert before['source_ranges']==[]


@pytest.mark.parametrize('version',[True,False,None,'37',0,-1,38,39])
def test_fixture_refuses_current_forward_or_invalid_versions(version):
    payload={'version':version,'source_ranges':[]};before=deepcopy(payload)
    with pytest.raises(AssertionError):strip_source_ranges_field(payload)
    assert payload==before


def test_genuine_accepted_schema37_project_preserves_every_historical_field():
    # Captured using cutcat-source-insert's unmodified schema-37 serializer,
    # not current to_dict() with a tag subsequently changed to 37.
    path=Path(__file__).parent/'fixtures'/'schema37-before-saved-source-ranges.json'
    assert hashlib.sha256(path.read_bytes()).hexdigest() == '39b13cf1209ec15a26f066ad788b5dc43112fef235092b90b3faaea50d263ed7'
    payload=json.loads(path.read_text())
    assert payload['version']==37 and 'source_ranges' not in payload
    before=deepcopy(payload)
    restored=Project.from_dict(payload)
    assert restored.source_ranges==[] and payload==before
    current=restored.to_dict()
    assert current.pop('timeline_markers')==[]
    assert current.pop('source_ranges')==[] and current['version']==VERSION==43
    assert current.pop('av_links') == []
    current['version']=37
    for clip in current['clips']:
        assert clip.pop('source_audio_enabled') is True
        assert clip.pop('audio_mix_anchor') is None
        assert clip.pop('audio_gain_keyframes') == []
    assert current==before
