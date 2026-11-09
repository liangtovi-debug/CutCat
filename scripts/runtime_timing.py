"""Opt-in private stage receipts. No media paths, text, or exception messages."""

def new_timing_token():
    import uuid
    return uuid.uuid4().hex


def record_stage_event(scope, stage, event, token, **details):
    import hashlib
    import json
    import os
    from pathlib import Path
    import time
    import stat
    directory = os.environ.get('RANGE_ORACLE_EVIDENCE')
    if not directory:
        return
    if len(token) != 32 or any(c not in '0123456789abcdef' for c in token):
        raise ValueError('Invalid timing token')
    root = Path(directory)
    root.mkdir(parents=True, exist_ok=True)
    node = os.environ.get('PYTEST_CURRENT_TEST', '').rsplit(' (', 1)[0]
    record = {'schema': 1, 'scope': scope, 'stage': stage, 'event': event,
              'pid': os.getpid(), 'node_sha256': hashlib.sha256(node.encode()).hexdigest(),
              'wall_time': time.time(), 'monotonic_seconds': time.monotonic(),
              'process_cpu_seconds': time.process_time(), **details}
    data = (json.dumps(record, sort_keys=True) + '\n').encode('utf-8')
    path = root / (token + '.jsonl')
    try:
        info = path.lstat()
    except FileNotFoundError:
        info = None
    if info is not None and (not stat.S_ISREG(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400):
        raise ValueError('Timing receipt must be a regular file')
    flags = os.O_WRONLY | os.O_APPEND | os.O_CREAT | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0)
    fd = os.open(path, flags, 0o600)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
            raise ValueError('Timing receipt must be a regular file')
        with os.fdopen(fd, 'ab', buffering=0) as stream:
            fd = None
            stream.write(data)
    finally:
        if fd is not None:
            os.close(fd)


def timed_stage(scope, stage, token):
    from contextlib import contextmanager
    import time
    @contextmanager
    def measured():
        began = time.monotonic()
        cpu = time.process_time()
        record_stage_event(scope, stage, 'start', token)
        try:
            yield
        except BaseException as error:
            try:
                record_stage_event(scope, stage, 'failed', token,
                                   elapsed_seconds=time.monotonic() - began,
                                   cpu_seconds=time.process_time() - cpu,
                                   error_type=type(error).__name__)
            except Exception as receipt_error:
                error.add_note('Timing failure receipt unavailable: ' + type(receipt_error).__name__)
            raise
        else:
            record_stage_event(scope, stage, 'finished', token,
                               elapsed_seconds=time.monotonic() - began,
                               cpu_seconds=time.process_time() - cpu)
    return measured()
