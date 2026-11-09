"""Narrow helpers for previewed whole-item timeline translations.

No discovery or editing policy lives here: each operation defines its interval,
its participants, its relationship guards and its audio preservation promise.
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class TimelineItemImpact:
    category: str
    item_id: str
    track: int | None
    previous_start: int
    start: int
    duration: int
    disposition: str


def participation(track, tracks, captions, stickers, *, operation, field_prefix, target_label):
    from .model import _boolean, _integer
    tracks = (track,) if tracks is None else tracks
    if type(tracks) is not tuple or not tracks:
        raise ValueError(f'{operation} participating tracks must be a nonempty tuple')
    for value in tracks:
        _integer(value, f'{field_prefix} participating track', 0, 3)
    if len(set(tracks)) != len(tracks) or track not in tracks:
        raise ValueError(f'{operation} participating tracks must be unique and include {target_label}')
    _boolean(captions, f'{field_prefix} follow captions')
    _boolean(stickers, f'{field_prefix} follow stickers')
    return tuple(sorted(tracks))


def interval_disposition(item, participates, start, end, *, intersection_message):
    if not participates or item.end <= start:
        return 'fixed'
    if item.start >= end:
        return 'move'
    raise ValueError(intersection_message)


def commit_item_collections(editor, candidate, impacts):
    """One guarded transaction; preserve live identities for fixed records."""
    def change():
        for category, name in (('clip', 'clips'), ('caption', 'captions'), ('sticker', 'overlays')):
            changed = {item.item_id for item in impacts
                       if item.category == category and item.disposition != 'fixed'}
            if changed:
                collection = getattr(editor.project, name)
                fixed = {item.id: item for item in collection if item.id not in changed}
                collection[:] = [fixed.get(item.id, item) for item in getattr(candidate, name)]
    editor._commit(change)
