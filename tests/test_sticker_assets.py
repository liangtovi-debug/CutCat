"""Adversarial offline sticker validation and original-asset regression tests."""
from dataclasses import FrozenInstanceError
import hashlib
import json
from pathlib import Path
import shutil
import struct
import threading
import zlib

import pytest
from PySide6.QtGui import QImage

from lumen import sticker_assets as assets


def chunk(kind, body=b''):
    return struct.pack('>I', len(body)) + kind + body + struct.pack('>I', zlib.crc32(kind + body) & 0xffffffff)


def png(width=16, height=16, rgba=(35, 190, 150, 180), depth=8, color=6, extra=(), raw=None, interlace=0):
    header = struct.pack('>IIBBBBB', width, height, depth, color, 0, 0, interlace)
    if raw is None:
        raw = (b'\x00' + bytes(rgba) * width) * height
    return b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', header) + b''.join(extra) + chunk(b'IDAT', zlib.compress(raw)) + chunk(b'IEND')


def manifest(path, frames=('a.png', 'b.png'), frame_duration=3, loop=True, **overrides):
    data = {'version': 1, 'frames': list(frames), 'frame_duration': frame_duration, 'loop': loop}
    data.update(overrides)
    path.write_text(json.dumps(data), encoding='utf-8')
    return path


@pytest.fixture
def pack(tmp_path):
    (tmp_path / 'a.png').write_bytes(png())
    (tmp_path / 'b.png').write_bytes(png(rgba=(220, 130, 25, 230)))
    return manifest(tmp_path / 'sticker.json')


def test_static_is_immutable_absolute_and_hashed(tmp_path):
    source = tmp_path / 'star.PNG'
    source.write_bytes(png())
    asset = assets.load_sticker(source)
    assert (asset.width, asset.height) == (16, 16)
    assert asset.frames == (source.resolve(),)
    assert asset.frame_duration == 1 and asset.loop is True
    assert len(asset.digest) == 64
    assert asset.frame_sha256 == (hashlib.sha256(source.read_bytes()).hexdigest(),)
    with pytest.raises(FrozenInstanceError):
        asset.width = 99
    assert assets.verify_sticker(source, asset.digest.upper()) == asset
    assert assets.read_sticker_frame(asset.frames[0]) == source.read_bytes()


def test_manifest_semantics_and_dependency_listing(pack):
    asset = assets.load_sticker(pack)
    assert (asset.width, asset.height, asset.frame_duration, asset.loop) == (16, 16, 3, True)
    assert asset.frames == (pack.parent / 'a.png', pack.parent / 'b.png')
    assert assets.sticker_input_paths(pack) == (pack, *asset.frames)
    assert assets.sticker_input_paths(asset.frames[0]) == (asset.frames[0],)


def test_semantic_digest_ignores_whitespace_key_order_and_location(pack, tmp_path):
    before = assets.load_sticker(pack)
    pack.write_text('{ "loop": true, "frame_duration": 3,\n "frames": ["a.png", "b.png"], "version": 1 }')
    assert assets.verify_sticker(pack, before.digest) == before
    moved = tmp_path / 'moved'
    moved.mkdir()
    for file in (pack, *before.frames):
        shutil.copyfile(file, moved / file.name)
    assert assets.load_sticker(moved / pack.name).digest == before.digest


@pytest.mark.parametrize('change', ['pixels', 'metadata', 'order', 'duration', 'loop', 'filename'])
def test_digest_covers_all_semantics_and_complete_png_bytes(pack, change):
    before = assets.load_sticker(pack)
    data = json.loads(pack.read_text())
    if change == 'pixels':
        (pack.parent / 'a.png').write_bytes(png(rgba=(180, 100, 70, 255)))
    elif change == 'metadata':
        (pack.parent / 'a.png').write_bytes(png(extra=[chunk(b'tEXt', b'Comment\x00new')]))
    elif change == 'order':
        data['frames'].reverse()
    elif change == 'duration':
        data['frame_duration'] = 4
    elif change == 'loop':
        data['loop'] = False
    elif change == 'filename':
        (pack.parent / 'a.png').rename(pack.parent / 'new.png')
        data['frames'][0] = 'new.png'
    pack.write_text(json.dumps(data))
    with pytest.raises(assets.StickerAssetError, match='changed since import'):
        assets.verify_sticker(pack, before.digest)


@pytest.mark.parametrize('digest', ['', '0' * 63, 'z' * 64, None, 2, '0' * 65])
def test_invalid_expected_digest_rejected(pack, digest):
    with pytest.raises(assets.StickerAssetError, match='SHA-256'):
        assets.verify_sticker(pack, digest)


@pytest.mark.parametrize('field,value', [
    ('version', 2), ('version', True), ('version', 1.0), ('version', '1'),
    ('frames', []), ('frames', 'a.png'), ('frames', ['a.png'] * 121), ('frames', [None]), ('frames', [1]),
    ('frame_duration', 0), ('frame_duration', 301), ('frame_duration', True), ('frame_duration', 1.5), ('frame_duration', '3'),
    ('loop', 1), ('loop', 'true'), ('loop', None), ('unexpected', 'not allowed'),
])
def test_invalid_manifest_fields(pack, field, value):
    data = json.loads(pack.read_text())
    data[field] = value
    pack.write_text(json.dumps(data))
    with pytest.raises(assets.StickerAssetError):
        assets.load_sticker(pack)


@pytest.mark.parametrize('content', [
    'not json', '[]', 'null', '{}', '{"version":1,"version":1}',
    '{"version":NaN}', '\ufeff{}', pytest.param('[' * 2000 + ']' * 2000, id='deeply-nested'),
])
def test_invalid_manifest_encoding_or_structure(pack, content):
    pack.write_text(content, encoding='utf-8')
    with pytest.raises(assets.StickerAssetError):
        assets.load_sticker(pack)


def test_invalid_utf8_and_manifest_size_bound(pack):
    pack.write_bytes(b'\xff')
    with pytest.raises(assets.StickerAssetError, match='UTF-8'):
        assets.load_sticker(pack)
    pack.write_bytes(b' ' * (assets.MAX_MANIFEST_BYTES + 1))
    with pytest.raises(assets.StickerAssetError, match='64 KiB'):
        assets.load_sticker(pack)


@pytest.mark.parametrize('name', [
    '../a.png', '/a.png', 'a/../a.png', './a.png', 'a//b.png',
    'C:/a.png', 'C:a.png', '\\\\server\\a.png', 'folder\\a.png',
    'https://example.invalid/a.png', 'file:///tmp/a.png', 'a.svg', 'a.png.exe',
    '', 'a\x00.png', 'a\n.png', 'a.png:stream', pytest.param('a' * 1025 + '.png', id='overlength-name'),
])
def test_manifest_paths_are_relative_contained_png_only(pack, name):
    manifest(pack, frames=[name])
    with pytest.raises(assets.StickerAssetError):
        assets.load_sticker(pack)


def test_manifest_allows_contained_subdirectory(pack):
    folder = pack.parent / 'frames'
    folder.mkdir()
    (folder / 'a.png').write_bytes(png())
    manifest(pack, frames=['frames/a.png'])
    assert assets.load_sticker(pack).frames == (folder / 'a.png',)


@pytest.mark.parametrize('path', ['https://example.invalid/a.png', 'file:///tmp/a.png', '//server/a.png', '\\\\server\\a.png'])
def test_remote_selection_rejected_without_network(path):
    with pytest.raises(assets.StickerAssetError, match='network paths and URLs'):
        assets.load_sticker(path)


@pytest.mark.parametrize('suffix', ['.zip', '.svg', '.gif', '.webp', '.mp4', '.apng', '.tar', '.png.exe'])
def test_explicit_format_allowlist(tmp_path, suffix):
    path = tmp_path / ('bad' + suffix)
    path.write_bytes(png())
    with pytest.raises(assets.StickerAssetError, match='PNG image or a JSON'):
        assets.load_sticker(path)


def test_missing_and_nonregular_inputs(tmp_path):
    with pytest.raises(assets.StickerAssetError):
        assets.load_sticker(tmp_path / 'missing.png')
    folder = tmp_path / 'directory.png'
    folder.mkdir()
    with pytest.raises(assets.StickerAssetError, match='ordinary local file'):
        assets.load_sticker(folder)


def symlink(source, target):
    try:
        target.symlink_to(source, target_is_directory=source.is_dir())
    except (OSError, NotImplementedError):
        pytest.skip('Symbolic links unavailable on this platform/account')


def test_selected_final_symlink_rejected(pack):
    alias = pack.parent / 'alias.json'
    symlink(pack, alias)
    with pytest.raises(assets.StickerAssetError, match='symbolic link'):
        assets.load_sticker(alias)
    image = pack.parent / 'alias.png'
    symlink(pack.parent / 'a.png', image)
    with pytest.raises(assets.StickerAssetError, match='symbolic link'):
        assets.load_sticker(image)


def test_selected_parent_alias_is_canonical_trust_anchor(pack, tmp_path):
    alias = tmp_path.parent / (tmp_path.name + '-alias')
    symlink(tmp_path, alias)
    try:
        assert assets.load_sticker(alias / pack.name) == assets.load_sticker(pack)
    finally:
        alias.unlink()


def test_manifest_descendant_file_and_directory_links_rejected(pack):
    linked = pack.parent / 'linked.png'
    symlink(pack.parent / 'a.png', linked)
    manifest(pack, frames=['linked.png'])
    with pytest.raises(assets.StickerAssetError, match='symbolic links'):
        assets.load_sticker(pack)
    real = pack.parent / 'actual'
    real.mkdir()
    (real / 'frame.png').write_bytes(png())
    symlink(real, pack.parent / 'alias')
    manifest(pack, frames=['alias/frame.png'])
    with pytest.raises(assets.StickerAssetError, match='symbolic links'):
        assets.load_sticker(pack)


def test_frame_reader_does_not_resolve_replaced_parent_alias(pack):
    folder = pack.parent / 'frames'
    folder.mkdir()
    frame = folder / 'a.png'
    frame.write_bytes(png())
    manifest(pack, frames=['frames/a.png'])
    loaded = assets.load_sticker(pack)
    moved = pack.parent / 'moved'
    folder.rename(moved)
    symlink(moved, folder)
    with pytest.raises(assets.StickerAssetError, match='symbolic links'):
        assets.read_sticker_frame(loaded.frames[0])


def test_wrong_content_and_oversized_png(tmp_path):
    path = tmp_path / 'bad.png'
    path.write_bytes(b'GIF89a' + b'\x00' * 32)
    with pytest.raises(assets.StickerAssetError, match='content is not PNG'):
        assets.load_sticker(path)
    path.write_bytes(b'x' * (assets.MAX_PNG_BYTES + 1))
    with pytest.raises(assets.StickerAssetError, match='4096 KiB'):
        assets.load_sticker(path)


@pytest.mark.parametrize('size', [(0, 1), (1, 0), (2049, 16), (16, 2049), (0xffffffff, 1)])
def test_dimensions_rejected_before_decode(tmp_path, size):
    path = tmp_path / 'big.png'
    path.write_bytes(png(*size, raw=b''))
    with pytest.raises(assets.StickerAssetError, match='dimensions'):
        assets.load_sticker(path)


def test_decoded_pack_budget_includes_duplicate_frames(pack):
    # Eight 2048-square RGBA frames are exactly 128 MiB. A duplicate is still a
    # decoded animation frame, so the ninth must be rejected before decoding.
    (pack.parent / 'a.png').write_bytes(png(2048, 2048))
    manifest(pack, frames=['a.png'] * 8)
    assert len(assets.load_sticker(pack).frames) == 8
    manifest(pack, frames=['a.png'] * 9)
    with pytest.raises(assets.StickerAssetError, match='128 MiB'):
        assets.load_sticker(pack)


def test_mixed_dimensions_rejected(pack):
    (pack.parent / 'b.png').write_bytes(png(20, 16))
    with pytest.raises(assets.StickerAssetError, match='same dimensions'):
        assets.load_sticker(pack)


@pytest.mark.parametrize('depth,color', [(16, 6), (16, 2), (8, 0), (8, 3), (8, 4), (1, 0)])
def test_only_8bit_rgb_or_rgba(tmp_path, depth, color):
    path = tmp_path / 'unsupported.png'
    path.write_bytes(png(depth=depth, color=color))
    with pytest.raises(assets.StickerAssetError, match='8-bit RGB or RGBA'):
        assets.load_sticker(path)


def test_plain_rgb_and_srgb_tags_supported(tmp_path):
    path = tmp_path / 'rgb.png'
    path.write_bytes(png(color=2, rgba=(35, 190, 150), extra=[
        chunk(b'sRGB', b'\x00'), chunk(b'gAMA', struct.pack('>I', 45455)),
        chunk(b'cHRM', struct.pack('>8I', 31270, 32900, 64000, 33000, 30000, 60000, 15000, 6000)),
    ]))
    assert assets.load_sticker(path).width == 16


@pytest.mark.parametrize('extra', [
    chunk(b'iCCP', b'profile\x00\x00' + zlib.compress(b'x' * 1000)),
    chunk(b'zTXt', b'text\x00\x00' + zlib.compress(b'x' * 1000)),
    chunk(b'iTXt', b'description\x00text'),
    chunk(b'acTL', struct.pack('>II', 8, 0)),
    chunk(b'cICP', b'\x01\x10\x00\x01'),
    chunk(b'gAMA', struct.pack('>I', 100000)),
    chunk(b'cHRM', b'\x00' * 32),
    chunk(b'PLTE', b'\x00\xff\x00'),
    # Keep this full payload out of PYTEST_CURRENT_TEST on Windows.
    pytest.param(chunk(b'tEXt', b'Comment\x00' + b'x' * 65536), id='oversized-text-metadata'),
])
def test_unsafe_or_unsupported_metadata_rejected(tmp_path, extra):
    path = tmp_path / 'unsafe.png'
    path.write_bytes(png(extra=[extra]))
    with pytest.raises(assets.StickerAssetError):
        assets.load_sticker(path)


def test_png_chunk_count_bound(tmp_path):
    path = tmp_path / 'many.png'
    path.write_bytes(png(extra=[chunk(b'tEXt', b'K\x00v')] * 1024))
    with pytest.raises(assets.StickerAssetError, match='too many chunks'):
        assets.load_sticker(path)


@pytest.mark.parametrize('damage', ['crc', 'truncate', 'trailing', 'missing_iend', 'bad_filter', 'pixels_short', 'pixels_long', 'bad_zlib'])
def test_corrupt_or_partially_decodable_png_is_rejected(tmp_path, damage):
    data = png()
    if damage == 'crc':
        data = data[:29] + bytes([data[29] ^ 1]) + data[30:]
    elif damage == 'truncate':
        data = data[:-3]
    elif damage == 'trailing':
        data += b'PK\x03\x04archive'
    elif damage == 'missing_iend':
        data = data[:-12]
    elif damage == 'bad_filter':
        data = png(raw=(b'\x07' + b'\x00' * 64) * 16)
    elif damage == 'pixels_short':
        data = png(raw=b'\x00' * 10)
    elif damage == 'pixels_long':
        data = png(raw=b'\x00' * 1000000)
    elif damage == 'bad_zlib':
        data = data[:33] + chunk(b'IDAT', b'invalid') + chunk(b'IEND')
    path = tmp_path / 'damaged.png'
    path.write_bytes(data)
    with pytest.raises(assets.StickerAssetError):
        assets.load_sticker(path)


def test_cancel_before_or_between_frames(pack, monkeypatch):
    event = threading.Event()
    event.set()
    with pytest.raises(assets.StickerCancelled):
        assets.load_sticker(pack, cancel=event)
    event.clear()
    original = assets._decode_png
    def decode(*args, **kwargs):
        result = original(*args, **kwargs)
        event.set()
        return result
    monkeypatch.setattr(assets, '_decode_png', decode)
    with pytest.raises(assets.StickerCancelled):
        assets.load_sticker(pack, cancel=event)


def test_modified_during_read_rejected(pack, monkeypatch):
    path = pack.parent / 'a.png'
    original = assets._open_nofollow
    def changed(path):
        fd = original(path)
        path.write_bytes(png(17, 17))
        return fd
    monkeypatch.setattr(assets, '_open_nofollow', changed)
    with pytest.raises(assets.StickerAssetError, match='changed while'):
        assets.load_sticker(path)


def test_dependency_listing_does_not_decode_png(pack, monkeypatch):
    def fail(*args, **kwargs):
        raise AssertionError('Dependency listing must not decode images')
    monkeypatch.setattr(assets, '_decode_png', fail)
    assert len(assets.sticker_input_paths(pack)) == 3


def test_original_builtins_are_transparent_distinct_and_reproducible(tmp_path):
    from scripts.create_original_stickers import generate
    builtins = assets.builtin_stickers()
    assert [name for name, _ in builtins] == ['Spark star', 'Check bubble', 'Pulse ring']
    regenerated = generate(tmp_path / 'generated')
    for (_, bundled), recreated in zip(builtins, regenerated):
        asset = assets.load_sticker(bundled)
        copy = assets.load_sticker(recreated)
        # Compare exactly in the compositor's premultiplied representation.
        # Qt PNG unpremultiplication can select adjacent straight-RGB codes
        # that map back to identical premultiplied pixels. No tolerance is used;
        # shipped PNG bytes and project identities are pinned separately below.
        assert (asset.frame_duration, asset.loop) == (copy.frame_duration, copy.loop)
        assert len(asset.frames) == len(copy.frames)
        for original_path, recreated_path in zip(asset.frames, copy.frames):
            original = QImage(str(original_path)).convertToFormat(QImage.Format.Format_RGBA8888_Premultiplied)
            recreated_image = QImage(str(recreated_path)).convertToFormat(QImage.Format.Format_RGBA8888_Premultiplied)
            assert original.size() == recreated_image.size()
            assert bytes(original.constBits()) == bytes(recreated_image.constBits())
            assert recreated_image.dotsPerMeterX() == recreated_image.dotsPerMeterY() == 3937
        assert (asset.width, asset.height) == (256, 256)
        for frame in asset.frames:
            image = QImage.fromData(assets.read_sticker_frame(frame), 'PNG')
            assert image.hasAlphaChannel()
            assert image.pixelColor(0, 0).alpha() == 0
            assert image.pixelColor(128, 128).alpha() > 0
    animation = assets.load_sticker(builtins[-1][1])
    assert len(animation.frames) == 8 and animation.frame_duration == 3 and animation.loop
    assert len({hashlib.sha256(p.read_bytes()).hexdigest() for p in animation.frames}) == 8
    notice = builtins[0][1].parent / 'LICENSE.txt'
    notice_text = notice.read_text(encoding='utf-8')
    assert 'DISTRIBUTION LICENSE PENDING' not in notice_text
    assert 'repository root LICENSE' in notice_text
    assert 'CutCat Use, Modification and No-Sale License' in notice_text


# These are the preserved distributed bytes, not values regenerated at test time.
# Regenerating artwork must never silently invalidate existing projects' hashes.
SHIPPED_STICKER_SHA256 = {
    'spark_star.png': 'aff92897cb6a24abb6d4a848732cd4bea398844b50e7dbd1d615ef793960fea9',
    'check_bubble.png': 'f21e5f961db9c483b83358998656af76c7b2beda047fe324501c108e4763bbe7',
    'pulse_ring/sticker.json': '837c28e36b2a6bae075780e53fa4f2d0509dddf08fb776959564746289d76065',
    'pulse_ring/frame_00.png': 'a8393ea06935521f6cc3cb612ae14e2e60ce4903dda909a70f1ca00a8c2c50b0',
    'pulse_ring/frame_01.png': '66c0e8c73f07495d1b67d142efea3d5a91d2a150f00d2989cbe044c6405f5b0e',
    'pulse_ring/frame_02.png': '5156f2cb1b58b7fa55d62f812b6f836f6402131ea80614b6eac465697de7779a',
    'pulse_ring/frame_03.png': 'af4fa0405b6753f7c6a1b7a12192ff6cb99d7268c79b28c59ad71ad9bb0d1028',
    'pulse_ring/frame_04.png': '68336babfb446e60c956c08b06af42759fb32ca332af5c8bbfe50de07de85f75',
    'pulse_ring/frame_05.png': 'e4290d120c6926803477148c23e172ee276c561f1282132d484be57c5b84d9a2',
    'pulse_ring/frame_06.png': '49e8fedc0123f738b71f751d9b12cdd20ea421421bacbc3f3b4de21b82c9ebe1',
    'pulse_ring/frame_07.png': 'e78be5f64566ddd8e7d869d3a922ec66c5bd1285ba87935c3be41872f5c0e5a0',
}


@pytest.mark.parametrize('relative,expected', SHIPPED_STICKER_SHA256.items())
def test_shipped_sticker_bytes_are_unchanged(relative, expected):
    root = assets.builtin_stickers()[0][1].parent
    assert hashlib.sha256((root / relative).read_bytes()).hexdigest() == expected


def test_shipped_sticker_project_identities_are_unchanged():
    assert tuple(assets.load_sticker(path).digest for _, path in assets.builtin_stickers()) == (
        '28675a1df840785dbc77ceae03557b980c450b56e546ea0f1b9da6c1b1645da6',
        '9b4912ff6318219984dd2de037045e416e0f5ef495e7c69fbea2a6c9fbe2f73e',
        '1b2e9267ed748dc2cd4f54b996a7b054472a27e936734b332e9066dbcd7cb30e',
    )


def test_adam7_interlaced_rgb_is_fully_decoded(tmp_path):
    width, height = 17, 21
    raw = bytearray()
    for x, y, dx, dy in ((0,0,8,8),(4,0,8,8),(0,4,4,8),(2,0,4,4),(0,2,2,4),(1,0,2,2),(0,1,1,2)):
        columns = max(0, (width - x + dx - 1) // dx)
        rows = max(0, (height - y + dy - 1) // dy)
        if columns and rows:
            raw.extend((b'\x00' + bytes((30, 200, 50)) * columns) * rows)
    path = tmp_path / 'adam7.png'
    path.write_bytes(png(width, height, color=2, raw=bytes(raw), interlace=1))
    asset = assets.load_sticker(path)
    assert (asset.width, asset.height) == (17, 21)
    image = QImage.fromData(assets.read_sticker_frame(path), 'PNG')
    assert image.pixelColor(16, 20).getRgb() == (30, 200, 50, 255)


def test_exact_frame_count_limit(pack):
    manifest(pack, frames=['a.png'] * 120)
    assert len(assets.load_sticker(pack).frames) == 120


def test_named_pipe_rejected_without_opening(tmp_path):
    import os
    if not hasattr(os, 'mkfifo'):
        pytest.skip('Named pipes unavailable')
    path = tmp_path / 'pipe.png'
    os.mkfifo(path)
    with pytest.raises(assets.StickerAssetError, match='ordinary local file'):
        assets.load_sticker(path)


def test_static_alternate_stream_spelling_rejected(tmp_path):
    path = tmp_path / 'a.png:stream.png'
    with pytest.raises(assets.StickerAssetError, match='alternate data streams'):
        assets.load_sticker(path)
