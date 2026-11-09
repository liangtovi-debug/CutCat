"""Known range errors translate; arbitrary names and technical data do not."""
import ast
from pathlib import Path
from string import Formatter

from lumen.i18n import set_locale, tr, tr_error
from lumen.i18n_catalog_source_ranges import ZH_CN, ERROR_ZH_CN, SOURCE_RANGES_HELP
from lumen.ui_help import help_text
from lumen import source_ranges, source_range_verification


def test_new_literal_model_worker_errors_have_explicit_chinese_translations():
    for module in (source_ranges, source_range_verification):
        tree = ast.parse(Path(module.__file__).read_text())
        for node in ast.walk(tree):
            if (isinstance(node, ast.Raise) and isinstance(node.exc, ast.Call)
                    and node.exc.args and isinstance(node.exc.args[0], ast.Constant)):
                message = node.exc.args[0].value
                if not isinstance(message, str): continue
                assert message in ERROR_ZH_CN
                set_locale('en'); assert tr_error(message) == message
                set_locale('zh-CN'); assert tr_error(message) != message


def test_nested_own_grid_reason_translates_without_rewriting_external_details():
    reason = 'Saved source range does not align exactly to the current project frame grid'
    set_locale('zh-CN')
    assert '帧网格' in tr_error('Cannot verify saved source range: ' + reason)
    assert reason not in tr_error('Cannot verify saved source range: ' + reason)
    detail = '/a/Source range <b>素材</b>.mov: Bad decode <name>'
    assert detail in tr_error('Cannot verify saved source range: ' + detail)


def test_placeholders_and_help_preserve_explicit_no_rebind_limit_contract():
    def fields(value):
        return {field for _, field, _, _ in Formatter().parse(value) if field}
    for source, translation in ZH_CN.items():
        assert fields(source) == fields(translation)
    assert SOURCE_RANGES_HELP in help_text()
    assert 'combined 30-second' in help_text() and 'no rounding' in help_text()
    set_locale('zh-CN')
    assert '不自动重新绑定' in help_text() and '合计 30 秒' in help_text()
    assert '<b>中文</b>' in tr('Saved: [{start}, {end}) seconds · captured at {fps} fps · {status}',
        start='0', end='1', fps='30', status='<b>中文</b>')
