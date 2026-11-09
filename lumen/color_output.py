"""Explicit SDR delivery contracts. Higher bit depth never implies HDR.

The two profiles share bounded RGB16 graded caches, not final compositors.
Unverified 8-bit graphics/effect branches fail closed for high-bit delivery.
"""
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ExportProfile:
    key: str
    suffixes: tuple[str, ...]
    pixel_format: str
    high_bit: bool


H264_8BIT = ExportProfile('h264_8bit', ('.mp4', '.mov'), 'yuv420p', False)
PRORES_10BIT = ExportProfile('prores_422_hq_10bit', ('.mov',), 'yuv422p10le', True)
EXPORT_PROFILES = {p.key: p for p in (H264_8BIT, PRORES_10BIT)}


def validate_export_profile(project, profile='h264_8bit'):
    if not isinstance(profile, str) or profile not in EXPORT_PROFILES:
        raise ValueError('Unknown export profile. Choose H.264 8-bit or ProRes 422 HQ 10-bit SDR.')
    selected = EXPORT_PROFILES[profile]
    if selected.high_bit:
        if any(o.enabled and Path(o.asset_path).suffix.lower() != '.png'
               for o in getattr(project, 'overlays', [])):
            from .highbit_stickers import STATIC_PNG_ERROR
            raise ValueError(STATIC_PNG_ERROR)
        if any(e.enabled and e.amount > 0 and e.kind not in ('blur', 'vignette') for c in project.clips if c.track < 2
               for e in getattr(c, 'effects', [])):
            from .highbit_effects import UNSUPPORTED_EFFECT_ERROR
            raise ValueError(UNSUPPORTED_EFFECT_ERROR)
    return selected
