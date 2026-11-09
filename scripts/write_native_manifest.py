"""Index native archives with consistent full-test, build and source provenance.

This checks locally produced consistency metadata, not a signature/security
attestation. No publishing, signing, downloads or source-only native claims.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import platform
import sys
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
if __package__ in (None, ''):
    sys.path.insert(0, str(ROOT))
from lumen.build_provenance import validate_source_provenance
from lumen.model import VERSION as PROJECT_VERSION
from scripts.run_bounded_tests import validate_nodes, verify_receipt
from scripts.source_provenance import capture_source_provenance


def fingerprint(path: Path) -> dict:
    if path.is_symlink() or not path.is_file() or path.stat().st_size == 0:
        raise ValueError(f'Required evidence is missing, empty or a symlink: {path.name}')
    with path.open('rb') as stream:
        digest = hashlib.file_digest(stream, 'sha256').hexdigest()
    return {'name': path.name, 'bytes': path.stat().st_size, 'sha256': digest}


def read_object(path: Path) -> dict:
    fingerprint(path)
    value = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(value, dict):
        raise ValueError(f'Expected an evidence object: {path.name}')
    return value


def validate_full_tests(root: Path, source: dict) -> tuple[dict, dict]:
    folder = root / 'build/test-shards'
    summary = read_object(folder / 'summary.json')
    if (summary.get('schema') != 1 or summary.get('success') is not True
            or summary.get('exact_once') is not True or summary.get('paths') != ['tests']
            or Path(str(summary.get('root', ''))).resolve() != root.resolve()):
        raise ValueError('Successful full tests from this source root are required, not a subset')
    for key in ('source_provenance_start', 'source_provenance_end'):
        if validate_source_provenance(summary.get(key)) != source:
            raise ValueError('Tests were not run against the current source snapshot')
    plan = read_object(folder / 'plan.json')
    nodes, shards = plan.get('collected'), plan.get('shards')
    if not isinstance(nodes, list) or not isinstance(shards, list) or not shards:
        raise ValueError('Missing full test collection/shard plan')
    validate_nodes(nodes)
    digest = hashlib.sha256(json.dumps(nodes, ensure_ascii=True, separators=(',', ':')).encode()).hexdigest()
    if (plan.get('schema') != 1 or plan.get('collection_sha256') != digest
            or summary.get('collection_sha256') != digest
            or summary.get('collected_nodes') != len(nodes)
            or summary.get('verified_nodes') != len(nodes)
            or summary.get('total_shards') != len(shards)):
        raise ValueError('Full collection identities and counts disagree')
    if any(not isinstance(shard, list) for shard in shards):
        raise ValueError('Invalid shard plan')
    flat = [node for shard in shards for node in shard]
    if Counter(flat) != Counter(nodes):
        raise ValueError('Shard union is not the exact full collection')
    completed = summary.get('completed_shards')
    if (not isinstance(completed, list) or len(completed) != len(shards)
            or any(not isinstance(item, dict) or item.get('success') is not True for item in completed)
            or Counter(item.get('shard') for item in completed) != Counter(range(len(shards)))
            or summary.get('unstarted_shards') not in (None, [], 0)):
        raise ValueError('Incomplete or failed shard status')
    verify_receipt(folder / 'collection/receipt.json', nodes, collection=True)
    evidence = {'tests': fingerprint(folder / 'summary.json'),
                'test_plan': fingerprint(folder / 'plan.json'),
                'test_collection_receipt': fingerprint(folder / 'collection/receipt.json')}
    outcomes = Counter()
    for index, expected in enumerate(shards):
        shard = folder / f'shard-{index:03d}'
        actual = verify_receipt(shard / 'receipt.json', expected)
        outcomes.update(actual)
        status = read_object(shard / 'status.json')
        if (status.get('success') is not True or status.get('returncode') != 0
                or status.get('timed_out') is not False or status.get('shard') != index
                or status.get('expected_nodes') != len(expected) or status.get('outcomes') != actual):
            raise ValueError('Shard status contradicts its exact receipt')
        tree = ET.parse(shard / 'results.xml').getroot()
        if (len(list(tree.iter('testcase'))) != len(expected)
                or list(tree.iter('failure')) or list(tree.iter('error'))):
            raise ValueError('Shard JUnit is incomplete or failed')
        evidence[f'shard_{index:03d}_receipt'] = fingerprint(shard / 'receipt.json')
        evidence[f'shard_{index:03d}_junit'] = fingerprint(shard / 'results.xml')
    if dict(outcomes) != summary.get('outcomes') or sum(outcomes.values()) != len(nodes):
        raise ValueError('Full-test outcome totals disagree with verified receipts')
    aggregate = ET.parse(folder / 'results.xml').getroot()
    if (len(list(aggregate.iter('testcase'))) != len(nodes)
            or list(aggregate.iter('failure')) or list(aggregate.iter('error'))):
        raise ValueError('Aggregate JUnit is incomplete or failed')
    evidence['test_junit'] = fingerprint(folder / 'results.xml')
    return summary, evidence


def make_manifest(root: Path, source: dict, system: str, machine: str) -> dict:
    source = validate_source_provenance(source)
    targets = {'win32': ('LumenEdit-Draft-Windows.zip', {'amd64', 'x86_64'}),
               'darwin': ('LumenEdit-Draft-macOS.tar.gz', {'arm64'})}
    if system not in targets or machine.lower() not in targets[system][1]:
        raise ValueError('Native validation currently targets Windows x64 or macOS arm64 only')
    summary, evidence = validate_full_tests(root, source)
    smoke_path = root / 'build/native-smoke/result.json'
    smoke = read_object(smoke_path)
    if (smoke.get('passed') is not True or smoke.get('frozen') is not True
            or smoke.get('platform') != system or str(smoke.get('machine', '')).lower() != machine.lower()):
        raise ValueError('Successful frozen smoke must match this native platform and architecture')
    if validate_source_provenance(smoke.get('built_source')) != source:
        raise ValueError('Frozen build/smoke was not produced from the tested current source')
    from lumen.caption_runtime import VERSION as FRIBIDI_VERSION
    caption = smoke.get('caption_shaping')
    caption_provision = read_object(root / 'build/caption-runtime.json')
    if (not isinstance(caption, dict) or not all(caption.get(key) for key in ('raqm', 'fribidi', 'harfbuzz'))
            or caption_provision.get('status') != 'passed' or caption_provision.get('after') != caption
            or (system == 'win32' and caption.get('fribidi') != FRIBIDI_VERSION)):
        raise ValueError('Frozen caption shaping does not match the verified runtime')
    color = smoke.get('color_pipeline_runtime_checks')
    if (not isinstance(color, dict) or color.get('synthetic_media_only') is not True
            or color.get('downloads_or_user_media_used') is not False
            or any(not isinstance(color.get(key), dict) or not color[key] or color[key].get('passed') is False
                   for key in ('animation', 'apple_log_experimental', 'highbit_caption_sticker_chain', 'ui'))
            or color['animation'].get('schema') != PROJECT_VERSION
            or any(color[key].get('source_unchanged') is not True for key in
                   ('animation', 'apple_log_experimental', 'highbit_caption_sticker_chain'))
            or color['ui'].get('apply_once_undo_redo_and_persistence') is not True):
        raise ValueError(f'Successful current schema-{PROJECT_VERSION} color-pipeline smoke evidence is missing')
    archive_path = root / 'dist' / targets[system][0]
    archive_identity = fingerprint(archive_path)
    executable_path = (root / 'build/archived-package' / 'LumenEdit-Draft/LumenEdit-Draft.exe'
                       if system == 'win32' else root / 'build/archived-package/LumenEdit-Draft.app/Contents/MacOS/LumenEdit-Draft')
    launch_path = root / 'build/native-smoke-launch/process-result.json'
    launch = read_object(launch_path)
    if (launch.get('schema') != 1 or launch.get('success') is not True
            or type(launch.get('returncode')) is not int or launch['returncode'] != 0
            or launch.get('timed_out') is not False or launch.get('platform') != system
            or launch.get('archive') != archive_identity
            or Path(str(launch.get('archive_path', ''))).resolve() != archive_path.resolve()
            or Path(str(launch.get('result_path', ''))).resolve() != smoke_path.resolve()
            or Path(str(launch.get('executable', ''))).resolve() != executable_path.resolve()
            or launch.get('executable_fingerprint') != fingerprint(executable_path)):
        raise ValueError('Smoke launch does not bind this unchanged archive and extracted executable')
    evidence.update({
        'native_smoke_launch': fingerprint(launch_path),
        'frozen_smoke': fingerprint(smoke_path),
        'python_dependencies': fingerprint(root / 'dist/python-dependencies.txt'),
        'ffmpeg_provenance': fingerprint(root / 'build/ffmpeg-provenance.json'),
        'caption_runtime': fingerprint(root / 'build/caption-runtime.json'),
        'test_font_provenance': fingerprint(root / 'build/test-font-provenance.json'),
    })
    return {'schema': 1, 'commit': source['commit'], 'source_provenance': source,
            'platform': system, 'machine': machine,
            'archive': archive_identity,
            'test_nodes': summary['verified_nodes'], 'test_outcomes': summary['outcomes'],
            'evidence': evidence, 'unsigned_internal_draft': True,
            'physical_desktop_validated': False, 'model_inference_validated': False,
            'signing_identity_used': False, 'public_release': False,
            'security_attestation': False}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=ROOT)
    args = parser.parse_args(argv)
    root = args.root.resolve()
    try:
        source = capture_source_provenance(root)
        if os.environ.get('GITHUB_SHA') not in (None, source['commit']):
            raise ValueError('Checked-out HEAD differs from the workflow commit')
        result = make_manifest(root, source, sys.platform, platform.machine())
        if capture_source_provenance(root) != source:
            raise ValueError('Source changed while preparing the native manifest')
        target = root / 'dist/native-manifest.json'
        with target.open('x', encoding='utf-8') as stream:
            json.dump(result, stream, indent=2)
            stream.write('\n')
        print(target)
        return 0
    except (ValueError, OSError, ET.ParseError) as error:
        parser.exit(1, f'Native manifest refused: {error}\n')


if __name__ == '__main__':
    raise SystemExit(main())
