# Optional online voice preparation / 可选联网音色准备

## Status / 当前范围

This iteration adds a read-only voice directory and a tested, fail-closed
request-consent interface. **No online synthesis backend is connected.**
The directory sends no text, takes no credentials, and cannot generate audio.
Local Kokoro/Melo workflows remain unchanged. No project schema migration is
needed. This is Linux source validation, not a new Windows/macOS build.

本轮仅提供真实音色目录、准确的未配置状态和后续接入用的安全接口。
联网生成按钮不可用；没有后台下载、自动转云、收费调用或密钥保存。
不会修改字幕原文或现有本地配音设置。

## Verified voices / 已核实目录

Microsoft Azure Speech: `zh-CN-XiaoxiaoNeural`,
`zh-TW-HsiaoChenNeural`, `zh-TW-HsiaoYuNeural`, `zh-TW-YunJheNeural`.
These are documented Azure voices, not proof of availability in Edge or in
any customer resource/region. Source:
https://learn.microsoft.com/en-us/azure/ai-services/speech-service/language-support?tabs=tts#text-to-speech

湾湾小何 is **Doubao / Volcengine**, voice ID
`zh_female_wanwanxiaohe_moon_bigtts`, officially described as Chinese with a
Taiwan accent. It is not an alias for a Microsoft voice. Source:
https://docs.volcengine.com/docs/DoubaoVoice/Tonelist-1?lang=zh

## Why Edge is not connected / Edge 接入边界

The user's confirmed reference repository is a three-document OpenClaw skill,
not a local model. Its examples recommend Xiaoxiao but do not establish which
provider was actually used:
https://github.com/liangtovi-debug/openclaw-skill-voice-reply/tree/2b7d5169fe18cd8e02a6ddc3abdc031b4f592598

Current OpenClaw passes text to third-party node-edge-tts. The inspected
client uses a shared Edge token and browser-style identity. Microsoft's
September 2026 Edge license restricts using Edge tokens to call services
separately from Edge (Windows section 1.3; non-Windows section 2.8(f)).
This route did not pass our service-access review; no request was made.
The client MIT license does not establish permission to use Microsoft's service.

- https://github.com/openclaw/openclaw/blob/e559b9eaad7e85fb62384f1a16f7dcc2d44c5c11/extensions/microsoft/speech-provider.ts
- https://github.com/SchneeHertz/node-edge-tts/blob/5fd566bbc30e305a605fc0afc99c98bfbad9d8be/src/edge-tts.ts
- https://explore.microsoft.com/en-us/edge/server/download?form=MA13FJ

## Future integration gate / 接通前要求

Use documented Azure Speech or Doubao APIs with an authorized resource,
reviewed terms, a secure credential mechanism and an approved usage budget.
Do not ask for secrets in chat or embed shared service tokens. Catalog presence
does not imply commercial output rights. Azure's prebuilt neural output grant
in Product Terms expressly refers to paid-tier customers; verify the applicable
customer agreement rather than infer rights from a free quota.

- https://learn.microsoft.com/en-us/azure/ai-services/speech-service/rest-text-to-speech
- https://www.microsoft.com/licensing/terms/en-US/product/MicrosoftAzure/all

Before each future request, show the exact reading text, voice, provider and
resource/recipient. Approval must be invalidated by any change and must not be
persisted with the project. Cancel/timeout/error must not publish a partial take.
The interface's cooperative deadline is not a substitute for bounded transport
I/O. Real retries, rate limiting, audio decoding, exact duration, cache publication,
and timeline insertion still require actual authorized backend integration tests.

No online audio sample, latency, pronunciation quality or native-platform
compatibility is claimed by this milestone. No GitHub push or CI was run.

## Local validation (2026-10-19)

- Frozen related suite: **121 passed in 5.35 seconds**, clean exit 0.
- Pre/post hashes: 289 files unchanged during that run (manifest included).
- Coverage: catalog identity/unavailability, 32 consent/transport-mock cases,
  English/Chinese read-only Qt directory in Simple/Professional modes,
  repeated open/close, locale changes, unchanged project/revision, existing
  local TTS/Melo/text-review flows and localization checks.
- Actual Linux offscreen Qt screenshots inspected in English and Chinese at
  100%, and Chinese at 150% scale; no visible clipping or missing Chinese glyphs.
- Early development checks found and fixed a translation argument collision;
  a layout assertion was corrected to measure height at the actual widget width.
  Independent review also prompted request-copy and huge-number validation
  regressions. No failing check was counted as passing.
- This is a focused regression run, not a new full-suite pass. Qt playback
  hardware and Windows/macOS native packaging were not validated this iteration.
- No real service call, credentials, generated online audio or paid usage.
- Independent read-only review: 46 new focused tests passed, clean exit 0;
  additional in-memory checks exercised 20 close/reopen/locale cycles, all five
  voice labels' layout, socket prohibition, copied-request rejection and huge/
  nonfinite numbers. No remaining blocking finding within this offline scope.
