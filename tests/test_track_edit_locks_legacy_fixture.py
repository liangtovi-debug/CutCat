"""Test-only version adaptation does not weaken production schema fences."""
from copy import deepcopy
import pytest
from lumen.model import Project, VERSION
from track_edit_locks_legacy_fixture import strip_track_edit_locks_field

@pytest.mark.parametrize('version', range(1, 37))
def test_explicit_legacy_fixture_strips_only_new_lock_field(version):
    payload = {'version': version, 'track_edit_locks': [False]*4, 'sentinel': {'keep': [1, 2]}}
    original = deepcopy(payload)
    assert strip_track_edit_locks_field(payload) is payload
    assert payload == {key: value for key, value in original.items() if key != 'track_edit_locks'}
    assert original['track_edit_locks'] == [False]*4

@pytest.mark.parametrize('version', [True, False, None, '36', 0, -1, 37, 38])
def test_fixture_refuses_current_future_or_invalid_versions(version):
    payload = {'version': version, 'track_edit_locks': [False]*4}
    original = deepcopy(payload)
    with pytest.raises(AssertionError):
        strip_track_edit_locks_field(payload)
    assert payload == original

def test_current_schema_remains_required_without_fixture_conversion():
    payload = Project().to_dict()
    assert payload['version'] == VERSION == 43
    payload.pop('track_edit_locks')
    with pytest.raises(ValueError, match='track edit locks'):
        Project.from_dict(payload)
