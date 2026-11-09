# External CC0 sounds bundled with CutCat

53 third-party sounds were selected on 2026-10-21 from asset-specific CC0 1.0 declarations by their publishers or creators. They are **not CutCat originals**. No user media was uploaded. CC0 permits copying, adapting and redistributing audio, including raw assets inside software, to the extent the dedicating rightsholder has those rights. This is recorded provenance, not a warranty covering all possible rights. The application and the CutCat name remain separately licensed.

| Source | Author as credited by source | Count | First-party asset page |
| --- | --- | ---: | --- |
| Interface Sounds 1.0 | Kenney | 18 | https://kenney.nl/assets/interface-sounds |
| Impact Sounds 1.0 | Kenney | 19 | https://kenney.nl/assets/impact-sounds |
| RPG Audio | Kenney Vleugels (Kenney) | 7 | https://kenney.nl/assets/rpg-audio |
| Bamboo-stick swishes | Iwan Gabovitch (qubodup) | 4 | https://opengameart.org/content/swish-bamboo-stick-weapon-swhoshes |
| Birds of Hawaii | Sébastien (muted.io) | 4 | https://muted.io/birds-of-hawaii/ |
| Rain (Ambient, Not Loopable) | Ove Melaa (OveMelaa) | 1 | https://opengameart.org/content/rain-ambient-not-loopable-2-versions-available |

Official license: https://creativecommons.org/publicdomain/zero/1.0/
Official legal text: https://creativecommons.org/publicdomain/zero/1.0/legalcode

Attribution is not a CC0 copyright condition; the app nevertheless records and displays source and author. Concise source declarations and pack licence notices are retained in `licenses/`, with page retrieval dates/hashes and evidence hashes. Full website HTML is not redistributed. Links are informational: the library does not fetch or open them automatically.

## Files and exact provenance

- `third_party_catalog.json`: the compact runtime manifest. Each entry contains bilingual name/tags/category, author, source pack/page, original filename/hash, official license URL, output WAV basename/hash/byte count, sample count/rate and peak.
- `THIRD_PARTY_PROVENANCE.json`: distribution-safe derivative of the frozen collector record (original collector SHA-256 `aecb16e4dc9b11f5ff75726843613ec5e704da1d220a866fa39c602272b3b52d`). It preserves archive URLs/hashes, original files, source measurements and transformations. Output paths are mapped to runtime WAV basenames. Original archive/input paths describe provenance and are not runtime dependencies. Website HTML is omitted.
- `licenses/source-declarations.json`: precise author, asset page, license URL, retrieval date and original-page SHA-256, plus a short exact CC0 license-label excerpt for each of the six source pages. These links and hashes identify the source of the recorded claim without republishing full websites.
- `licenses/`: the above six concise declarations, four pack licence notices, and official CC0 legal text extracted as plain text with its source URL. All six distributed evidence files are hashed in the provenance record.

The original 24 procedural WAVs, `catalog.json`, `LICENSE.txt` and their generator remain unchanged. Regenerating originals does not replace external files or their separate catalog. The original license notice never applies on behalf of these third parties.

## Audio preparation and limitations

All 53 outputs are decoded, 48 kHz PCM16 WAV, preserving the original mono/stereo channel count. Output bytes total 11,343,456; durations total 60.742042 seconds. None has decoded full-scale samples, and every output peak is at most 0.5 (about −6.02 dBFS). The preparation attenuates only as needed, never boosts quiet sources. The rain is a 15-second opening excerpt with 50-ms fades; all other selected source intervals are complete. No denoising is applied.

The collection is 9 UI sounds, 8 transitions, 14 impacts, 5 notifications, 12 foley sounds and 5 atmospheres. Bird recordings retain natural background noise and are not assigned species names. Rain and other atmospheres are not advertised as seamless loops. Full decoding and numeric checks are not a human listening review or a guarantee against analog clipping/audible distortion. No human listening pass has been performed. Summing tracks or increasing gain can still overload the mix.

The original compressed downloads, including the roughly 410 MB bird pack, are intentionally not shipped. There are no runtime CDN, download, paid API, activation or network requirements for these sounds. This integration is source-tested on Linux; new native Windows/macOS builds and speaker playback have not been validated.

## Publication contact redaction

The bamboo-swish pack notice omits only the author's contact email in this public copy. The author name, website, source and full licence statements remain. The four affected provenance entries record the original notice hash, this redaction and the updated distributed-notice hash. Audio bytes are unchanged.
