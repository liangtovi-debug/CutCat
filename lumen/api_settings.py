"""Memory-only API configuration. No transport, persistence, or model execution.

A valid configuration describes intent, not a working connection. Never attach
this object to a Project or serialize it. The app's offline provider is separate.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import ipaddress
import re
import unicodedata
from urllib.parse import unquote, urlsplit, urlunsplit


class ApiSettingsValidationError(ValueError):
    """Stable, data-free error code: never interpolate an input or secret."""
    def __init__(self, field_name: str, code: str):
        self.field_name = field_name
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, repr=False)
class ApiSettingsSession:
    provider_name: str = ''
    base_url: str = ''
    model: str = ''
    api_key: str = field(default='', repr=False)

    def __repr__(self) -> str:
        return '<ApiSettingsSession connection=unverified secret=redacted>'


def _invalid(field_name: str, code: str):
    raise ApiSettingsValidationError(field_name, code)


def _text(value: str, field_name: str, limit: int, *, required=False,
          spaces=True, trim=True) -> str:
    if not isinstance(value, str) or len(value) > limit:
        _invalid(field_name, f'{field_name}_invalid')
    # Reject controls and bidi/format controls even before stripping whitespace.
    if any(unicodedata.category(c).startswith('C') for c in value):
        _invalid(field_name, f'{field_name}_invalid')
    result = value.strip() if trim else value
    if required and not result:
        _invalid(field_name, f'{field_name}_required')
    if not spaces and any(c.isspace() for c in result):
        _invalid(field_name, f'{field_name}_invalid')
    return result


def normalize_base_url(value: str) -> str:
    """Canonical HTTPS base only. Never guess an API version or follow URLs."""
    value = _text(value, 'base_url', 2048, required=True, spaces=False)
    if '\\' in value or '?' in value or '#' in value:
        _invalid('base_url', 'base_url_extras')
    try:
        parsed = urlsplit(value)
        host, port = parsed.hostname, parsed.port
    except (ValueError, UnicodeError):
        _invalid('base_url', 'base_url_invalid')
    if parsed.scheme.lower() != 'https':
        _invalid('base_url', 'base_url_https')
    if not host or '%' in host or parsed.netloc.endswith(':') or parsed.username is not None or parsed.password is not None or '@' in parsed.netloc:
        _invalid('base_url', 'base_url_extras')
    if port is not None and not 1 <= port <= 65535:
        _invalid('base_url', 'base_url_invalid')
    try:
        address = ipaddress.ip_address(host)
        canonical_host = f'[{address.compressed}]' if address.version == 6 else address.compressed
    except ValueError:
        try:
            canonical_host = host.encode('idna').decode('ascii').lower()
        except UnicodeError:
            _invalid('base_url', 'base_url_invalid')
        if (len(canonical_host) > 253 or not all(
                re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', label)
                for label in canonical_host.split('.'))):
            _invalid('base_url', 'base_url_invalid')
    path = parsed.path.rstrip('/')
    # Reject traversal/escaped separators and ambiguity before a future transport
    # could reinterpret them. Preserve legitimate vendor prefixes and versions.
    decoded = unquote(path)
    if (not re.fullmatch(r"[A-Za-z0-9._~!$&'()*+,;=:@/\-]*", path)
            or decoded != path or '//' in parsed.path or any(p in ('.', '..') for p in path.split('/'))
            or any(c.isspace() or unicodedata.category(c).startswith('C') for c in path)):
        _invalid('base_url', 'base_url_path')
    if path.lower().endswith(('/chat/completions', '/completions', '/responses', '/models')):
        _invalid('base_url', 'base_url_operation')
    authority = canonical_host + (f':{port}' if port is not None and port != 443 else '')
    return urlunsplit(('https', authority, path, '', ''))


def validate_provider(provider_name: str, base_url: str, api_key: str) -> tuple[str, str, str]:
    name = _text(provider_name, 'provider_name', 120)
    url = normalize_base_url(base_url)
    # The key is optional for configuration preparation. Preserve exact bytes,
    # rather than silently changing a credential with .strip().
    key = _text(api_key, 'api_key', 8192, spaces=False, trim=False)
    return name, url, key


def validated_session(provider_name: str, base_url: str, model: str,
                      api_key: str = '') -> ApiSettingsSession:
    name, url, key = validate_provider(provider_name, base_url, api_key)
    model = _text(model, 'model', 256, required=True, spaces=False)
    return ApiSettingsSession(name, url, model, key)
