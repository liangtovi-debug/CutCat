"""Known native omissions must never become successful published takes."""
import json
import sys
from pathlib import Path
import pytest
from lumen import tts
from lumen.tts_text_review import reading_warnings
from test_tts import pack

@pytest.mark.parametrize('diagnostic,blocked', [
    ("Ignore OOV '嗧'", True),
    ('Skip unknown phonemes. Unicode codepoint: 602', True),
    ('Unknown token: ❓', False),
    ('ordinary runtime note', False),
])
def test_reported_omission_rejects_complete_worker_output(pack,tmp_path,monkeypatch,diagnostic,blocked):
    script=tmp_path/'worker.py'
    script.write_text('''import json,sys,wave
from pathlib import Path
r=json.loads(Path(sys.argv[1]).read_text())
print(sys.argv[3],file=sys.stderr)
with wave.open(r['output'],'wb') as w:
 w.setparams((1,2,24000,0,'NONE','not compressed'));w.writeframes(b'\\x01\\x00'*24000)
Path(sys.argv[2]).write_text(json.dumps({'engine_version':'test'}))
''')
    monkeypatch.setattr(tts,'_worker_command',lambda a,b:[sys.executable,str(script),str(a),str(b),diagnostic])
    out=tmp_path/'take.wav'
    if blocked:
        with pytest.raises(tts.TTSError,match='skipped unsupported') as exc:
            tts.synthesize('Original 字幕',tts.TTSConfig(str(pack)),out)
        assert diagnostic not in str(exc.value)
        assert not out.exists()
    else:
        result=tts.synthesize('Original 字幕',tts.TTSConfig(str(pack)),out)
        assert result.provenance.text=='Original 字幕' and out.exists()
    assert not list(tmp_path.glob('.lumen-tts-*'))

@pytest.mark.parametrize('text', ['他说：“检查字幕……再导出！”','（检查字幕）','［检查］','｛检查｝','＂检查＂','Wait...','MP4和1080p','v1.2.3','版本1.2.3'])
def test_review_known_frontend_risks_without_rewriting(text):
    original=text
    assert reading_warnings(text)
    assert text==original

@pytest.mark.parametrize('text',['请检查字幕，再导出。','价格3.14元。','2026年10月19日。','AI 与 API','第一句！\n第二句？'])
def test_review_does_not_blanket_reject_punctuation_numbers(text):
    assert reading_warnings(text)==()

def test_error_translation():
    from lumen.i18n import tr_error,set_locale
    text='The speech engine skipped unsupported text or sounds. No audio was saved. Revise the reading text or choose another model.'
    set_locale('zh-CN')
    try:assert '未保存音频' in tr_error(tts.TTSError(text))
    finally:set_locale('en')


def test_long_plain_text_review_is_bounded():
    import time
    start=time.monotonic()
    assert reading_warnings('1'*20000)==()
    assert reading_warnings('a'*20000)==()
    assert time.monotonic()-start<1.0
