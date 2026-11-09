"""Register and prove the CJK fixture inside each Qt screenshot process.

No downloads or application font installation. A configured fixture must pass
its existing byte/license verification; an invalid fixture never falls back.
"""
from __future__ import annotations

import os


def prepare_layout_font(app):
    from PySide6.QtGui import QFont, QFontDatabase, QFontInfo, QTextLayout

    fixture = None
    directory = os.environ.get('LUMEN_TEST_CJK_FONT_DIR')
    if directory:
        from scripts.provision_test_font import verify_fixture, FONT_FAMILY
        verified = verify_fixture(directory)
        font_id = QFontDatabase.addApplicationFont(verified['font_file'])
        assert font_id >= 0, 'Qt could not register the verified CJK fixture'
        registered = QFontDatabase.applicationFontFamilies(font_id)
        assert FONT_FAMILY in registered, 'Registered CJK fixture family mismatch'
        fixture = dict(font_sha256=verified['font_sha256'],
                       license_sha256=verified['license_sha256'], families=registered)
        cjk = FONT_FAMILY
    else:
        families = QFontDatabase.families()
        cjk = next((f for f in ('Noto Sans CJK SC', 'Noto Serif CJK SC', 'WenQuanYi Zen Hei')
                    if f in families), None)
    assert cjk, 'Chinese layout evidence requires an installed or verified CJK font'
    font = QFont(app.font())
    original = QFontInfo(font)
    preferred = [font.family(), cjk] if original.family() and original.pixelSize() > 0 else [cjk]
    font.setFamilies(list(dict.fromkeys(preferred)))
    app.setFont(font)

    def proof(text, selected):
        info = QFontInfo(selected)
        assert info.family() and info.pixelSize() > 0, 'Qt has no resolved layout font'
        layout = QTextLayout(text, selected)
        layout.beginLayout()
        layout.createLine().setLineWidth(4096)
        layout.endLayout()
        runs = layout.glyphRuns()
        assert runs and all(run.rawFont().isValid() for run in runs), 'Invalid layout glyph run'
        assert all(run.glyphIndexes() and all(i != 0 for i in run.glyphIndexes())
                   for run in runs), 'Layout evidence must not use missing-glyph boxes'
        return dict(resolved_family=info.family(), pixel_size=info.pixelSize(),
                    glyph_families=[run.rawFont().familyName() for run in runs],
                    glyph_count=sum(len(run.glyphIndexes()) for run in runs))

    cjk_font = QFont(font)
    cjk_font.setFamilies([cjk])
    cjk_proof = proof('中文对比度中心颜色参考', cjk_font)
    assert cjk_proof['resolved_family'] == cjk, 'CJK font identity mismatch'
    assert all(f == cjk for f in cjk_proof['glyph_families']), 'CJK glyph fallback is not the verified family'
    return dict(fixture=fixture, cjk=cjk_proof,
                ui=proof('CutCat ABCxyz 0123456789 中文对比度中心', app.font()))
