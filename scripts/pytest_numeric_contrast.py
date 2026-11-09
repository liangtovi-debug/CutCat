"""Restrict caption-free pytest startup to the two verified numeric modules."""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
NUMERIC_MODULES = frozenset((ROOT/'tests'/name).resolve() for name in (
    'test_contrast_pivot.py', 'test_native_contrast_oracle.py'))


def numeric_contrast_only(config):
    args = config.args
    if not args:
        return False
    for arg in args:
        path = Path(str(arg).split('::', 1)[0])
        if not path.is_absolute():
            path = Path(config.invocation_params.dir) / path
        if path.resolve() not in NUMERIC_MODULES:
            return False
    return True


def verify_numeric_imports(config):
    if numeric_contrast_only(config):
        if any(name in ('PIL', 'PySide6', 'lumen.i18n') or name.startswith(('PIL.', 'PySide6.')) for name in sys.modules):
            raise RuntimeError('Numeric contrast profile imported a UI/caption dependency; runtime isolation is no longer valid.')
