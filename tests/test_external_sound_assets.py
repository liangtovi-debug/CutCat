"""Audit every shipped third-party file and source record without network use."""
from contrast_legacy_fixture import strip_contrast_pivot_field
from collections import Counter
from copy import deepcopy
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import wave

import numpy as np
import pytest

from lumen import sound_library as sounds
from lumen.model import VERSION, Editor, Media, Project, SoundProvenance
from lumen.sound_sources import SOURCE_FIELDS

ROOT = Path(__file__).resolve().parents[1] / 'lumen/assets/sounds'
CATALOG = json.loads((ROOT / 'third_party_catalog.json').read_text(encoding='utf-8'))
RECORD = json.loads((ROOT / 'THIRD_PARTY_PROVENANCE.json').read_text(encoding='utf-8'))


def external_media(entry, identifier='external'):
    return Media(identifier, str(entry.path), entry.name_en, entry.duration, has_video=False, has_audio=True,
                 sound_provenance=SoundProvenance(entry.id, entry.origin, entry.license,
                     entry.source_sha256, entry.sha256, entry.sample_rate, entry.sample_count,
                     **{name: getattr(entry, name) for name in SOURCE_FIELDS}))


def test_exact_shipped_collection_and_no_implicit_original_claim():
    entries = sounds.builtin_sounds()
    external = [e for e in entries if e.origin == 'external-cc0']
    originals = [e for e in entries if e.origin == 'procedural']
    assert len(entries) == 77 and len(external) == 53 and len(originals) == 24
    assert Counter(e.category for e in external) == dict(ui=9, transitions=8, notifications=5,
                                                       impacts=14, foley=12, atmosphere=5)
    assert len({e.sha256 for e in entries}) == len(entries)
    assert {e.path.name for e in entries} == {p.name for p in ROOT.glob('*.wav')}
    assert sum(e.byte_count for e in external) == 11343456
    assert all(e.license == 'CC0-1.0' and e.author and e.source_name for e in external)
    assert all(e.source_sha256 != e.sha256 for e in external)
    assert all(e.license == 'CutCat original / CC0-1.0' and not e.author for e in originals)
    assert all('Kenney' in e.author for e in sounds.search_sounds(external, 'Kenney'))
    assert len(sounds.search_sounds(external, 'Kenney')) == 44


@pytest.mark.parametrize('item', CATALOG['sounds'], ids=lambda item: item['id'])
def test_real_pcm_identity_quality_and_saved_license_evidence(item):
    entry = sounds._entry(item, ROOT, external=True)
    source = next(value for value in RECORD['sounds'] if value['id'] == entry.id)
    assert source['file'] == entry.path.name
    assert source['sha256'] == entry.sha256
    assert source['original_sha256'] == entry.source_sha256
    assert source['source_page_url'] == entry.source_url
    assert source['author'] == entry.author and source['source_name'] == entry.source_name
    assert source['output_bytes'] == entry.byte_count == entry.path.stat().st_size
    for name, digest in source['license_evidence_hashes'].items():
        assert name.startswith('licenses/') and '..' not in Path(name).parts
        assert hashlib.sha256((ROOT / name).read_bytes()).hexdigest() == digest
    with wave.open(str(entry.path), 'rb') as wav:
        assert wav.getnchannels() == source['channels'] in (1, 2)
        assert wav.getsampwidth() == 2 and wav.getframerate() == 48000
        assert wav.getnframes() == entry.sample_count
        raw = wav.readframes(entry.sample_count + 1)
        assert len(raw) == entry.sample_count * source['channels'] * 2
    pcm = np.frombuffer(raw, dtype='<i2').astype(np.float64) / 32768
    assert np.isfinite(pcm).all() and np.count_nonzero(pcm)
    assert 0 < np.max(np.abs(pcm)) <= .5
    assert np.sqrt(np.mean(pcm ** 2)) > .0001
    assert 20 * np.log10(np.max(np.abs(pcm))) == pytest.approx(entry.peak_dbfs, abs=1e-7)
    assert entry.duration == pytest.approx(source['duration_seconds'], abs=1e-9)
    assert source['quality']['source']['samples_at_or_above_full_scale'] == 0
    assert source['quality']['output']['samples_at_or_above_full_scale'] == 0
    assert source['quality']['human_listening_performed'] is False


@pytest.mark.parametrize('field,value', [
    ('author', ''), ('author', '\ud800'), ('source_name', ''), ('source_url', 'http://example.com/'),
    ('source_url', 'https://secret@example.com/'), ('license_url', 'https://example.com/cc0'),
    ('source_file', 'bad\nfile'), ('source_sha256', 'invalid'), ('byte_count', True),
    ('byte_count', 1), ('license', 'Free download'), ('origin', 'procedural'),
])
def test_invalid_external_metadata_is_rejected(field, value):
    data = deepcopy(CATALOG['sounds'][0]); data[field] = value
    with pytest.raises(sounds.SoundLibraryError):
        sounds._entry(data, ROOT, external=True)


def test_external_cannot_hide_in_original_or_import_catalog():
    item = CATALOG['sounds'][0]
    with pytest.raises(sounds.SoundLibraryError):
        sounds._entry(item, ROOT)
    with pytest.raises(sounds.SoundLibraryError):
        sounds._entry(item, ROOT, local=True)
    original = json.loads((ROOT / 'catalog.json').read_text())['sounds'][0]
    original['author'] = 'Third party'
    with pytest.raises(sounds.SoundLibraryError):
        sounds._entry(original, ROOT)


def test_legacy13_reads_but_external_metadata_cannot_be_downgraded(tmp_path):
    original = next(e for e in sounds.builtin_sounds() if e.origin == 'procedural')
    legacy = Project(media=[external_media(original)]).to_dict()
    legacy['version'] = 13; strip_contrast_pivot_field(legacy)
    if legacy['version'] < 17:
        for old_clip in legacy.get('clips', []):
            old_clip.get('grade', {}).pop('hue_hue_curve', None)
            old_clip.get('grade', {}).pop('hue_sat_curve', None)
            old_clip.get('grade', {}).pop('qualifier', None)
    if legacy['version'] < 16:
        for old_clip in legacy.get('clips', []): old_clip.pop('transform', None)
    for name in SOURCE_FIELDS:
        legacy['media'][0]['sound_provenance'].pop(name)
    migrated = Project.from_dict(legacy)
    assert migrated.media[0].sound_provenance.author == '' and migrated.to_dict()['version'] == VERSION
    external = next(e for e in sounds.builtin_sounds() if e.origin == 'external-cc0')
    data = Project(media=[external_media(external)]).to_dict()
    assert Project.from_dict(data).media[0].sound_provenance == external_media(external).sound_provenance
    data['version'] = 13; strip_contrast_pivot_field(data)
    if data['version'] < 17:
        for old_clip in data.get('clips', []):
            old_clip.get('grade', {}).pop('hue_hue_curve', None)
            old_clip.get('grade', {}).pop('hue_sat_curve', None)
            old_clip.get('grade', {}).pop('qualifier', None)
    if data['version'] < 16:
        for old_clip in data.get('clips', []): old_clip.pop('transform', None)
    with pytest.raises(ValueError, match='Legacy'):
        Project.from_dict(data)
    data['version'] = 14; strip_contrast_pivot_field(data); data['media'][0]['sound_provenance'] = []
    with pytest.raises(ValueError, match='provenance'):
        Project.from_dict(data)
    data['version'] = VERSION + 1
    with pytest.raises(ValueError, match='version'):
        Project.from_dict(data)


def test_external_identity_and_sources_survive_independent_project_save(tmp_path):
    entry = next(e for e in sounds.builtin_sounds() if e.origin == 'external-cc0')
    independent = tmp_path / 'library-copy.wav'; independent.write_bytes(entry.path.read_bytes())
    entry = replace(entry, path=independent)
    media = external_media(entry)
    editor = Editor(); editor.add_sound_clip(media, 3, 23)
    before = deepcopy(editor.project); editor.undo(); assert not editor.project.media
    editor.redo(); assert editor.project == before
    target = tmp_path / 'film.lumen'; editor.project.save(target); independent.unlink()
    reopened = Project.load(target)
    assert not reopened.missing_media() and reopened.clips == before.clips
    assert reopened.media[0].sound_provenance == media.sound_provenance
    assert str(reopened.media[0].path).startswith(str(tmp_path / 'film.assets'))
    sounds.verify_sound_file(reopened.media[0].path, entry.sha256, entry.sample_rate, entry.sample_count)


def test_distribution_evidence_is_concise_and_has_no_full_website_pages():
    assert not list((ROOT / 'licenses').glob('*.html'))
    assert RECORD['source_collector_manifest_sha256'] == 'aecb16e4dc9b11f5ff75726843613ec5e704da1d220a866fa39c602272b3b52d'
    declarations = json.loads((ROOT / 'licenses/source-declarations.json').read_text())['sources']
    assert len(declarations) == 6
    for declaration in declarations:
        assert len(declaration['license_declaration_excerpt'].split()) <= 25
        assert len(declaration['retrieved_page_sha256']) == 64
        assert declaration['retrieved_at_utc']
    by_id = {item['id']: item for item in declarations}
    for item in RECORD['sounds']:
        record = by_id[item['source_declaration_id']]
        assert record['page_url'] == item['source_page_url'] and record['author'] == item['author']
    legal = (ROOT / 'licenses/CC0-1.0.txt').read_text()
    assert '4. Limitations and Disclaimers.' in legal and 'Public License Fallback' in legal
