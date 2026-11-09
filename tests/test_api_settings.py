"""Configuration is local intent, never proof of connectivity or a saved secret."""
import dataclasses

import pytest

from lumen.api_settings import (ApiSettingsSession, ApiSettingsValidationError,
    normalize_base_url, validate_provider, validated_session)
from lumen.i18n_catalog_api_settings import VALIDATION_MESSAGES, ZH_CN


@pytest.mark.parametrize('source,expected', [
    ('https://EXAMPLE.com/v1/', 'https://example.com/v1'),
    ('  https://example.com:443/v1/  ', 'https://example.com/v1'),
    ('https://custom.example:8443/gateway/api/v2/', 'https://custom.example:8443/gateway/api/v2'),
    ('https://example.com', 'https://example.com'),
    ('https://127.0.0.1:443/v1', 'https://127.0.0.1/v1'),
    ('https://[::1]:8443/v1/', 'https://[::1]:8443/v1'),
])
def test_canonical_base_preserves_custom_target_and_never_invents_version(source, expected):
    assert normalize_base_url(source) == expected


@pytest.mark.parametrize('source', [
    '', 'example.com/v1', 'http://example.com/v1', 'http://localhost:8000/v1',
    'file:///tmp/model', 'ftp://example.com/v1', 'https://', 'https:///v1',
    'https://user:private-key@example.com/v1', 'https://example.com/v1?key=secret',
    'https://example.com/v1#secret', 'https://example.com/v1?', 'https://example.com/v1#',
    'https://example.com:99999/v1', 'https://example.com:0/v1',
    'https://example.com:notport/v1', 'https://[invalid]/v1',
    'https://example.com/a/../v1', 'https://example.com/a/./v1',
    'https://example.com/%', 'https://example.com/%GG',
    'https://example.com/<path>', 'https://example.com/"path"',
    'https://example.com/{path}', 'https://example.com/a|b',
    'https://example.com//', 'https://example.com:/v1',
    'https://example.com/%2e%2e/v1', 'https://example.com/v1%2fmodels',
    'https://example.com//v1', 'https://example.com/\\v1',
    'https://exam ple.com/v1', 'https://example.com/v1\n',
    'https://example.com/\u202ev1', 'https://bad_host.example/v1',
    'https://example.com/v1/chat/completions', 'https://example.com/v1/models/',
    'https://example.com/v1/responses', 'https://example.com/v1/completions',
    4, None,
])
def test_rejects_unsafe_or_operation_urls_without_echoing_input(source):
    with pytest.raises(ApiSettingsValidationError) as error:
        normalize_base_url(source)
    assert error.value.field_name == 'base_url'
    assert error.value.code in VALIDATION_MESSAGES
    assert 'private-key' not in str(error.value) and 'secret' not in repr(error.value)


def test_session_is_immutable_repr_redacted_and_key_bytes_not_normalized():
    session = validated_session('  My provider ', 'https://example.com/v1/', 'org/model:tag', 'synthetic-key')
    assert session.provider_name == 'My provider'
    assert session.api_key == 'synthetic-key'
    assert session.model == 'org/model:tag'
    assert 'synthetic-key' not in repr(session) and 'example.com' not in repr(session)
    with pytest.raises(dataclasses.FrozenInstanceError):
        session.model = 'changed'
    with pytest.raises(ApiSettingsValidationError) as error:
        validated_session('', 'https://example.com/v1', 'model', ' synthetic-key ')
    assert error.value.code == 'api_key_invalid'
    assert 'synthetic-key' not in str(error.value)


@pytest.mark.parametrize('field,value,code', [
    ('provider_name', 'x'*121, 'provider_name_invalid'),
    ('provider_name', 'Name\nSecond line', 'provider_name_invalid'),
    ('api_key', 'x'*8193, 'api_key_invalid'),
    ('api_key', 'key\r\nsecret', 'api_key_invalid'),
    ('api_key', 'key\x00secret', 'api_key_invalid'),
    ('model', '', 'model_required'),
    ('model', 'x'*257, 'model_invalid'),
    ('model', 'two models', 'model_invalid'),
    ('model', 'model\tname', 'model_invalid'),
    ('model', 'model\u200b', 'model_invalid'),
], ids=['long-name', 'name-newline', 'long-key', 'key-crlf', 'key-null', 'missing-model', 'long-model', 'model-space', 'model-tab', 'model-format-control'])
def test_bounded_fields_and_data_free_errors(field, value, code):
    args = dict(provider_name='', base_url='https://example.com/v1', model='model', api_key='')
    args[field] = value
    with pytest.raises(ApiSettingsValidationError) as error:
        validated_session(**args)
    assert error.value.code == code and error.value.field_name == field


def test_optional_name_and_key_do_not_claim_connection():
    assert validate_provider('', 'https://example.com/v1', '') == ('', 'https://example.com/v1', '')
    assert 'unverified' in repr(validated_session('', 'https://example.com/v1', 'model'))
    assert ApiSettingsSession().api_key == ''


def test_all_validation_codes_have_chinese_text():
    assert set(VALIDATION_MESSAGES.values()) <= set(ZH_CN)
