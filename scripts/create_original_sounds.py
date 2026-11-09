"""Regenerate CutCat's original procedural starter sounds, without downloads.

All waveforms are synthesized from sine oscillators and seeded PCG64 noise.
There are no recordings, samples, model weights, or third-party sound assets.
Run with the project's pinned NumPy environment for byte-identical output:
    python scripts/create_original_sounds.py
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import json
import math
from pathlib import Path
import wave

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
SAMPLE_RATE = 48_000
LICENSE = 'CutCat original / CC0-1.0'


@dataclass(frozen=True)
class SoundSpec:
    id: str
    name_en: str
    name_zh: str
    category: str
    tags: tuple[str, ...]
    duration: float
    peak: float = 0.4


SOUNDS = (
    SoundSpec('ui_soft_click', 'Soft click', '轻柔点击', 'ui', ('click', 'button', '点击', '按钮'), 0.10, 0.28),
    SoundSpec('ui_pop', 'Bubble pop', '气泡弹出', 'ui', ('pop', 'bubble', '气泡', '弹出'), 0.19, 0.35),
    SoundSpec('ui_toggle', 'Toggle switch', '切换开关', 'ui', ('toggle', 'switch', '切换', '开关'), 0.16, 0.30),
    SoundSpec('ui_confirm', 'Confirm tap', '确认轻音', 'ui', ('confirm', 'tap', '确认', '轻触'), 0.22, 0.32),
    SoundSpec('ui_type_tick', 'Typing tick', '键盘轻敲', 'ui', ('typing', 'keyboard', '打字', '键盘'), 0.09, 0.26),
    SoundSpec('transition_air_swipe', 'Air swipe', '空气掠过', 'transitions', ('whoosh', 'swipe', '转场', '掠过'), 0.65),
    SoundSpec('transition_riser', 'Rising tension', '渐强上升', 'transitions', ('riser', 'build', '上升', '渐强'), 1.80, 0.42),
    SoundSpec('transition_reverse', 'Reverse swell', '倒吸渐起', 'transitions', ('reverse', 'swell', '倒吸', '转场'), 1.40),
    SoundSpec('transition_sparkle', 'Sparkle trail', '闪光轨迹', 'transitions', ('sparkle', 'magic', '闪光', '魔法'), 1.20, 0.38),
    SoundSpec('transition_swoosh_down', 'Falling swoosh', '下落呼啸', 'transitions', ('down', 'swoosh', '下落', '呼啸'), 0.80),
    SoundSpec('impact_soft_thud', 'Soft thud', '柔和闷击', 'impacts', ('thud', 'soft', '闷击', '柔和'), 0.65, 0.42),
    SoundSpec('impact_cinematic', 'Cinematic hit', '电影重击', 'impacts', ('cinematic', 'bass', '重击', '低音'), 1.60, 0.48),
    SoundSpec('impact_wood_knock', 'Wooden knock', '木质敲击', 'impacts', ('wood', 'knock', '木质', '敲击'), 0.32, 0.40),
    SoundSpec('impact_digital', 'Digital hit', '电子冲击', 'impacts', ('digital', 'glitch', '电子', '冲击'), 0.55, 0.42),
    SoundSpec('impact_rubber_bounce', 'Rubber bounce', '橡皮弹跳', 'impacts', ('bounce', 'rubber', '弹跳', '橡皮'), 0.90, 0.38),
    SoundSpec('notification_chime', 'Gentle chime', '轻柔提示', 'notifications', ('chime', 'gentle', '提示', '轻柔'), 0.80, 0.34),
    SoundSpec('notification_success', 'Success melody', '成功旋律', 'notifications', ('success', 'complete', '成功', '完成'), 1.40, 0.38),
    SoundSpec('notification_warning', 'Soft warning', '温和提醒', 'notifications', ('warning', 'alert', '提醒', '警示'), 0.75, 0.34),
    SoundSpec('notification_message', 'New message', '消息到达', 'notifications', ('message', 'ping', '消息', '提示音'), 0.70, 0.32),
    SoundSpec('notification_bell', 'Glass bell', '玻璃铃声', 'notifications', ('bell', 'glass', '铃声', '玻璃'), 2.20, 0.38),
    SoundSpec('atmosphere_breeze', 'Synthetic breeze', '合成微风', 'atmosphere', ('wind', 'breeze', '风声', '氛围'), 6.00, 0.24),
    SoundSpec('atmosphere_rain', 'Synthetic light rain', '合成细雨', 'atmosphere', ('rain', 'water', '雨声', '水滴'), 6.00, 0.24),
    SoundSpec('atmosphere_space', 'Space drone', '太空氛围', 'atmosphere', ('space', 'drone', '太空', '氛围'), 8.00, 0.27),
    SoundSpec('atmosphere_room', 'Quiet room texture', '静室底噪', 'atmosphere', ('room', 'texture', '房间', '底噪'), 6.00, 0.16),
)


def _smooth(values: np.ndarray, width: int) -> np.ndarray:
    """Symmetric boxcar low-pass, without a platform-dependent FFT."""
    return np.convolve(values, np.ones(width, dtype=np.float64) / width, mode='same')


def _noise(rng: np.random.Generator, count: int, width: int = 1) -> np.ndarray:
    values = rng.uniform(-1.0, 1.0, count)
    if width > 1:
        values = _smooth(_smooth(values, width), width)
    return values / max(float(np.max(np.abs(values))), 1e-12)


def _sine(t: np.ndarray, frequency: float, phase: float = 0.0) -> np.ndarray:
    return np.sin(2.0 * np.pi * frequency * t + phase)


def _chirp(t: np.ndarray, start: float, end: float, duration: float) -> np.ndarray:
    return np.sin(2.0 * np.pi * (start * t + (end - start) * t * t / (2.0 * duration)))


def _voice(t: np.ndarray, start: float, frequency: float, length: float,
           decay: float = 0.15, brightness: float = 0.18) -> np.ndarray:
    """A gently attacked, decaying harmonic note with a tapered release."""
    u = np.maximum(t - start, 0.0)
    envelope = (1.0 - np.exp(-u / 0.003)) * np.exp(-u / decay)
    envelope *= np.clip((length - u) / min(0.03, length / 3.0), 0.0, 1.0)
    envelope *= (t >= start) & (t < start + length)
    partials = _sine(u, frequency) + brightness * _sine(u, frequency * 2.0)
    return partials * envelope


def _echo(values: np.ndarray, delay: float, gain: float) -> np.ndarray:
    offset = round(delay * SAMPLE_RATE)
    result = values.copy()
    if 0 < offset < len(values):
        result[offset:] += values[:-offset] * gain
    return result


def synthesize(spec: SoundSpec) -> np.ndarray:
    """Return finite float64 mono samples before final headroom and edge shaping.

    Each ID seeds an independent stream. Adding or reordering another asset does
    not change existing sound bytes. Frequencies remain below the Nyquist limit.
    """
    seed = int.from_bytes(hashlib.sha256(spec.id.encode('ascii')).digest()[:8], 'little')
    rng = np.random.Generator(np.random.PCG64(seed))
    t = np.arange(round(spec.duration * SAMPLE_RATE), dtype=np.float64) / SAMPLE_RATE
    n = len(t)
    d = spec.duration
    match spec.id:
        case 'ui_soft_click':
            return (0.7 * _sine(t, 1150) + 0.3 * _noise(rng, n, 3)) * np.exp(-t / 0.012)
        case 'ui_pop':
            return _chirp(t, 720, 120, d) * np.exp(-t / 0.040)
        case 'ui_toggle':
            return _voice(t, 0, 480, 0.07, 0.018) + 0.8 * _voice(t, 0.065, 790, 0.09, 0.025)
        case 'ui_confirm':
            return _voice(t, 0, 1046.5, d, 0.045) + 0.35 * _voice(t, 0.035, 1568, d - 0.035, 0.045)
        case 'ui_type_tick':
            return (_noise(rng, n, 2) * 0.65 + _sine(t, 1950) * 0.35) * np.exp(-t / 0.006)
        case 'transition_air_swipe':
            air = _noise(rng, n, 7) - 0.7 * _noise(rng, n, 55)
            return air * np.sin(np.pi * t / d) ** 2 * (0.7 + 0.3 * t / d)
        case 'transition_riser':
            body = 0.45 * _chirp(t, 90, 1200, d) + 0.55 * _noise(rng, n, 9)
            return body * (t / d) ** 1.4 * (0.85 + 0.15 * _sine(t, 9))
        case 'transition_reverse':
            body = _sine(t, 294) + 0.42 * _sine(t, 440) + 0.2 * _noise(rng, n, 35)
            return body * np.exp((t - d) / 0.25)
        case 'transition_sparkle':
            result = np.zeros(n)
            for index, frequency in enumerate((1046.5, 1318.5, 1568, 2093, 2637, 3136)):
                result += _voice(t, index * 0.105, frequency, 0.55, 0.13, 0.06) * (1 - index * 0.07)
            return _echo(result, 0.14, 0.22)
        case 'transition_swoosh_down':
            return (_chirp(t, 950, 65, d) * 0.5 + _noise(rng, n, 13) * 0.5) * np.sin(np.pi * t / d) ** 1.5
        case 'impact_soft_thud':
            return (_chirp(t, 130, 45, d) + 0.2 * _noise(rng, n, 40)) * np.exp(-t / 0.10)
        case 'impact_cinematic':
            bass = _sine(t, 48) + 0.6 * _sine(t, 73) + 0.2 * _sine(t, 110)
            return bass * np.exp(-t / 0.30) + _noise(rng, n, 8) * np.exp(-t / 0.05) * 0.5
        case 'impact_wood_knock':
            return (0.6 * _sine(t, 580) + 0.25 * _sine(t, 973) + 0.15 * _noise(rng, n, 3)) * np.exp(-t / 0.028)
        case 'impact_digital':
            body = _sine(t, 145) * _sine(t, 580) + 0.2 * _chirp(t, 1600, 190, d)
            return body * np.exp(-t / 0.10) * (0.6 + 0.4 * _sine(t, 27) ** 2)
        case 'impact_rubber_bounce':
            result = np.zeros(n)
            for start, frequency, gain in ((0.0, 180, 1.0), (0.30, 225, 0.65), (0.52, 285, 0.40), (0.69, 360, 0.23)):
                u = np.maximum(t - start, 0.0)
                result += gain * _chirp(u, frequency * 1.5, frequency * 0.4, 0.22) * np.exp(-u / 0.04) * (t >= start) * (1 - np.exp(-u / 0.003))
            return result
        case 'notification_chime':
            return _voice(t, 0, 880, d, 0.16, 0.05) + 0.50 * _voice(t, 0.10, 1320, d - 0.10, 0.20, 0.05)
        case 'notification_success':
            result = np.zeros(n)
            for start, frequency in ((0.0, 523.25), (0.16, 659.25), (0.32, 783.99), (0.52, 1046.5)):
                result += _voice(t, start, frequency, min(0.8, d - start), 0.20, 0.12)
            return _echo(result, 0.11, 0.13)
        case 'notification_warning':
            return _voice(t, 0, 440, 0.28, 0.09, 0.05) + _voice(t, 0.32, 392, 0.36, 0.12, 0.05)
        case 'notification_message':
            return _voice(t, 0, 1174.66, 0.35, 0.08, 0.05) + 0.8 * _voice(t, 0.13, 1567.98, 0.56, 0.13, 0.04)
        case 'notification_bell':
            result = np.zeros(n)
            for ratio, gain, decay in ((1.0, 1.0, 0.55), (2.756, 0.35, 0.25), (5.404, 0.12, 0.12)):
                result += gain * _voice(t, 0, 660 * ratio, d, decay, 0.0)
            return result
        case 'atmosphere_breeze':
            air = _noise(rng, n, 85) + 0.18 * _noise(rng, n, 13)
            return air * (0.58 + 0.22 * _sine(t, 0.23) + 0.12 * _sine(t, 0.61, 1.2))
        case 'atmosphere_rain':
            noise = _noise(rng, n)
            result = (noise - _smooth(noise, 35)) * 0.13
            for start, frequency, gain in zip(rng.uniform(0.05, d - 0.15, 52), rng.uniform(1800, 4800, 52), rng.uniform(0.08, 0.3, 52)):
                result += gain * _voice(t, float(start), float(frequency), 0.11, 0.009, 0.0)
            return result * (0.8 + 0.2 * _sine(t, 0.17))
        case 'atmosphere_space':
            drone = _sine(t, 110) + 0.45 * _sine(t, 164.81) + 0.25 * _sine(t, 220.7)
            shimmer = _sine(t, 440.5) * (0.5 + 0.5 * _sine(t, 0.19))
            return drone * (0.65 + 0.12 * _sine(t, 0.13)) + 0.12 * shimmer + 0.12 * _noise(rng, n, 150)
        case 'atmosphere_room':
            return _noise(rng, n, 27) * 0.45 + _noise(rng, n, 200) * 0.35 + 0.08 * _sine(t, 120) + 0.025 * _sine(t, 240)
        case _:
            raise ValueError(f'Unknown sound recipe: {spec.id}')


def pcm16(spec: SoundSpec) -> np.ndarray:
    """Remove DC, taper both ends, reserve headroom, quantize little-endian PCM."""
    values = synthesize(spec)
    if values.ndim != 1 or len(values) != round(spec.duration * SAMPLE_RATE) or not np.isfinite(values).all():
        raise ValueError(f'Invalid synthesized samples: {spec.id}')
    values = values - np.mean(values)
    fade_seconds = 0.25 if spec.category == 'atmosphere' else 0.008
    count = min(round(SAMPLE_RATE * fade_seconds), len(values) // 2)
    fade = np.sin(np.linspace(0.0, np.pi / 2.0, count)) ** 2
    values[:count] *= fade
    values[-count:] *= fade[::-1]
    maximum = float(np.max(np.abs(values)))
    if not maximum > 0.0 or not 0.0 < spec.peak <= 0.5:
        raise ValueError(f'Invalid peak: {spec.id}')
    values *= spec.peak / maximum
    # Signed 16-bit full scale is 32768; never approach clipping or hard-limit.
    return np.rint(values * 32768.0).astype('<i2')


def generate(destination: Path) -> tuple[Path, ...]:
    """Write 24 WAVs and the v1 catalogue; leave unrelated files untouched."""
    destination.mkdir(parents=True, exist_ok=True)
    entries = []
    paths = []
    for spec in SOUNDS:
        samples = pcm16(spec)
        path = destination / f'{spec.id}.wav'
        with wave.open(str(path), 'wb') as output:
            output.setnchannels(1)
            output.setsampwidth(2)
            output.setframerate(SAMPLE_RATE)
            output.writeframes(samples.tobytes())
        peak = float(np.max(np.abs(samples.astype(np.float64)))) / 32768.0
        entries.append({
            'id': spec.id, 'name_en': spec.name_en, 'name_zh': spec.name_zh,
            'category': spec.category, 'tags': list(spec.tags), 'file': path.name,
            'sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
            'sample_rate': SAMPLE_RATE, 'sample_count': len(samples),
            'peak_dbfs': round(20.0 * math.log10(peak), 6),
            'license': LICENSE, 'origin': 'procedural',
        })
        paths.append(path)
    catalog = destination / 'catalog.json'
    catalog.write_text(json.dumps({'version': 1, 'sounds': entries}, ensure_ascii=False, indent=2) + '\n', encoding='utf-8', newline='\n')
    return (*paths, catalog)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT / 'lumen' / 'assets' / 'sounds')
    args = parser.parse_args()
    for path in generate(args.output):
        print(path)


if __name__ == '__main__':
    main()
