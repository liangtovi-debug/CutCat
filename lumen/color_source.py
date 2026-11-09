"""Strict source color metadata interpretation, shared by import and render.

Unknown tags are stated assumptions, never camera-gamut or HDR transforms.
Transfer equations live in color.py; YUV matrix/range decoding uses libzimg.
"""
from __future__ import annotations

from dataclasses import dataclass

UNKNOWN_TAGS = ("unknown", "unspecified", "reserved")
APPLE_LOG_ENCODING = "apple_log_bt2020"
INPUT_ENCODINGS = ("rec709", "srgb", "linear_rec709", APPLE_LOG_ENCODING)
INPUT_LABELS = {"rec709": "Rec.709", "srgb": "sRGB", "linear_rec709": "Linear Rec.709", APPLE_LOG_ENCODING: "Apple Log 1 / BT.2020 (experimental)"}
TRANSFER_ENCODINGS = {"bt709": "rec709", "iec61966-2-1": "srgb", "linear": "linear_rec709"}
MATRICES = {"bt709": "709", "bt470bg": "470bg", "smpte170m": "170m"}


def matching_input_encoding(transfer):
    """Known, supported source transfer assignment, or None if untagged."""
    if transfer in UNKNOWN_TAGS:
        return None
    if not isinstance(transfer, str) or transfer not in TRANSFER_ENCODINGS:
        raise ValueError(f"Only SDR Rec.709-primary inputs are supported; color_transfer={transfer}. "
                         "Convert unsupported Log/HDR to SDR Rec.709 before importing.")
    return TRANSFER_ENCODINGS[transfer]


def _tag(stream, name):
    value = stream.get(name, "unknown")
    if not isinstance(value, str) or not value or any(ord(c) < 32 for c in value):
        raise ValueError(f"Malformed source {name} metadata")
    return value


@dataclass(frozen=True)
class SourceColor:
    pixel_format: str
    color_space: str
    color_transfer: str
    color_primaries: str
    color_range: str
    is_rgb: bool
    matrix: str
    range: str
    input_encoding: str = "rec709"


def source_color_from_stream(stream, input_encoding="rec709"):
    """Validate a real video stream and resolve only supported matrix/range tags."""
    if not isinstance(stream, dict):
        raise ValueError("Malformed video color metadata")
    pixel_format = _tag(stream, "pix_fmt")
    matrix, transfer, primaries, range_tag = (
        _tag(stream, key) for key in ("color_space", "color_transfer", "color_primaries", "color_range"))
    side_data = stream.get("side_data_list", [])
    if not isinstance(side_data, list) or any(not isinstance(item, dict) for item in side_data):
        raise ValueError("Malformed video side metadata")
    if any(any(word in str(item).lower() for word in
               ("mastering", "content light", "dovi", "dolby vision", "hdr10", "hdr dynamic"))
           for item in side_data):
        raise ValueError("HDR metadata is unsupported. Convert to SDR Rec.709 before importing.")
    if input_encoding == APPLE_LOG_ENCODING:
        return _apple_log_source(pixel_format, matrix, transfer, primaries, range_tag)
    if input_encoding not in INPUT_ENCODINGS:
        raise ValueError("Unsupported input encoding")
    matching_input_encoding(transfer)
    if primaries not in (*UNKNOWN_TAGS, "bt709"):
        raise ValueError(f"Only SDR Rec.709 primaries are supported; color_primaries={primaries}. "
                         "Convert this source before importing; an input encoding assignment cannot change its gamut.")
    if matrix not in (*UNKNOWN_TAGS, "gbr", "rgb", *MATRICES):
        raise ValueError(f"Only SDR Rec.709-primary sources with a supported matrix are allowed; color_space={matrix}. "
                         "Convert this source before importing.")
    if range_tag not in (*UNKNOWN_TAGS, "pc", "tv"):
        raise ValueError(f"Unsupported source color_range={range_tag}")
    is_rgb = pixel_format.startswith(("rgb", "bgr", "gbr", "argb", "abgr", "rgba", "bgra", "pal"))
    if not is_rgb and not pixel_format.startswith(("yuv", "yuva", "nv", "p0", "p2", "p4", "gray", "ya", "uyvy", "yuyv", "yvyu", "vuy", "xv")):
        raise ValueError(f"Unsupported source pixel format: {pixel_format}")
    if not is_rgb and matrix in ("gbr", "rgb"):
        raise ValueError("YUV source has contradictory RGB matrix metadata")
    if matrix in ("bt470bg", "smpte170m") and primaries != "bt709":
        raise ValueError("BT.601-matrix sources require explicitly tagged BT.709 primaries; "
                         "no unknown or BT.601 gamut conversion is inferred.")
    if pixel_format.startswith("yuvj") and range_tag == "tv":
        raise ValueError("Full-range yuvj pixel format conflicts with limited-range metadata")
    if is_rgb and range_tag == "tv":
        # zimg's RGB matrix path does not expand limited RGB reliably on the
        # supported FFmpeg build. Do not silently retain compressed code range.
        raise ValueError("Limited-range RGB input is unsupported. Convert to full-range RGB "
                         "or correctly tagged SDR YUV before importing.")
    source_range = ("full" if range_tag == "pc" else "limited" if range_tag == "tv"
                    else "full" if is_rgb or pixel_format.startswith("yuvj") else "limited")
    return SourceColor(pixel_format, matrix, transfer, primaries, range_tag, is_rgb,
                       "gbr" if is_rgb else MATRICES.get(matrix, "709"), source_range)


def _apple_log_source(pixel_format, matrix, transfer, primaries, range_tag):
    """A deliberately narrow explicit profile, not camera/filename detection.

    The decoder must expose original Log code values. Unknown transfer is
    required: tagged SDR/PQ/HLG is a conflict, not permission to override it.
    Primaries, YUV matrix and code range may never be inferred for this path.
    """
    if transfer not in UNKNOWN_TAGS or primaries != "bt2020":
        raise ValueError("Experimental Apple Log 1 requires unknown transfer and explicit BT.2020 primaries; conflicting SDR/HDR tags are not overridden.")
    rgb_formats = {"rgb48le", "rgb48be", "gbrp16le", "gbrp16be"}
    yuv_formats = {"yuv420p10le", "yuv422p10le", "yuv444p10le"}
    if pixel_format in rgb_formats:
        if range_tag != "pc" or matrix not in (*UNKNOWN_TAGS, "rgb", "gbr"):
            raise ValueError("Experimental Apple Log RGB requires full-range RGB16 and RGB matrix metadata.")
        return SourceColor(pixel_format, matrix, transfer, primaries, range_tag,
                           True, "gbr", "full", APPLE_LOG_ENCODING)
    if pixel_format in yuv_formats:
        if matrix != "bt2020nc" or range_tag not in ("pc", "tv"):
            raise ValueError("Experimental Apple Log YUV requires 10-bit BT.2020 non-constant-luminance matrix and explicit full/limited range.")
        return SourceColor(pixel_format, matrix, transfer, primaries, range_tag,
                           False, "2020_ncl", "full" if range_tag == "pc" else "limited",
                           APPLE_LOG_ENCODING)
    raise ValueError("Experimental Apple Log supports only full-range RGB16 or explicitly tagged 10-bit YUV; this pixel format is unsupported.")


def validate_input_assignment(source, selected):
    if selected not in INPUT_ENCODINGS:
        raise ValueError("Input encoding must be rec709, srgb, linear_rec709 or apple_log_bt2020")
    source_encoding = getattr(source, "input_encoding", "rec709")
    if selected == APPLE_LOG_ENCODING:
        if source_encoding != APPLE_LOG_ENCODING:
            raise ValueError("Import this source explicitly as Apple Log 1 / BT.2020 before assigning experimental Log normalization.")
        if source.color_transfer not in UNKNOWN_TAGS or source.color_primaries != "bt2020":
            raise ValueError("Experimental Apple Log 1 requires unknown transfer and explicit BT.2020 primaries; conflicting SDR/HDR tags are not overridden.")
        return
    if source_encoding == APPLE_LOG_ENCODING:
        raise ValueError("This source was explicitly imported as Apple Log 1 / BT.2020; keep its Apple Log input interpretation.")
    known = matching_input_encoding(source.color_transfer)
    if known is not None and known != selected:
        raise ValueError(f"Source transfer is {INPUT_LABELS[known]}, but the clip input is {INPUT_LABELS[selected]}. "
                         f"Select {INPUT_LABELS[known]} in Input encoding before preview or export.")


def input_encoding_status(media, selected="rec709"):
    """Concise inspector copy using retained tags; rendering always re-probes."""
    try:
        validate_input_assignment(media, selected)
    except ValueError as exc:
        return str(exc)
    if selected == APPLE_LOG_ENCODING:
        return ("Experimental Apple Log 1 / BT.2020 → CutCat SDR shoulder v1 → Rec.709. "
                "Synthetic decoder validation only; not Apple Log 2, Apple LUT or Resolve matching.")
    known = matching_input_encoding(media.color_transfer)
    transfer = (f"Source transfer: {INPUT_LABELS[known]}." if known else
                f"Source transfer untagged; assigned {INPUT_LABELS[selected]}.")
    primaries = ("Rec.709 primaries assumed." if media.color_primaries in UNKNOWN_TAGS else
                 f"Primaries: {media.color_primaries}.")
    return f"{transfer} {primaries} Output: SDR Rec.709."
