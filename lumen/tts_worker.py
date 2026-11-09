"""Private process entry for optional sherpa-onnx. No network or downloads."""
import json
import math
from pathlib import Path
import sys
import wave

from .tts import RUNTIME_VERSION, TTSConfig, _validate_controls


def main(argv=None):
    request_path, response_path = (argv or sys.argv[1:])
    response = Path(response_path)
    try:
        try:
            import sherpa_onnx
            import numpy as np
        except ImportError:
            response.write_text(json.dumps({'error': 'Optional local TTS runtime is missing. Install sherpa-onnx in this Python environment; no download is performed by the app.'}), encoding='utf-8')
            return 2
        if sherpa_onnx.__version__ != RUNTIME_VERSION:
            raise ValueError('Unverified optional runtime version')
        data = json.loads(Path(request_path).read_text(encoding='utf-8'))
        pause = data.get('silence_scale', 0.2)
        if isinstance(pause, bool) or not isinstance(pause, (int, float)) or not math.isfinite(pause) or not 0.2 <= pause <= 1.0:
            raise ValueError('Invalid pause preservation')
        pack = _validate_controls(data['text'], TTSConfig(
            data['model_dir'], data['voice_id'], data['speed'], data['threads'],
            data['pack_version'], pause))
        if data.get('backend', pack.backend) != pack.backend:
            raise ValueError('Backend does not match the selected pack')
        root = Path(data['model_dir'])
        path = lambda name: str(root / name)
        if pack.backend == 'kokoro':
            native = sherpa_onnx.OfflineTtsKokoroModelConfig(
                model=path(pack.model_asset), voices=path(pack.voice_asset), tokens=path('tokens.txt'),
                data_dir=path('espeak-ng-data'),
                lexicon=','.join(path(n) for n in pack.lexicons))
            model = sherpa_onnx.OfflineTtsModelConfig(kokoro=native, num_threads=data['threads'], debug=False, provider='cpu')
        elif pack.backend == 'melo':
            native = sherpa_onnx.OfflineTtsVitsModelConfig(
                model=path(pack.model_asset), tokens=path('tokens.txt'),
                lexicon=','.join(path(n) for n in pack.lexicons),
                noise_scale=0.667, noise_scale_w=0.8, length_scale=1.0)
            model = sherpa_onnx.OfflineTtsModelConfig(vits=native, num_threads=2, debug=False, provider='cpu')
        else:
            raise ValueError('Unsupported local speech backend')
        config = sherpa_onnx.OfflineTtsConfig(
            model=model,
            rule_fsts=','.join(path(n) for n in pack.rule_fsts),
            max_num_sentences=pack.max_num_sentences, silence_scale=pause)
        if not config.validate():
            raise ValueError('Invalid local engine configuration')
        engine = sherpa_onnx.OfflineTts(config)
        if engine.num_speakers != pack.bank_size:
            raise ValueError('The actual voice bank does not match the explicit pack version')
        audio = engine.generate(data['text'], sid=data['voice_id'], speed=data['speed'])
        samples = np.asarray(audio.samples)
        if (type(audio.sample_rate) is not int or audio.sample_rate != pack.sample_rate
                or samples.ndim != 1 or not samples.size or not np.isfinite(samples).all()
                or float(np.max(np.abs(samples))) < 0.0001):
            raise ValueError('No usable speech generated')
        pcm = (np.clip(samples, -1.0, 1.0) * 32767).astype('<i2')
        with wave.open(data['output'], 'wb') as output:
            output.setnchannels(1)
            output.setsampwidth(2)
            output.setframerate(audio.sample_rate)
            output.writeframes(pcm.tobytes())
        response.write_text(json.dumps({'engine_version': sherpa_onnx.__version__}), encoding='utf-8')
        return 0
    except Exception:
        response.write_text(json.dumps({'error': 'Local speech synthesis failed: incompatible runtime, model pack, or text. Use the complete documented pack matching the selected version.'}), encoding='utf-8')
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
