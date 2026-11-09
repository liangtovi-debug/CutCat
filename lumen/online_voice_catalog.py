"""Reference identities for optional online speech, without any service access.

This small catalog records provider-documented names and identifiers. A catalog
entry is not an installed voice, a configured service, permission to send text,
or a claim that synthesis has been tested. Every entry is unavailable in this
build. This module never contacts a provider, reads credentials, or falls back
to another provider or a local voice.

Azure identifiers belong to Azure AI Speech. They are not an Edge endpoint
configuration. The Doubao identifier belongs to that provider alone. Source
links are documentation references, not synthesis endpoints.
"""
from __future__ import annotations

from dataclasses import dataclass, field


AZURE_VOICE_SOURCE = (
    'https://learn.microsoft.com/en-us/azure/ai-services/speech-service/'
    'language-support?tabs=tts'
)
DOUBAO_VOICE_SOURCE = 'https://docs.volcengine.com/docs/DoubaoVoice/Tonelist-1?lang=zh'

ONLINE_PROVIDERS = ('azure', 'doubao')
UNAVAILABLE_REASON_CODE = 'no_authorized_service_configured'
UNAVAILABLE_REASON = (
    'No authorized online speech service is configured in this build. '
    'This voice is reference metadata only; preview and synthesis are unavailable.'
)


@dataclass(frozen=True, slots=True)
class OnlineVoice:
    """An exact provider identity, kept separate from service availability.

    ``locale`` is a catalog language tag, not a request parameter or a promise
    about supported synthesis input. Doubao uses the broad ``zh`` here: no
    Azure locale mapping is inferred from its voice name or identifier.
    """

    provider: str
    voice_id: str
    name: str
    locale: str
    source_url: str

    @property
    def key(self) -> str:
        return f'{self.provider}:{self.voice_id}'

    @property
    def provider_label(self) -> str:
        if self.provider == 'azure':
            return 'Microsoft Azure AI Speech'
        if self.provider == 'doubao':
            return '火山引擎 / 豆包语音'
        raise ValueError('Unknown online speech provider.')

    @property
    def label(self) -> str:
        return f'{self.name} · {self.provider_label}'


# Provider-documented identities only. Do not infer new voices or aliases from
# similar names, and do not replace an unavailable entry with a local voice.
ONLINE_VOICES: tuple[OnlineVoice, ...] = (
    OnlineVoice('azure', 'zh-CN-XiaoxiaoNeural', 'Xiaoxiao', 'zh-CN', AZURE_VOICE_SOURCE),
    OnlineVoice('azure', 'zh-TW-HsiaoChenNeural', 'HsiaoChen', 'zh-TW', AZURE_VOICE_SOURCE),
    OnlineVoice('azure', 'zh-TW-HsiaoYuNeural', 'HsiaoYu', 'zh-TW', AZURE_VOICE_SOURCE),
    OnlineVoice('azure', 'zh-TW-YunJheNeural', 'YunJhe', 'zh-TW', AZURE_VOICE_SOURCE),
    OnlineVoice('doubao', 'zh_female_wanwanxiaohe_moon_bigtts', '湾湾小何', 'zh',
                DOUBAO_VOICE_SOURCE),
)


@dataclass(frozen=True, slots=True)
class OnlineVoiceStatus:
    """Availability in this build, never inferred from catalog membership.

    The fixed flags cannot be supplied as constructor arguments. A future real
    service integration must establish its own authorized configuration and
    request consent; catalog data cannot enable synthesis.
    """

    voice: OnlineVoice
    configured: bool = field(default=False, init=False)
    available: bool = field(default=False, init=False)
    reason_code: str = field(default=UNAVAILABLE_REASON_CODE, init=False)
    reason: str = field(default=UNAVAILABLE_REASON, init=False)


def _validate_provider(provider: str) -> None:
    if not isinstance(provider, str) or provider not in ONLINE_PROVIDERS:
        raise ValueError('Choose an explicitly cataloged online speech provider: azure or doubao.')


def get_online_voice(provider: str, voice_id: str) -> OnlineVoice:
    """Resolve only an exact provider and documented ID, with no aliases."""
    _validate_provider(provider)
    if not isinstance(voice_id, str) or not voice_id:
        raise ValueError('Online voice ID must be an exact documented string.')
    for voice in ONLINE_VOICES:
        if voice.provider == provider and voice.voice_id == voice_id:
            return voice
    raise ValueError('Voice ID is not cataloged for the selected online speech provider.')


def list_online_voices(*, provider: str | None = None,
                       locale: str | None = None) -> tuple[OnlineVoice, ...]:
    """Return reference records filtered by exact provider/language tags.

    Unknown providers are errors; an unmatched language tag yields no entries.
    In particular, this does not assert that every Chinese voice is equivalent
    to a Taiwan voice, or that every catalog entry is usable online.
    """
    if provider is not None:
        _validate_provider(provider)
    if locale is not None and (not isinstance(locale, str) or not locale):
        raise ValueError('Catalog locale must be a nonempty language tag or None.')
    return tuple(voice for voice in ONLINE_VOICES
                 if (provider is None or voice.provider == provider)
                 and (locale is None or voice.locale == locale))


def get_online_voice_status(provider: str, voice_id: str) -> OnlineVoiceStatus:
    """Report explicit unavailability; this never probes a remote service."""
    return OnlineVoiceStatus(get_online_voice(provider, voice_id))
