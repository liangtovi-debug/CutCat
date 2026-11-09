"""Failure evidence must distinguish execution from a rejected pixel oracle."""
import subprocess

import pytest

from lumen.clip_composite import (
    CLIP_COMPOSITE_ERROR, CLIP_MOTION_ERROR,
    verify_clip_composite_support, verify_clip_motion_support,
)


@pytest.mark.parametrize('motion', [False, True])
@pytest.mark.parametrize('returncode', [0, 1])
def test_failure_notes_preserve_strict_gate_and_motion_cause(monkeypatch, motion, returncode):
    processes = []

    class Process:
        def __init__(self, command, *, stdin, stdout, stderr):
            self.returncode = returncode
            self.waited = False
            stdout.write(b'bad')
            stdout.flush()
            stderr.write(b'fixture diagnostic\xff' + b'x' * 5000)
            stderr.flush()
            processes.append(self)

        def poll(self):
            return self.returncode

        def wait(self):
            self.waited = True
            return self.returncode

    monkeypatch.setattr(subprocess, 'Popen', Process)
    verify = verify_clip_motion_support if motion else verify_clip_composite_support
    with pytest.raises(RuntimeError) as caught:
        verify(False, 30)
    assert str(caught.value) == (CLIP_MOTION_ERROR if motion else CLIP_COMPOSITE_ERROR)
    detail = caught.value.__cause__ if motion else caught.value
    notes = detail.__notes__
    assert ('process exit' if returncode else 'pixel oracle mismatch') in notes[0]
    assert f'returncode={returncode}' in notes[0]
    assert 'bytes=3/43008' in notes[0]
    assert f'motion={motion}' in notes[0]
    assert notes[1].startswith('FFmpeg stderr (first 4096 bytes): fixture diagnostic\ufffd')
    assert len(notes[1].split(': ', 1)[1]) == 4096
    assert len(processes) == 1 and processes[0].waited
