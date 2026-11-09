"""New backend errors and band descriptions stay bilingual and plain text."""
import ast
from dataclasses import replace
from pathlib import Path

import pytest

from lumen.i18n import set_locale, tr, tr_error
from lumen.i18n_catalog_errors import ZH_CN as ERROR_CATALOG
from lumen.timeline_markers import TimelineMarker, validate_timeline_marker
from test_i18n_coverage import _static_errors


def test_marker_backend_static_errors_are_explicitly_translated():
    source = Path(__file__).resolve().parents[1] / 'lumen' / 'timeline_markers.py'
    errors = [value for _, value in _static_errors(ast.parse(source.read_text()))]
    assert errors
    assert all(value in ERROR_CATALOG and ERROR_CATALOG[value] != value for value in errors)


@pytest.mark.parametrize('field,value', [('id', ''), ('name', ''), ('comment', '\x00'),
                                        ('start_frame', True), ('end_frame', False), ('color', 'invalid')])
def test_marker_dynamic_errors_are_translated_without_exposing_raw_ids(field, value):
    record = TimelineMarker('secret-id', 'Name', 'Comment', 'yellow', 1, 2)
    with pytest.raises(ValueError) as caught:
        validate_timeline_marker(replace(record, **{field: value}))
    assert str(caught.value) in ERROR_CATALOG
    try:
        set_locale('zh-CN')
        assert str(tr_error(caught.value)) != str(caught.value)
        assert 'secret-id' not in str(tr_error(caught.value))
    finally:
        set_locale('en')
