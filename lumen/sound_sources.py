"""Bounded attribution metadata for the shipped external CC0 collection.

These are recorded provenance claims, not a legal-rights inference about local
imports. Source links are informational only; loading a sound never opens them.
"""
from urllib.parse import urlsplit

SOURCE_FIELDS = ('author', 'source_name', 'source_url', 'license_url', 'source_file')
CC0_URL = 'https://creativecommons.org/publicdomain/zero/1.0/'


def validate_source_metadata(origin, license_name, values):
    if origin != 'external-cc0':
        if any(values.get(field, '') != '' for field in SOURCE_FIELDS):
            raise ValueError('Only external CC0 sounds may carry third-party source fields.')
        return
    if license_name != 'CC0-1.0':
        raise ValueError('External sounds require a recorded CC0-1.0 license.')
    for field in SOURCE_FIELDS:
        value = values.get(field)
        if (not isinstance(value, str) or not value.strip() or len(value) > 2048
                or any(ord(char) < 32 or ord(char) == 127 or 0xD800 <= ord(char) <= 0xDFFF for char in value)):
            raise ValueError('External sound source metadata is incomplete or invalid.')
    for field in ('source_url', 'license_url'):
        try:
            parsed = urlsplit(values[field])
            if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password:
                raise ValueError
        except ValueError as exc:
            raise ValueError('Sound source links must be public HTTPS URLs.') from exc
    if values['license_url'] not in (CC0_URL, CC0_URL + 'legalcode'):
        raise ValueError('External sounds require the official CC0 license link.')
