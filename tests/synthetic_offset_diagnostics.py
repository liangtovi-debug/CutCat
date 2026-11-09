"""Allowlisted evidence for the locally generated split_source fixture only."""
import json
import re
import subprocess
import tempfile

from lumen.media import ffmpeg_binary, ffprobe_binary


def _read(command):
    # Never retain stderr, command strings, exceptions, filenames or tags.
    try:
        with tempfile.TemporaryFile() as output:
            result = subprocess.run(command, stdout=output, stderr=subprocess.DEVNULL,
                                    timeout=5, check=False)
            output.seek(0)
            data = output.read(65537)
        if result.returncode:
            return None, 'process_error'
        if len(data) > 65536:
            return None, 'output_limit'
        return data.decode('utf-8'), 'ok'
    except subprocess.TimeoutExpired:
        return None, 'timeout'
    except (OSError, UnicodeError):
        return None, 'unavailable'


def _numbers(record, fields):
    result = {}
    for field in fields:
        value = record.get(field)
        if type(value) in (str, int, float):
            value = str(value)
            if len(value) <= 64 and re.fullmatch(r'-?\d+(?:\.\d+|/\d+)?', value):
                result[field] = value
    return result


def collect_synthetic_offset_evidence(path):
    """No arbitrary media callers: split_source invokes this only after mismatch."""
    evidence = {'kind': 'synthetic_split_source_offset'}
    data, status = _read([ffmpeg_binary(), '-version'])
    match = re.match(r'ffmpeg version (\d+(?:\.\d+){1,3})(?=[\s-])', data or '')
    evidence['ffmpeg_version'] = match.group(1) if match else 'unavailable'
    evidence['version_status'] = status
    for role in ('v:0', 'a:0'):
        section = {}
        for kind, fields in (
            ('stream', ('index', 'start_pts', 'start_time', 'time_base')),
            ('packet', ('stream_index', 'pts', 'pts_time', 'dts', 'dts_time',
                        'duration', 'duration_time')),
        ):
            command = [ffprobe_binary(), '-v', 'error', '-select_streams', role]
            if kind == 'packet':
                command += ['-read_intervals', '%+#1']
            command += ['-show_entries', kind + '=' + ','.join(fields),
                        '-of', 'json', '-protocol_whitelist', 'file,pipe', str(path)]
            data, status = _read(command)
            section[kind + '_status'] = status
            if data is not None:
                try:
                    records = json.loads(data).get(kind + 's', [])
                    if records and isinstance(records[0], dict):
                        section[kind] = _numbers(records[0], fields)
                except (ValueError, AttributeError, TypeError, KeyError):
                    section[kind + '_status'] = 'invalid_json'
        evidence[role] = section
    return evidence
