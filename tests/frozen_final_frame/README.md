# Frozen historical full-renderer oracle dependencies

The original model and caption files remain byte-for-byte copies from accepted
CutCat commit `2c70ebb1e31733ee4d95fe20a43152ae0d38f5df` (schema41). This range
round additionally retains that same hash-pinned full `render.py`,
`color_media.py`, `media.py`, `clip_composite.py` and `overlay_render.py` before introducing
optional range clocks in production. They are test-only; the application never
imports them.

The final-frame oracle prefers a full frozen checkout. A standalone extraction
loads these modules only in its separate oracle subprocess, loading the frozen
composite class before the frozen model to preserve strict type identity. The
benchmark verifies all historical hashes and unchanged visual dependencies.
Expected pixels are never redefined from the candidate range implementation.

Transport accepts only schema43 scenes with an empty `av_links` list, removes
exactly that metadata field and sets version41. Nonempty links reject. The new
range oracle uses its separate accepted schema43 archive without this transport.
