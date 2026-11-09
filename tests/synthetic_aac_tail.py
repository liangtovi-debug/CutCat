"""Give generated MP4 fixtures an explicitly underestimated audio duration.

Only the audio mdhd duration changes. AAC bytes, packet tables, edit lists and
video remain intact, so tail tests do not depend on decoder padding policy.
Never apply this test helper to user media.
"""
from pathlib import Path


def understate_audio_duration(path):
    path = Path(path)
    data = bytearray(path.read_bytes())

    def boxes(start, end):
        while start < end:
            assert end - start >= 8, 'Truncated fixture atom'
            size = int.from_bytes(data[start:start + 4], 'big')
            assert 8 <= size <= end - start, 'Unsupported fixture atom size'
            yield bytes(data[start + 4:start + 8]), start + 8, start + size
            start += size

    moov = [(a, b) for kind, a, b in boxes(0, len(data)) if kind == b'moov']
    assert len(moov) == 1
    changed = 0
    for kind, start, end in boxes(*moov[0]):
        if kind != b'trak':
            continue
        mdia = [(a, b) for kind, a, b in boxes(start, end) if kind == b'mdia']
        assert len(mdia) == 1
        fields = {kind: (a, b) for kind, a, b in boxes(*mdia[0])}
        handler, handler_end = fields[b'hdlr']
        assert handler_end - handler >= 12
        if data[handler + 8:handler + 12] != b'soun':
            continue
        header, header_end = fields[b'mdhd']
        version = data[header]
        assert version in (0, 1)
        scale_pos = header + (12 if version == 0 else 20)
        size = 4 if version == 0 else 8
        duration_pos = scale_pos + 4
        assert duration_pos + size <= header_end
        scale = int.from_bytes(data[scale_pos:scale_pos + 4], 'big')
        duration = int.from_bytes(data[duration_pos:duration_pos + size], 'big')
        # Two AAC-LC frames plus 5 ms remove the priming-sized excess
        # without moving the selected tail span away from actual decoder EOF.
        reduction = 2048 + max(1, scale // 200)
        assert 0 < reduction < duration
        data[duration_pos:duration_pos + size] = (duration - reduction).to_bytes(size, 'big')
        changed += 1
    assert changed == 1, 'Fixture must have exactly one audio track'
    path.write_bytes(data)
