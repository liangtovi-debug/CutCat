# Tutorial research evidence ledger

Research consolidated 2026-10-15. This is a record of **what was accessible**, not proof of watching a video, installing a competitor, or benchmarking its behavior. Technical conclusions use official sources where available; proposed product behavior is labeled as our design inference in [the design note](TUTORIAL_WORKFLOW_DESIGN.md).

## Access categories

- **Page text:** retrieved webpage text was read; no desktop application test implied.
- **Indexed excerpt:** search-indexed passage only; not the full document.
- **Description/chapters:** lesson metadata used to locate topics; not observed instruction or verified results.
- **Publisher transcript:** written lesson transcript was read. This does not mean video playback occurred.

**No tutorial video playback was obtained. No full Resolve PDF was read. One Ripple publisher transcript was read.** Chapter markers below are metadata, not recommended scene timestamps or confirmation of viewing.

## DaVinci Resolve

| Source | Access and supported use | Limits |
|---|---|---|
| [Official training catalog](https://www.blackmagicdesign.com/products/davinciresolve/training) | Page text: Introduction to Color (1h33m), Advanced Color (57m), Color Management (31m), lesson topics | Catalog durations/topics, not lesson playback |
| [Introduction to Color](https://www.youtube.com/watch?v=OrEcXbET1Y4) | Link verified through official catalog | Direct YouTube open failed; no transcript/playback |
| [Advanced Color](https://www.youtube.com/watch?v=zvwmgOeXd28) | Official catalog + indexed description | Direct YouTube open failed; no transcript/playback |
| [Color Management](https://www.youtube.com/watch?v=CTAzjAReZvs) | Link verified through official catalog | Direct YouTube open failed; no transcript/playback |
| [Official Color page](https://www.blackmagicdesign.com/products/davinciresolve/color) | Page text: Primary Lift/Gamma/Gain/Offset, overlapping influence, tighter Log ranges, customizable HDR zones | Not a disclosure of exact proprietary equations |
| [Resolve 20 Colorist Guide](https://documents.blackmagicdesign.com/UserManuals/DaVinci-Resolve-20-Colorist-Guide.pdf) | Indexed excerpts: printed p32 Log Low/High Range; p394 HDR wheel banking; p397 Black/Dark/Shadow/Light/Highlight/Specular + Global | Full PDF open failed on 24MB document; no full-PDF or page-by-page claim |
| [Resolve 10 Reference Manual](https://documents.blackmagicdesign.com/UserManuals/DaVinci_Resolve_10_Reference_Manual.pdf) | Indexed p420 excerpt explicitly names four Log wheels Shadow/Midtone/Highlight/Offset | Historical version evidence only; not exact current UI-layout verification |
| [Supplemental wheel overview](https://www.youtube.com/watch?v=RRZlCWRuKOc) | Description/chapters: 0:17 Primary, 2:58 Log, 5:27 HDR, 8:27 grade | Topic-navigation evidence only; not technical authority or watched demonstration |

No equation for Resolve's proprietary Primary/Log/HDR operators was verified. Our existing LGG formula must remain separately documented; naming similar controls does not establish numerical equivalence. DVResolve outlines were consulted for locating scope, not used as the authoritative source for mathematical claims.

## Jianying China and international CapCut

These products/platforms/versions are not merged into a single tested feature list.

| Source | Access and supported use | Limits |
|---|---|---|
| [Jianying China official site](https://www.capcut.cn/) | Page text: desktop/pro masks (linear/circular/text/pen), keyframes, wheels/HSL/hue-saturation/RGB curves/float color, 4-/9-view multicam, up to 50 timelines per draft, speech/text tools | Current site claim; no exact installed executable/build tested. Feature presence does not prove quality or local AI execution |
| [CapCut PC guide](https://www.capcut.com/resource/how-to-use-capcut-on-pc) | Page text: language-selected auto captions, editable timeline text, text-to-voice workflow, export settings | International CapCut guide, not proof of China Jianying parity or offline behavior |
| [CapCut auto-caption help](https://www.capcut.com/help/auto-captions) | Page text explicitly discusses speech recognition/cloud processing and connectivity failures, including desktop context | Supports this feature's network dependence; not a blanket claim about all Jianying/CapCut AI |
| [CapCut subtitle recognition help](https://www.capcut.com/help/how-to-recognise-subtitles) | Page text: platform variation and regional speaker-detection availability | Do not generalize regional/platform options |
| [2025 desktop beginner lesson](https://www.youtube.com/watch?v=XWUbaVnFXuk) | Indexed description: approximately 18-minute Jianying desktop/pro beginner lesson; editing/transitions/captions/effects/export topics | YouTube page exposed boilerplate; no transcript/playback |
| [2025 desktop course](https://www.youtube.com/watch?v=-EM0Ho3Nplw) | Indexed chapter metadata includes primary color and captions | Direct open cache miss; no exact executable version or watched segments |
| [2025 keyframe lesson](https://www.youtube.com/watch?v=y8ij1VXPDaM) | Indexed description/course syllabus: timeline/audio/text/effects/HSL/curves/wheels/speed/masks/keyframes/compounds/export | Direct open cache miss; no transcript/playback |

No Jianying/CapCut tutorial transcript was obtained. The tutorial labels are from 2025, while the official page was read in 2026; they are not treated as one version. Offline implementation in Lumen is a user requirement, not a capability inferred from these competitors.

## Final Cut Pro / Ripple Training

| Source | Access and supported use | Limits |
|---|---|---|
| [Ripple: 4K Multicam Magic](https://www.rippletraining.com/blog/final-cut-pro-x/4k-multicam-magic/) | **Publisher transcript read:** angle editor; add/name Singer XCU; duplicate source angle; monitoring button; scale/reframe; navigate back and cut to synthetic close-up | No video playback; publisher page date is not asserted as recording date; reframing is not another real camera |
| [Audio Finishing using Roles video](https://www.youtube.com/watch?v=h4XchWCEFwg) and [publisher page](https://www.rippletraining.com/blog/final-cut-pro-x/audio-finishing-using-roles-in-fcp/) | Description/chapters: 02:17 roles, 02:51 lanes, 03:42 subroles, 05:04 priority, 06:05 focus, 06:41 mixdown, 07:56 effect order, 10:07 meter target | No playback/transcript evidence for this lesson; no claimed measured audio result |
| [Top 10 Multicam Editing Tips video](https://www.youtube.com/watch?v=IMS0iDOg3Q4) and [publisher page](https://www.rippletraining.com/blog/final-cut-pro-x/top-10-multicam-editing-tips/) | Publisher embed verifies video ID; description/chapters: 01:19 codec/storage, 04:33 sync, 08:12 custom roles, 13:11 progressive disclosure, 21:08 audio | No playback; chapter topics only |
| [FCP 10.8 walkthrough](https://www.youtube.com/watch?v=oiJlUPix7jM) | Indexed description/chapters: 03:49 automatic-color adjustment; 06:25 Balance vs Enhance; 08:00 Log; 08:55 iPhone HDR; 11:34 matching; 12:49 multicam; 19:28 optical-flow artifacts | Exact title unavailable; version 10.8 label; direct open failed; no watched lesson |
| [Apple storylines/magnetic timeline](https://support.apple.com/en-ca/guide/final-cut-pro/verb8fcfc133/mac) and [anchors](https://support.apple.com/nl-nl/guide/final-cut-pro/ver7a77ef9e/mac) | Official guide text: ripple/collision prevention, connected movement, Position tool/gaps, reanchoring | Affected-object preview and atomic undo requirements are our inference |
| [Apple secondary storylines](https://support.apple.com/en-eg/guide/final-cut-pro/ver8e3f1748/mac) | Official guide text: contextual internal transitions/trims | Not the same as simple multi-selection grouping |
| [Apple compound editing](https://support.apple.com/guide/final-cut-pro/edit-compound-clips-verca9e8672/12.4/mac/26.6) | Official guide text: nested navigation, outer duration boundaries | Versioned 12.4 guide URL; not silently merged with the 10.8 lesson |
| [Apple compound references](https://support.apple.com/en-ie/guide/final-cut-pro/verca9e8d33/mac) and [snapshots](https://support.apple.com/en-is/guide/final-cut-pro/verbbd3496b/mac) | Official guide text: shared parent, Reference New Parent Clip, snapshot isolation | Independent-copy semantics require explicit design; ordinary copying is not assumed isolated |
| [Apple audio roles](https://support.apple.com/en-gb/guide/final-cut-pro/verb1466e0bb/mac) | Official guide text: components, roles/subroles, nested propagation/consolidation; caption language subroles | Roles are not automatically buses. No full caption-editing/export tutorial was studied |
| [Apple Audio Roles PDF](https://www.apple.com/final-cut-pro/docs/Audio_Roles.pdf) | Search excerpt only | Full PDF not read; guide text is the primary support above |
| [Apple multicam switching](https://support.apple.com/en-lk/guide/final-cut-pro/ver23c76c9c/mac) and [angles/cuts](https://support.apple.com/en-gb/guide/final-cut-pro/ver23c76d65/mac) | Official guide text: cut-and-switch vs existing-segment switch, separate audio/video modes, active labels, roll through-cuts, angle-specific processing | Our acceptance scenario is video cuts with continuous good microphone audio |
| [Apple color correction](https://support.apple.com/en-asia/guide/final-cut-pro/ver761ca98b/mac) and [color metadata workflow](https://support.apple.com/en-euro/guide/final-cut-pro/ver761cb1ff/mac) | Official guide text: correction/scopes and dependence on source metadata | Does not validate Lumen's display or imply its SDR math is full color management |

Apple links retain the verified locale/version URLs returned during research; locale does not denote a different engine. No unsupported cross-version equivalence is assumed.

## Remaining research gates

- Obtain actual playable lessons or complete authorized transcripts before claiming hands-on course coverage.
- Install/test exact competitor versions only if separately scoped; current evidence is documentation, metadata and one written transcript.
- For implementation: validate official camera transform mathematics/licensing and CPU reference results; independently review offline model/asset dependencies. Tutorial popularity cannot waive these gates.
- This document does not authorize publishing code, installing proprietary packs, accepting new licenses, creating a repository or running native CI.
