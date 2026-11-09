# Project schema 39: timeline markers

Schema 39 adds exactly one project field, `timeline_markers`, a list of up to
10,000 independent annotation records. It is required even when empty. Projects
with versions 1–38 migrate to an empty list; a pre-39 document carrying this field
is rejected, including an empty/null field. Unknown project and record fields
continue to reject. Older readers reject version 39.

Each record requires all six fields (including `end_frame: null` for a point):

| Field | Contract |
| --- | --- |
| `id` | Unique, nonblank single-line string, at most 128 Unicode code points |
| `name` | Nonblank single-line string, at most 128 Unicode code points |
| `comment` | Plain text, at most 4096 Unicode code points; empty permitted |
| `color` | `red`, `orange`, `yellow`, `green`, `cyan`, `blue`, `purple`, or `pink` |
| `start_frame` | Integer from 0 through 2^53−1, never boolean |
| `end_frame` | Null, or integer greater than start and at most 2^53−1, never boolean |

Names, IDs and comments reject C0/C1 controls, DEL, U+2028/U+2029, unpaired
surrogates and all Unicode noncharacters: U+FDD0–U+FDEF plus U+FFFE/U+FFFF at
the end of every Unicode plane (through U+10FFFE/U+10FFFF). Qt reserves some
noncharacters as internal document sentinels, so rejection prevents silent
newline conversion. The sole control exceptions are LF (`\n`) and tab (`\t`)
in comments. CR is rejected rather than silently normalized. Ordinary valid
NBSP, BOM, format characters and non-BMP Unicode are preserved exactly. This
constraint is local to new schema39 marker text and does not change versions
1–38, which have no marker field. HTML-looking text and Unicode names
are allowed and must remain plain text at every UI presentation. There is no
normalization, trimming or truncation on serialization. Character limits are
code points, not UTF-8 bytes. The combined `id` + `name` + `comment` text across
the entire collection is additionally capped at **2,000,000 code points**, checked
in a single validation pass. This keeps marker-only text at most 8 MiB in UTF-8
(or at most 24,000,000 bytes with worst-case non-BMP JSON ASCII escaping), plus
bounded record overhead. Different IDs may use the same name and frame;
overlapping ranges are valid.

A null end is a point. A range uses `[start_frame, end_frame)`; the end is
exclusive. These are **fixed project frame coordinates**, not source In/Out,
clip-attached metadata, range playback, export marks, chapter output or a sync
relationship. Moving/deleting clips, Source Insert, ripple delete and gap close
leave marker frame integers unchanged. Changing project FPS likewise preserves
integers, so the wall-clock seconds represented by a marker change with FPS.
There is no hidden retiming.

Markers never contribute to `Project.duration_frames`. Out-of-range records are
valid, persist on empty/shortened projects, remain editable and are not clamped,
trimmed or deleted. Navigation is valid only when the start is less than project
duration and, for a range, the whole exclusive end is at most duration. Consumers
must reject invalid navigation before invoking a clamping seek function.

Editor add/update/delete commands commit only the exact detached marker list.
They cannot change content or any other metadata; generic content transactions
cannot silently mutate markers. Equal-value updates retain history and redo.
Marker changes advance editor revision so preexisting edit plans go stale.
Immutable records and project snapshots preserve ID/history isolation. Save and
recovery reuse the existing validated project JSON path; source files are not
read or modified by annotation operations.

## Genuine schema38 serializer fixture

`tests/fixtures/schema38-before-timeline-markers.json` was generated **before**
modifying schema/model code, using the unmodified schema38 serializer in the
accepted `cutcat-gap-close` source copied to this worktree. Its input was the
preserved accepted schema37 fixture, loaded through that schema38 reader, with a
schema38 bound source-range record added and a descriptive project name. This is
an actual schema38 serialization capture, not current schema39 output with its
version tag changed, and not a claim that the newly generated file was an older
archived user project.

SHA-256: `e776826e1eb543a262607733734ef7d57855fd843ae620870ba95336f5acfdec`

The test verifies the hash, source-range presence, empty migrated marker list,
input immutability, and equality of **every historical serialized field** after
removing only the new marker field and restoring the historical version tag for
comparison. The preserved schema37 fixture and hash remain unchanged.

The existing 32 MiB total project-file/recovery input limit still applies. Record
count and per-string maxima are not a promise that every maximum-sized record
can coexist with every other maximum-sized project collection. This round does
not enlarge that existing file budget.
