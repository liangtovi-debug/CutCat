"""Versioned explicit-local speech identities. No inferred IDs or downloads.

Origin describes the upstream voice label, not a restriction on text language.
All listed packs have English/Chinese frontends. A voice is not a promise of
accent, gender, age, emotion, quality, or resemblance to any real person.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Voice:
    pack_version: str
    voice_id: int
    name: str
    origin: str
    origin_language: str
    text_languages: tuple[str, ...] = ('en', 'zh')

    @property
    def key(self):
        return f'{self.pack_version}:{self.voice_id}'

    @property
    def label(self):
        return f'{self.name} · {self.origin} · #{self.voice_id}'


@dataclass(frozen=True)
class VoicePack:
    version: str
    folder_name: str
    voices: tuple[Voice, ...]
    bank_size: int
    model_sha256: str
    voices_sha256: str
    archive_sha256: str | None
    required_files: tuple[str, ...]
    lexicons: tuple[str, ...]
    rule_fsts: tuple[str, ...] = ('phone-zh.fst', 'date-zh.fst', 'number-zh.fst')
    backend: str = 'kokoro'
    sample_rate: int = 24000
    model_asset: str = 'model.onnx'
    voice_asset: str = 'voices.bin'
    required_dirs: tuple[str, ...] = ('espeak-ng-data', 'dict')
    asset_sha256: tuple[tuple[str, str], ...] = ()
    max_num_sentences: int = 1
    max_text_chars: int = 100000
    experimental: bool = False
    source_revision: str = ''

    @property
    def display_name(self):
        return f'Kokoro {self.version}' if self.backend == 'kokoro' else 'Melo Chinese-English'

    @property
    def engine(self):
        return ('sherpa-onnx/Kokoro-' + self.version if self.backend == 'kokoro'
                else 'sherpa-onnx/MeloTTS-' + self.version)

    @property
    def expected_hashes(self):
        return {self.model_asset: self.model_sha256, self.voice_asset: self.voices_sha256,
                **dict(self.asset_sha256)}

    @property
    def download_url(self):
        if self.archive_sha256 is None:
            return None  # This pack was verified file-by-file, not as an archive.
        return ('https://github.com/k2-fsa/sherpa-onnx/releases/download/tts-models/'
                + self.folder_name + '.tar.bz2')


_V1_NAMES = ('af_alloy af_aoede af_bella af_heart af_jessica af_kore af_nicole '
             'af_nova af_river af_sarah af_sky am_adam am_echo am_eric am_fenrir '
             'am_liam am_michael am_onyx am_puck am_santa bf_alice bf_emma '
             'bf_isabella bf_lily bm_daniel bm_fable bm_george bm_lewis ef_dora '
             'em_alex ff_siwis hf_alpha hf_beta hm_omega hm_psi if_sara im_nicola '
             'jf_alpha jf_gongitsune jf_nezumi jf_tebukuro jm_kumo pf_dora pm_alex '
             'pm_santa zf_xiaobei zf_xiaoni zf_xiaoxiao zf_xiaoyi zm_yunjian '
             'zm_yunxi zm_yunxia zm_yunyang').split()
# Exact official mapping, including intentional gaps in the Chinese name suffixes.
_V11_NAMES = ('af_maple af_sol bf_vale '
    'zf_001 zf_002 zf_003 zf_004 zf_005 zf_006 zf_007 zf_008 zf_017 zf_018 '
    'zf_019 zf_021 zf_022 zf_023 zf_024 zf_026 zf_027 zf_028 zf_032 zf_036 '
    'zf_038 zf_039 zf_040 zf_042 zf_043 zf_044 zf_046 zf_047 zf_048 zf_049 '
    'zf_051 zf_059 zf_060 zf_067 zf_070 zf_071 zf_072 zf_073 zf_074 zf_075 '
    'zf_076 zf_077 zf_078 zf_079 zf_083 zf_084 zf_085 zf_086 zf_087 zf_088 '
    'zf_090 zf_092 zf_093 zf_094 zf_099 '
    'zm_009 zm_010 zm_011 zm_012 zm_013 zm_014 zm_015 zm_016 zm_020 zm_025 '
    'zm_029 zm_030 zm_031 zm_033 zm_034 zm_035 zm_037 zm_041 zm_045 zm_050 '
    'zm_052 zm_053 zm_054 zm_055 zm_056 zm_057 zm_058 zm_061 zm_062 zm_063 '
    'zm_064 zm_065 zm_066 zm_068 zm_069 zm_080 zm_081 zm_082 zm_089 zm_091 '
    'zm_095 zm_096 zm_097 zm_098 zm_100').split()
_ORIGINS = {'a': ('US English', 'en'), 'b': ('British English', 'en'),
            'e': ('Spanish', 'es'), 'f': ('French', 'fr'), 'h': ('Hindi', 'hi'),
            'i': ('Italian', 'it'), 'j': ('Japanese', 'ja'),
            'p': ('Brazilian Portuguese', 'pt'), 'z': ('Chinese', 'zh')}


def _voices(version, names):
    return tuple(Voice(version, index, name, *_ORIGINS[name[0]])
                 for index, name in enumerate(names))


_COMMON_FILES = ('model.onnx', 'voices.bin', 'tokens.txt', 'lexicon-us-en.txt',
                 'lexicon-zh.txt', 'phone-zh.fst', 'date-zh.fst', 'number-zh.fst')
PACKS = {
    'v1.0': VoicePack('v1.0', 'kokoro-multi-lang-v1_0', _voices('v1.0', _V1_NAMES), 54,
        'b40f62b166ac8164b0627ef48a0b358eda0985e272fb03ef5252e7206305da11',
        '1c5a5b983d3d50d8586d437a51f3faa2da7919ce76a013c081e65671a3447c29',
        'c5f7e2d2caf082bc1d20fb70334a61d99d20b484500aad32e7cf84c128ea3298',
        _COMMON_FILES, ('lexicon-us-en.txt', 'lexicon-zh.txt')),
    'v1.1': VoicePack('v1.1', 'kokoro-multi-lang-v1_1', _voices('v1.1', _V11_NAMES), 103,
        'acc4adc175b9d9986106cd20060329673ad5a2e12ef3c557d2d3745b694f8b38',
        'e64a5a581d8c2a350d848f51c3121657cd83aa07ed6109172177345874a7244c',
        'a3f4c73d043860e3fd2e5b06f36795eb81de0fc8e8de6df703245edddd87dbad',
        _COMMON_FILES, ('lexicon-us-en.txt', 'lexicon-zh.txt')),
    # Official file revision evaluated on CPU with the pinned sherpa runtime.
    # This single speaker's weights are embedded in model.onnx: its voice hash
    # is the actual model-file hash, never a fabricated separate voice bank.
    'melo-zh_en-v1': VoicePack(
        'melo-zh_en-v1', 'vits-melo-tts-zh_en',
        (Voice('melo-zh_en-v1', 0, 'Melo Chinese-English', 'Chinese + English', 'zh'),), 1,
        'bf30582eb1b012250a35b1a4a80e7dfbcf8485e7bb9de0d95efbbeef0e4ad86d',
        'bf30582eb1b012250a35b1a4a80e7dfbcf8485e7bb9de0d95efbbeef0e4ad86d',
        None, ('model.onnx', 'tokens.txt', 'lexicon.txt', 'date.fst', 'number.fst'),
        ('lexicon.txt',), ('date.fst', 'number.fst'),
        backend='melo', sample_rate=44100, voice_asset='model.onnx', required_dirs=(),
        asset_sha256=(
            ('model.onnx', 'bf30582eb1b012250a35b1a4a80e7dfbcf8485e7bb9de0d95efbbeef0e4ad86d'),
            ('tokens.txt', 'd18664a7e12bd7ea1022ddaf951e534e136815016c5a809d6b64156bffb4369d'),
            ('lexicon.txt', '7236884b02435ac5d10cf69b4be40a61b45aa676b5300f0e412f185748fee528'),
            ('date.fst', 'eb8aa079ae3cb81d8f4404992f39d61a0cb990947512b5b8d1e54d1f6980e718'),
            ('number.fst', '743f402181fcfebf76cc2f0546b71fa26476e626fbe4e460fb7b4c3a7a8bd5bd'),
        ), max_num_sentences=0, max_text_chars=300, experimental=True,
        source_revision='a0d5c6a264c0ef92d70d8661d8cc502d79627cd6'),
}


def get_pack(version: str) -> VoicePack:
    if not isinstance(version, str) or version not in PACKS:
        raise ValueError('Choose an explicitly supported local speech pack version: ' + ', '.join(PACKS) + '.')
    return PACKS[version]


def get_voice(version: str, voice_id: int) -> Voice:
    pack = get_pack(version)
    if type(voice_id) is not int:
        raise ValueError('Voice ID must be a documented integer for the selected pack.')
    for voice in pack.voices:
        if voice.voice_id == voice_id:
            return voice
    raise ValueError(f'Voice ID {voice_id} is not documented for {pack.display_name}.')


def filter_voices(version: str, *, origin: str = '', text_language: str = '',
                  favorites=None, favorites_only=False) -> tuple[Voice, ...]:
    """Filters distinguish origin from supported text; no language-exclusive claim."""
    return tuple(voice for voice in get_pack(version).voices
                 if (not origin or voice.origin == origin)
                 and (not text_language or text_language in voice.text_languages)
                 and (not favorites_only or voice.key in (favorites or ())))
