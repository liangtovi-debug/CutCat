"""Explicit, offline-only real-pack QA. Never downloads or installs assets.

Run with the existing optional runtime plus test-only onnx==1.18.0 installed:
  python scripts/validate_voice_catalog.py /path/to/test-cache
Pack folders must already be extracted there. Reports/WAVs go under the cache.
The native sweep reuses one engine; selected samples also exercise the app's
isolated, cancelable adapter. No claim of human listening or native OS testing.
"""
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
import threading
import time
import wave

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import numpy as np
import onnx
import sherpa_onnx
from lumen import tts
from lumen.voice_catalog import PACKS


def sha(path):
    return hashlib.file_digest(path.open('rb'), 'sha256').hexdigest()


def measure(samples, rate):
    samples = np.asarray(samples, dtype=np.float32)
    assert samples.size and np.isfinite(samples).all()
    peak = float(np.max(np.abs(samples)))
    rms = float(np.sqrt(np.mean(np.square(samples))))
    assert peak > .0001 and rms > .00001
    return dict(frames=len(samples), sample_rate=rate, duration=len(samples)/rate,
                peak=peak, rms=rms, clipped_samples=int(np.count_nonzero(np.abs(samples) >= 1)))


def main():
    root = Path(sys.argv[1]).resolve(strict=True)
    destination = root / 'voice-browser-validation'; destination.mkdir(exist_ok=True)
    report = dict(created_utc=datetime.now(timezone.utc).isoformat(), runtime=sherpa_onnx.__version__,
                  numpy=np.__version__, onnx_test_only=onnx.__version__, platform=sys.platform,
                  human_listening=False, platform_scope='Linux x86_64 CPU; not native Windows/macOS',
                  network_scope='No runtime network/download calls in adapter. No packet trace claim.', packs={}, adapter_samples=[])
    assert sherpa_onnx.__version__ == tts.RUNTIME_VERSION
    adapter_only = '--adapter-only' in sys.argv[2:]
    if adapter_only:
        report = json.loads((destination/'validation.json').read_text(encoding='utf-8'))
        report['adapter_samples'] = []
    utterance = 'Hello, this is a local voice. 你好，这是本地语音。'
    # This runner audits Kokoro's external voice-bank metadata only.
    # Melo uses scripts/verify_melo_setup_integration.py (embedded single voice).
    for version, pack in ([] if adapter_only else PACKS.items()):
        if pack.backend != 'kokoro':
            continue
        model = root / pack.folder_name
        assert tts.validate_pack(str(model), version) == (str(model), version)
        metadata = {entry.key: entry.value for entry in onnx.load(model/'model.onnx').metadata_props}
        names = metadata['speaker_names'].split(',')
        assert [names[v.voice_id] for v in pack.voices] == [v.name for v in pack.voices]
        assert int(metadata['n_speakers']) == pack.bank_size
        style = [int(n) for n in metadata['style_dim'].split(',')]
        assert (model/'voices.bin').stat().st_size == pack.bank_size*np.prod(style)*4
        archive = root / (pack.folder_name + '.tar.bz2')
        assert sha(archive) == pack.archive_sha256
        assets = tts._assets(model, None)
        p = lambda name: str(model/name)
        config = sherpa_onnx.OfflineTtsConfig(model=sherpa_onnx.OfflineTtsModelConfig(
            kokoro=sherpa_onnx.OfflineTtsKokoroModelConfig(model=p('model.onnx'), voices=p('voices.bin'),
                tokens=p('tokens.txt'), data_dir=p('espeak-ng-data'), lexicon=','.join(p(n) for n in pack.lexicons)),
            num_threads=2, debug=False, provider='cpu'), rule_fsts=','.join(p(n) for n in pack.rule_fsts), max_num_sentences=1)
        assert config.validate()
        engine = sherpa_onnx.OfflineTts(config)
        assert engine.num_speakers == pack.bank_size
        sweep=[]
        for voice in pack.voices:
            start = time.monotonic()
            audio = engine.generate(utterance, sid=voice.voice_id, speed=1.0)
            stats = measure(audio.samples, audio.sample_rate)
            stats.update(voice_id=voice.voice_id, name=voice.name, inference_seconds=time.monotonic()-start,
                         raw_float32_sha256=hashlib.sha256(np.asarray(audio.samples,dtype='<f4').tobytes()).hexdigest())
            sweep.append(stats)
            print(version, voice.voice_id, voice.name, f"{stats['duration']:.3f}s", flush=True)
        del engine
        assert len({s['raw_float32_sha256'] for s in sweep}) == len(sweep)
        assert assets == tts._assets(model, None)
        report['packs'][version] = dict(official_archive=pack.download_url, archive_sha256=sha(archive),
            archive_bytes=archive.stat().st_size, metadata=metadata, bank_size=pack.bank_size,
            exposed_voices=len(pack.voices), pack_files=[dict(path=n,sha256=h,bytes=(model/n).stat().st_size) for n,h in assets],
            sweep_text=utterance,sweep=sweep,distinct_output_count=len(sweep),
            source_license='Apache-2.0 top-level weights/pack; eSpeak GPL component and dictionary/lexicon/FST rights require separate review. No redistribution clearance.')
        (destination/'validation.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    cases=[('v1.1',0,'English voice preview, generated locally on this computer.'),
           ('v1.1',2,'English voice preview, generated locally on this computer.'),
           ('v1.1',3,'这是本地语音试听，可以选择不同的声音。'),
           ('v1.1',58,'这是本地语音试听，可以选择不同的声音。'),
           ('v1.0',11,'The original local English voice still works.'),
           ('v1.0',45,'原来的中文音色仍然可以正常使用。')]
    for version,sid,text in cases:
        path=destination/(('final-' if adapter_only else '')+f'{version}-voice-{sid}.wav')
        if path.exists(): raise ValueError(f'Refusing to overwrite {path}')
        result=tts.synthesize(text,tts.TTSConfig(str(root/PACKS[version].folder_name),sid,pack_version=version),path)
        with wave.open(str(path),'rb') as stream:
            samples=np.frombuffer(stream.readframes(stream.getnframes()),dtype='<i2').astype(np.float32)/32767
        report['adapter_samples'].append(asdict(result)|{'signal':measure(samples,result.sample_rate)})
        print('adapter',version,sid,result.duration,flush=True)
    event=threading.Event();timer=threading.Timer(.3,event.set);start=time.monotonic()
    command = tts._worker_command
    launch_requested=[]
    def arm_cancel(request,response):
        launch_requested.append(True);timer.start()
        return command(request,response)
    tts._worker_command = arm_cancel
    cancel_path=destination/'canceled.wav'
    try:
        tts.synthesize('This operation will be canceled.',tts.TTSConfig(str(root/PACKS['v1.1'].folder_name),pack_version='v1.1'),cancel_path,cancel=event)
        raise AssertionError('Cancellation should raise')
    except tts.TTSCancelled:
        report['cancellation']={'seconds':time.monotonic()-start,'worker_launch_requested':bool(launch_requested),'published_output':cancel_path.exists(),
                                'leftover_temporaries':len(list(destination.glob('.lumen-tts-*')))}
        assert not cancel_path.exists() and not list(destination.glob('.lumen-tts-*'))
    finally:timer.cancel();tts._worker_command=command
    source_root=Path(__file__).resolve().parents[1]
    report['final_adapter_code_sha256']={name:sha(source_root/name) for name in ('lumen/tts.py','lumen/tts_worker.py','lumen/voice_catalog.py')}
    report['final_adapter_validation_utc']=datetime.now(timezone.utc).isoformat()
    (destination/'validation.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print('VALIDATION COMPLETE',destination/'validation.json',flush=True)


if __name__=='__main__':main()
