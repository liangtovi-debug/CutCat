"""Diagnostic capture must preserve probe results, failures and call counts."""
import json
import hashlib
from pathlib import Path
from types import SimpleNamespace
import subprocess

import pytest
import lumen.media as media
from media_endpoint_evidence import trace_synthetic_probes, LIMIT

STREAM = dict(index=0,codec_type='video',width=2,height=48,pix_fmt='gbrp16le',time_base='1/1000',
              avg_frame_rate='30000/1001',start_pts=0,start_time='0',
              color_space='rgb',color_transfer='bt709',color_primaries='bt709',color_range='pc')
FRAMES = [dict(best_effort_timestamp=0,duration=33),dict(best_effort_timestamp=33,duration=33)]


@pytest.mark.parametrize('case,reason', [
    ('ok','accepted'), ('stderr','tail_stderr'), ('exit','tail_nonzero_exit'),
    ('empty','tail_empty_frames'), ('invalid_json','tail_invalid_json'),
    ('timeout','tail_TimeoutExpired'), ('oversized','tail_output_limit'),
    ('reverse','nonincreasing_timestamps'), ('no_duration','nonpositive_frame_duration'),
    ('vfr','non_cfr_tail'), ('metadata_exit','metadata_nonzero_exit'),
    ('missing_pts','invalid_or_missing_timing'), ('bad_timebase','invalid_or_missing_timing'),
    ('endpoint_range','endpoint_exceeds_metadata_bound'), ('negative_end','nonpositive_endpoint'),
])
def test_evidence_preserves_original_probe_and_filters_private_text(tmp_path,monkeypatch,case,reason):
    source=tmp_path/'synthetic.mkv';source.write_bytes(b'synthetic fixture')
    output=tmp_path/'evidence.json';calls=[]
    secret=b'/private/user-material/customer.mov token=never-serialize'
    def run(command,**options):
        calls.append(command)
        if '-show_frames' not in command:
            assert options['timeout']==30
            return SimpleNamespace(returncode=4 if case=='metadata_exit' else 0,
                stderr=secret.decode() if case=='metadata_exit' else '',
                stdout=json.dumps(dict(streams=[dict(STREAM,tags={'private':secret.decode()},
                    **({'time_base':'invalid '+secret.decode()} if case=='bad_timebase' else {}),
                    **({'start_pts':100} if case=='negative_end' else {}))],
                                       format=dict(duration='0.066',filename=secret.decode()))))
        assert options['timeout']==10
        if case=='timeout':raise subprocess.TimeoutExpired(command,10,stderr=secret)
        frames=[dict(f) for f in FRAMES]
        if case=='empty':frames=[]
        if case=='reverse':frames.reverse()
        if case=='no_duration':frames[-1].pop('duration')
        if case=='vfr':frames[-1]['best_effort_timestamp']=60
        if case=='missing_pts':frames[-1].pop('best_effort_timestamp')
        if case=='endpoint_range':
            for frame in frames:frame['best_effort_timestamp']+=2000
        data=json.dumps(dict(frames=frames,private=secret.decode())).encode()
        if case=='invalid_json':data=b'not json '+secret
        if case=='oversized':data=b' '*(LIMIT+1)
        options['stdout'].write(data)
        return SimpleNamespace(returncode=3 if case=='exit' else 0,
                               stderr=b'Error while decoding '+secret if case=='stderr' else b'')
    monkeypatch.setattr(media,'ffmpeg_available',lambda:True)
    if case=='metadata_exit':
        with pytest.raises(media.MediaError):
            with trace_synthetic_probes(monkeypatch,{'synthetic':source},output):
                media.probe(source,run_process=run)
        assert len(calls)==1
    else:
        baseline=media.probe(source,run_process=run);calls.clear()
        with trace_synthetic_probes(monkeypatch,{'synthetic':source},output):
            actual=media.probe(source,run_process=run)
        assert (actual.duration,actual.decoded_video_end_num,actual.decoded_video_end_den)==(
            baseline.duration,baseline.decoded_video_end_num,baseline.decoded_video_end_den)
        assert len(calls)==(1 if case=='bad_timebase' else 2)
    text=output.read_text();assert secret.decode() not in text and str(tmp_path) not in text
    row=json.loads(text)['probes'][0]
    assert row['endpoint_reason']==reason and row['content_unchanged']
    assert row['before']['sha256']==hashlib.sha256(source.read_bytes()).hexdigest()
    if case=='stderr':assert row['processes'][1]['stderr_classes']==['decoder_error']


def test_unknown_source_is_not_read_or_captured(tmp_path,monkeypatch):
    source=tmp_path/'outside';source.write_bytes(b'private media bytes')
    sentinel=object();calls=[]
    monkeypatch.setattr(media,'_probe',lambda *a,**kw:calls.append(a) or sentinel)
    output=tmp_path/'evidence.json'
    with trace_synthetic_probes(monkeypatch,{'fixture':tmp_path/'allowed'},output):
        assert media.probe(source) is sentinel
    record=json.loads(output.read_text());assert record['probes']==[] and record['unrecorded_sources']==1
    assert len(calls)==1 and 'private media bytes' not in output.read_text()


def test_evidence_write_error_preserves_original_failure(tmp_path,monkeypatch):
    output=tmp_path/'is-a-file';output.write_bytes(b'not a folder')
    with pytest.raises(ValueError,match='original boundary') as caught:
        with trace_synthetic_probes(monkeypatch,{},output/'evidence.json'):
            raise ValueError('original boundary')
    assert caught.value.__notes__==['Synthetic endpoint evidence write failed: FileExistsError']
