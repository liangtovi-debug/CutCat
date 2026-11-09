"""Local-only model registration and a shared, non-queuing AI resource gate.

This module contains no network/client/downloader. A registry records explicit
local files and their identities; it does not certify their license or safety.
"""
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
import re
from pathlib import Path
import tempfile
import threading
from uuid import uuid4

from .file_identity import is_plain_file, path_matches_descriptor, same_file_state

MAX_MODEL_BYTES = 4 * 1024**3
MAX_PACK_BYTES = 8 * 1024**3
MAX_MODEL_FILES = 32
MAX_THREADS = 4
_gate = threading.Lock()
_state_lock = threading.Lock()
_active_kind = None

class LocalAIError(ValueError): pass
class AIResourceBusy(LocalAIError): pass
class InferenceCancelled(LocalAIError): pass

def check_cancel(cancel):
    if cancel is not None and cancel.is_set():
        raise InferenceCancelled('Local AI task cancelled.')

@contextmanager
def inference_slot(kind, cancel=None):
    """Permit one heavy local inference task at a time; never silently queue."""
    global _active_kind
    check_cancel(cancel)
    if not isinstance(kind, str) or not kind.strip(): raise LocalAIError('A task name is required.')
    if not _gate.acquire(blocking=False):
        raise AIResourceBusy('Another local AI task is running. Wait or cancel it before starting this task.')
    try:
        with _state_lock: _active_kind = kind
        check_cancel(cancel)
        yield
    finally:
        with _state_lock: _active_kind = None
        _gate.release()

def active_task():
    with _state_lock: return _active_kind

@dataclass(frozen=True)
class ModelFile:
    role: str
    path: str
    sha256: str
    size_bytes: int

@dataclass(frozen=True)
class LocalModelRecord:
    id: str
    kind: str
    label: str
    files: tuple[ModelFile, ...]
    created_utc: str
    source_note: str = ''
    license_note: str = 'Not independently verified; review model and runtime component terms.'

def inspect_model_files(kind, files, label='', source_note='', license_note='', cancel=None):
    """Hash explicit role/path pairs only, bounded and without file discovery."""
    if kind not in ('subtitle-assistant','semantic-search','tts','asr'):
        raise LocalAIError('Unsupported local model category.')
    if not isinstance(files, dict) or not 1 <= len(files) <= MAX_MODEL_FILES:
        raise LocalAIError('Choose between 1 and32 explicit local files.')
    result=[];total=0
    for role, raw in sorted(files.items()):
        check_cancel(cancel)
        if not isinstance(role,str) or not role or len(role)>80: raise LocalAIError('Invalid model-file role.')
        raw=os.fspath(raw)
        if raw.startswith(('//','\\\\')) or re.match(r'^[A-Za-z][A-Za-z0-9+.-]*:',raw) and not (os.name=='nt' and re.match(r'^[A-Za-z]:[\\/]',raw)):
            raise LocalAIError('Only explicit local model files are supported, not URLs or network paths.')
        path=Path(raw).expanduser()
        if path.is_symlink(): raise LocalAIError('Choose an actual local file rather than a symbolic link.')
        path=path.resolve(strict=True)
        if not path.is_file(): raise LocalAIError('Model inputs must be regular local files.')
        before=path.lstat();total+=before.st_size
        if not is_plain_file(before): raise LocalAIError('Model inputs must be regular local files without symbolic links.')
        if before.st_size<=0 or before.st_size>MAX_MODEL_BYTES or total>MAX_PACK_BYTES:
            raise LocalAIError('Model file exceeds the4GiB per-file /8GiB pack limit.')
        digest=hashlib.sha256()
        descriptor=os.open(path,os.O_RDONLY|getattr(os,'O_BINARY',0)|getattr(os,'O_NONBLOCK',0)|getattr(os,'O_NOFOLLOW',0))
        with os.fdopen(descriptor,'rb') as stream:
            opened=os.fstat(stream.fileno())
            if not is_plain_file(opened): raise LocalAIError('Model input is not a regular file.')
            if not path_matches_descriptor(before,opened): raise LocalAIError('A model input changed while being opened.')
            count=0
            for block in iter(lambda:stream.read(1024*1024),b''):
                check_cancel(cancel);count+=len(block)
                if count>before.st_size: raise LocalAIError('Model input grew while being verified.')
                digest.update(block)
            after=os.fstat(stream.fileno())
        now=path.lstat()
        if (count!=before.st_size or not is_plain_file(now) or not same_file_state(opened,after)
                or not same_file_state(before,now) or not path_matches_descriptor(now,after)):
            raise LocalAIError('A model input changed while being verified.')
        result.append(ModelFile(role,str(path),digest.hexdigest(),before.st_size))
    return LocalModelRecord(uuid4().hex,kind,label or kind,tuple(result),datetime.now(timezone.utc).isoformat(),source_note,
                            license_note or LocalModelRecord.__dataclass_fields__['license_note'].default)

class ModelRegistry:
    """Small local app-owned JSON registry. Registration never loads a model."""
    def __init__(self,path): self.path=Path(path)
    def records(self):
        if not self.path.exists(): return []
        if self.path.stat().st_size>1024*1024: raise LocalAIError('Local model registry is too large.')
        data=json.loads(self.path.read_text(encoding='utf-8'))
        if not isinstance(data,dict) or data.get('version')!=1 or not isinstance(data.get('models'),list) or len(data['models'])>64:
            raise LocalAIError('Invalid local model registry.')
        return [LocalModelRecord(**(item|{'files':tuple(ModelFile(**f) for f in item['files'])})) for item in data['models']]
    def register(self,record):
        if not isinstance(record,LocalModelRecord): raise LocalAIError('A verified model record is required.')
        records=self.records()
        records=[r for r in records if (r.kind,tuple(f.path for f in r.files))!=(record.kind,tuple(f.path for f in record.files))]
        if len(records)>=64: raise LocalAIError('The local registry has64 packs; remove an unused record first.')
        records.append(record)
        encoded=json.dumps({'version':1,'models':[asdict(r) for r in records]},ensure_ascii=False,indent=2)
        if len(encoded.encode('utf-8'))>1024*1024: raise LocalAIError('Model registry exceeds its1MiB limit.')
        self.path.parent.mkdir(parents=True,exist_ok=True)
        fd,tmp=tempfile.mkstemp(prefix='.lumen-models-',dir=self.path.parent)
        try:
            with os.fdopen(fd,'w',encoding='utf-8') as stream:
                stream.write(encoded)
                stream.flush();os.fsync(stream.fileno())
            os.replace(tmp,self.path)
        finally: Path(tmp).unlink(missing_ok=True)
        return record

def verify_registered(record, cancel=None):
    current=inspect_model_files(record.kind,{f.role:f.path for f in record.files},cancel=cancel)
    if [(f.role,f.path,f.sha256,f.size_bytes) for f in current.files] != [(f.role,f.path,f.sha256,f.size_bytes) for f in record.files]:
        raise LocalAIError('Model files changed since local import. Verify/import the pack again.')
    return record
