"""Real nonzero process evidence is bounded and never emits private text."""
import json
import sys
import pytest
from lumen import audio_analysis as aa


def note_data(note):
    return json.loads(note.removeprefix('Audio decoder diagnostic: '))


def test_nonzero_decoder_keeps_error_and_private_stderr_is_not_emitted():
    secret = '/private/customer/婚礼.wav secret-token'
    code = "import sys;sys.stderr.write(" + repr(secret + "\n[error] Unrecognized option 'filter_complex_script'.\n[fatal] Error splitting the argument list: Option not found\n") + ");sys.exit(8)"
    with pytest.raises(aa.AudioAnalysisError) as caught:
        aa._run([sys.executable, '-c', code], aa._Budget(aa.AnalysisLimits(), None), 1024, 4096)
    assert str(caught.value) == 'The local audio decoder or required analysis filter failed.'
    assert secret not in repr(caught.value.__notes__)
    data = note_data(caught.value.__notes__[0])
    assert data['returncode'] == 8 and data['stdout_bytes'] == 0
    assert data['stderr_evidence'] == ['option_not_found', 'unrecognized_filter_complex_script']
    assert data['redacted_line_count'] == 1
    assert data['recognized_options'] == []


def test_diagnostic_only_copies_fixed_vocabulary_and_bounds_scan():
    private = "C:\\Users\\Owner\\Private.mov"
    raw = ("[error] No such filter: '" + private + "'\n[error] No such filter: 'ebur128'\n" + 'x'*20000).encode()
    note = aa._decoder_failure_note(['ffmpeg', '-filter_complex_script', private,
                                     '-i', private, '-metadata', 'secret-token'], 1, raw, [0,len(raw)])
    data = note_data(note)
    assert private not in note and 'secret-token' not in note and 'x'*100 not in note
    assert data['stderr_evidence'] == ['missing_ebur128']
    assert data['recognized_options'] == ['-filter_complex_script']
    assert data['stderr_scan_truncated'] and len(note) < 1000


def test_success_retains_original_pipe_results():
    result = aa._run([sys.executable, '-c', "import sys;sys.stdout.buffer.write(b'pcm');sys.stderr.buffer.write(b'meter')"],
                     aa._Budget(aa.AnalysisLimits(), None), 1024, 1024)
    assert result == (b'pcm', b'meter')
