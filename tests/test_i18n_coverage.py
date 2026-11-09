"""Source-based localization coverage, independent of visual smoke snapshots.

Only developer-authored presentation keys are audited. Arbitrary captions,
paths, identifiers, model tags and native process diagnostics must never become
translation keys merely because they appear on screen.
"""
import ast
from pathlib import Path
from string import Formatter

from lumen import i18n
from lumen.i18n_catalog_errors import ZH_CN as ERROR_CATALOG

ROOT = Path(__file__).resolve().parents[1] / 'lumen'
BACKEND_MODULES = (
    'model', 'media', 'render', 'sound_library', 'sound_sources', 'local_ai', 'tts', 'tts_worker', 'asr',
    'sticker_assets', 'color', 'color_source', 'color_media', 'caption_render',
    'color_output', 'highbit_captions', 'highbit_stickers', 'highbit_effects', 'vignette_shape',
    'overlay_render', 'subtitles', 'subtitle_ai', 'semantic_search',
    'semantic_search_worker', 'voice_catalog', 'controlled_assistant',
    'video_transition', 'video_transition_render', 'video_transition_sources', 'video_transition_preflight',
)


def _call_name(call):
    if isinstance(call.func, ast.Name):
        return call.func.id
    if isinstance(call.func, ast.Attribute):
        return call.func.attr
    return ''


def _literal_translation_calls(tree):
    """Yield explicit literal keys; don't guess variable or user-data values."""
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = _call_name(node)
        if name not in {'tr', 'bind_text', 'bind_tooltip'}:
            continue
        # tr(source, **values), bind_text(widget, source, ...), and keyword forms.
        index = 0 if name == 'tr' else 1
        source = node.args[index] if len(node.args) > index else None
        for keyword in node.keywords:
            if keyword.arg == 'source':
                source = keyword.value
        if isinstance(source, ast.Constant) and isinstance(source.value, str):
            yield node.lineno, source.value


def _fields(template):
    """Include nested format-width/precision fields, not just outer fields."""
    result = set()
    for _, field, specification, _ in Formatter().parse(template):
        if field is not None:
            result.add(field)
        if specification:
            result.update(_fields(specification))
    return result


def _catalog():
    i18n._load_catalogs()
    return i18n._CATALOGS['zh-CN']


def test_every_literal_ui_translation_source_has_chinese_catalog_entry():
    catalog = _catalog()
    missing = []
    for path in sorted(ROOT.glob('ui*.py')):
        tree = ast.parse(path.read_text(encoding='utf-8'), filename=str(path))
        for line, source in _literal_translation_calls(tree):
            if source not in catalog:
                missing.append(f'{path.name}:{line}: {source!r}')
    assert not missing, 'Missing Chinese UI catalog keys:\n' + '\n'.join(missing)


def test_all_loaded_catalog_entries_preserve_format_field_sets():
    failures = []
    for source, target in sorted(_catalog().items()):
        if not isinstance(target, str) or not target:
            failures.append(f'Empty or non-text translation: {source!r}')
            continue
        try:
            expected, actual = _fields(source), _fields(target)
        except ValueError as exc:
            failures.append(f'Malformed format template {source!r}: {exc}')
            continue
        if expected != actual:
            failures.append(f'{source!r}: expected {expected!r}, got {actual!r}')
    assert not failures, '\n'.join(failures)


def _static_errors(tree):
    for node in ast.walk(tree):
        if isinstance(node, ast.Raise) and isinstance(node.exc, ast.Call) and node.exc.args:
            argument = node.exc.args[0]
            # Include the static fallback in e.g. stderr or 'No preview frame.'.
            candidates = argument.values if isinstance(argument, ast.BoolOp) else [argument]
            for value in candidates:
                if isinstance(value, ast.Constant) and isinstance(value.value, str):
                    yield node.lineno, value.value
        if isinstance(node, ast.Dict):
            # Isolated TTS/search workers communicate user-facing errors via JSON.
            for key, value in zip(node.keys, node.values):
                if (isinstance(key, ast.Constant) and key.value == 'error'
                        and isinstance(value, ast.Constant) and isinstance(value.value, str)):
                    yield node.lineno, value.value


def test_backend_static_errors_and_worker_protocol_fallbacks_are_translated():
    missing = []
    for module in BACKEND_MODULES:
        path = ROOT / f'{module}.py'
        for line, source in _static_errors(ast.parse(path.read_text(encoding='utf-8'))):
            if source not in ERROR_CATALOG:
                missing.append(f'{path.name}:{line}: {source!r}')
    assert not missing, 'Missing backend error translations:\n' + '\n'.join(missing)


def test_ui_static_exception_preconditions_are_translated():
    missing = []
    for path in sorted(ROOT.glob('ui*.py')):
        for line, source in _static_errors(ast.parse(path.read_text(encoding='utf-8'))):
            if source not in ERROR_CATALOG:
                missing.append(f'{path.name}:{line}: {source!r}')
    assert not missing, 'Missing UI precondition translations:\n' + '\n'.join(missing)


def test_audit_extractor_handles_bindings_but_ignores_user_values():
    # Exercise the audit itself, including APIs not currently used by every UI.
    tree = ast.parse('''
tr("Open {name}", name=user_filename)
bind_text(button, "Save")
bind_tooltip(button, source="Save project")
i18n.tr(source="Choose a file")
tr(caption.text)
label.setText(user_filename)
''')
    assert [source for _, source in _literal_translation_calls(tree)] == [
        'Open {name}', 'Save', 'Save project', 'Choose a file',
    ]
    assert _fields('{value:{width}.{precision}f} {{literal}}') == {'value', 'width', 'precision'}


def test_known_dynamic_display_labels_are_translated_without_touching_ids():
    from lumen.voice_catalog import PACKS
    from lumen.color_source import INPUT_LABELS
    catalog = _catalog()
    labels = {'Runtime', 'Model', 'Tokenizer'}
    labels.update(voice.origin for pack in PACKS.values() for voice in pack.voices)
    # These two standards are intentionally language-neutral technical notation.
    labels.update(set(INPUT_LABELS.values()) - {'Rec.709', 'sRGB'})
    tree = ast.parse((ROOT / 'subtitle_ai.py').read_text(encoding='utf-8'))
    for node in ast.walk(tree):
        if isinstance(node, (ast.Assign, ast.AugAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            if any(isinstance(target, ast.Name) and target.id == 'warnings' for target in targets):
                if isinstance(node.value, ast.Tuple):
                    labels.update(value.value for value in node.value.elts
                                  if isinstance(value, ast.Constant) and isinstance(value.value, str))
    missing = sorted(label for label in labels if label not in catalog)
    assert not missing, f'Missing known dynamic UI labels: {missing!r}'
