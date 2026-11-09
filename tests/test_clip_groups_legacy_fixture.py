"""Historical fixture adaptation never weakens the production version fence."""
from copy import deepcopy
import pytest
from lumen.model import Project, VERSION
from clip_groups_legacy_fixture import strip_clip_groups_field


@pytest.mark.parametrize('version', range(1, 36))
def test_explicit_legacy_fixture_strips_only_new_group_field(version):
    payload = {'version': version, 'clip_groups': [], 'sentinel': {'keep': [1, 2]}}
    original = deepcopy(payload)
    assert strip_clip_groups_field(payload) is payload
    assert payload == {key: value for key, value in original.items() if key != 'clip_groups'}
    assert original['clip_groups'] == []


@pytest.mark.parametrize('version', [True, False, None, '35', 0, -1, 36, 37])
def test_fixture_refuses_current_future_or_invalid_versions(version):
    payload = {'version': version, 'clip_groups': []}
    original = deepcopy(payload)
    with pytest.raises(AssertionError):
        strip_clip_groups_field(payload)
    assert payload == original


def test_current_schema_remains_required_without_fixture_conversion():
    payload = Project().to_dict()
    assert payload['version'] == VERSION == 43
    payload.pop('clip_groups')
    with pytest.raises(ValueError, match='clip groups'):
        Project.from_dict(payload)
