"""Compare file snapshots without mixing Windows creation and change times.

CPython 3.12 on Windows reports creation time in stat/lstat.st_ctime_ns,
but can report metadata change time in fstat.st_ctime_ns. See
https://github.com/python/cpython/issues/157671. Never discard ctime from
before/after comparisons made through the *same* API: it still detects edits
that restore the original size and mtime. These checks detect changes; they
are not a file lock or a substitute for validating the bytes that were read.
"""
from __future__ import annotations

import os
import stat

_WINDOWS = os.name == 'nt'


def is_plain_file(info: os.stat_result) -> bool:
    """Reject special files and Windows reparse points, including symlinks."""
    return stat.S_ISREG(info.st_mode) and not (
        getattr(info, 'st_file_attributes', 0)
        & getattr(stat, 'FILE_ATTRIBUTE_REPARSE_POINT', 0x400)
    )


def _common_signature(info: os.stat_result) -> tuple:
    # Device + inode remain mandatory; size/timestamps alone are not identity.
    # Only compare the file type bits: Windows can infer permission bits from
    # the filename for stat(), but not for an already-open descriptor.
    return (info.st_dev, info.st_ino, stat.S_IFMT(info.st_mode), info.st_size,
            info.st_mtime_ns, getattr(info, 'st_birthtime_ns', None))


def same_file_state(before: os.stat_result, after: os.stat_result) -> bool:
    """Compare two path snapshots OR two descriptor snapshots, including ctime."""
    return (_common_signature(before) == _common_signature(after)
            and before.st_ctime_ns == after.st_ctime_ns)


def path_matches_descriptor(path_info: os.stat_result, descriptor_info: os.stat_result) -> bool:
    """Compare a checked path with its open file, retaining portable identity.

    Callers must separately use same_file_state for path-to-path and fd-to-fd
    checks. Only the Windows cross-API comparison omits ctime; birthtime, when
    supplied by the platform, is compared explicitly instead.
    """
    if not _WINDOWS:
        return same_file_state(path_info, descriptor_info)
    return _common_signature(path_info) == _common_signature(descriptor_info)
