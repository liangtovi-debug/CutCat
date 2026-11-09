"""Pure-stdlib catalog checks; run without Qt or an online speech SDK.

python -m unittest discover -s tests -p test_online_voice_catalog.py -v
"""
from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
from pathlib import Path
import subprocess
import sys
import unittest

from lumen.online_voice_catalog import (
    AZURE_VOICE_SOURCE, DOUBAO_VOICE_SOURCE, ONLINE_PROVIDERS, ONLINE_VOICES,
    UNAVAILABLE_REASON, UNAVAILABLE_REASON_CODE, OnlineVoiceStatus,
    get_online_voice, get_online_voice_status, list_online_voices,
)


class OnlineVoiceCatalogTests(unittest.TestCase):
    def test_exact_documented_provider_specific_ids(self):
        self.assertEqual(ONLINE_PROVIDERS, ('azure', 'doubao'))
        self.assertEqual(
            tuple((voice.provider, voice.voice_id) for voice in ONLINE_VOICES),
            (
                ('azure', 'zh-CN-XiaoxiaoNeural'),
                ('azure', 'zh-TW-HsiaoChenNeural'),
                ('azure', 'zh-TW-HsiaoYuNeural'),
                ('azure', 'zh-TW-YunJheNeural'),
                ('doubao', 'zh_female_wanwanxiaohe_moon_bigtts'),
            ),
        )
        self.assertEqual(len({voice.key for voice in ONLINE_VOICES}), 5)
        for voice in ONLINE_VOICES:
            with self.subTest(voice=voice.key):
                self.assertIs(get_online_voice(voice.provider, voice.voice_id), voice)
                self.assertEqual(voice.key, f'{voice.provider}:{voice.voice_id}')

    def test_official_sources_and_provider_labels_are_not_endpoint_aliases(self):
        self.assertEqual(AZURE_VOICE_SOURCE,
                         'https://learn.microsoft.com/en-us/azure/ai-services/'
                         'speech-service/language-support?tabs=tts')
        self.assertEqual(DOUBAO_VOICE_SOURCE,
                         'https://docs.volcengine.com/docs/DoubaoVoice/Tonelist-1?lang=zh')
        for voice in list_online_voices(provider='azure'):
            self.assertEqual(voice.source_url, AZURE_VOICE_SOURCE)
            self.assertEqual(voice.provider_label, 'Microsoft Azure AI Speech')
            self.assertNotIn('Edge', voice.label)
        voice = get_online_voice('doubao', 'zh_female_wanwanxiaohe_moon_bigtts')
        self.assertEqual(voice.source_url, DOUBAO_VOICE_SOURCE)
        self.assertEqual(voice.name, '湾湾小何')
        self.assertEqual(voice.provider_label, '火山引擎 / 豆包语音')
        self.assertIn(voice.name, voice.label)
        self.assertIn(voice.provider_label, voice.label)

    def test_catalog_and_records_are_immutable(self):
        self.assertIsInstance(ONLINE_VOICES, tuple)
        self.assertIsInstance(ONLINE_PROVIDERS, tuple)
        voice = ONLINE_VOICES[0]
        for attribute, value in (('provider', 'edge'), ('voice_id', 'invented'),
                                 ('name', 'Fake'), ('locale', 'zh-TW'),
                                 ('source_url', 'https://example.invalid')):
            with self.subTest(attribute=attribute):
                with self.assertRaises(FrozenInstanceError):
                    setattr(voice, attribute, value)

    def test_every_known_voice_is_explicitly_unavailable(self):
        for voice in ONLINE_VOICES:
            with self.subTest(voice=voice.key):
                status = get_online_voice_status(voice.provider, voice.voice_id)
                self.assertIs(status.voice, voice)
                self.assertIs(status.configured, False)
                self.assertIs(status.available, False)
                self.assertEqual(status.reason_code, UNAVAILABLE_REASON_CODE)
                self.assertEqual(status.reason, UNAVAILABLE_REASON)
                self.assertIn('No authorized online speech service is configured', status.reason)
                self.assertIn('preview and synthesis are unavailable', status.reason)

    def test_status_cannot_be_enabled_by_constructor_or_mutation(self):
        voice = ONLINE_VOICES[0]
        status = get_online_voice_status(voice.provider, voice.voice_id)
        with self.assertRaises(TypeError):
            OnlineVoiceStatus(voice, available=True)
        with self.assertRaises(TypeError):
            OnlineVoiceStatus(voice, configured=True)
        with self.assertRaises(FrozenInstanceError):
            status.available = True
        with self.assertRaises(FrozenInstanceError):
            status.configured = True
        with self.assertRaises(ValueError):
            replace(status, available=True)

    def test_filters_are_exact_and_do_not_invent_locale_mappings(self):
        self.assertEqual(list_online_voices(), ONLINE_VOICES)
        self.assertEqual(len(list_online_voices(provider='azure')), 4)
        self.assertEqual(len(list_online_voices(provider='doubao')), 1)
        taiwan = list_online_voices(provider='azure', locale='zh-TW')
        self.assertEqual(tuple(voice.name for voice in taiwan),
                         ('HsiaoChen', 'HsiaoYu', 'YunJhe'))
        self.assertEqual(len(list_online_voices(locale='zh-CN')), 1)
        self.assertEqual(list_online_voices(provider='doubao', locale='zh-TW'), ())
        self.assertEqual(list_online_voices(provider='doubao', locale='zh')[0].name, '湾湾小何')
        self.assertEqual(list_online_voices(locale='en-US'), ())
        self.assertEqual(list_online_voices(locale='zh-tw'), ())
        self.assertEqual(list_online_voices(locale='zh_TW'), ())

    def test_no_aliases_cross_provider_substitutions_or_local_voice_fallbacks(self):
        for provider, voice_id in (
            ('edge', 'zh-CN-XiaoxiaoNeural'),
            ('azure', 'Xiaoxiao'),
            ('azure', 'zh-cn-xiaoxiaoneural'),
            ('azure', 'zh-TW-XiaoxiaoNeural'),
            ('azure', 'zh-CN-XiaoxiaoNeural '),
            ('azure', 'zf_xiaoxiao'),
            ('azure', 'zh_female_wanwanxiaohe_moon_bigtts'),
            ('doubao', 'zh-CN-XiaoxiaoNeural'),
            ('doubao', '湾湾小何'),
            ('doubao', 'zh_female_wanwanxiaohe'),
        ):
            with self.subTest(provider=provider, voice_id=voice_id):
                with self.assertRaises(ValueError):
                    get_online_voice(provider, voice_id)
                with self.assertRaises(ValueError):
                    get_online_voice_status(provider, voice_id)

    def test_invalid_provider_and_voice_types_are_rejected_cleanly(self):
        for provider in (None, '', 'Azure', 'edge', 'kokoro', True, 1, [], {}):
            with self.subTest(provider=provider):
                with self.assertRaises(ValueError):
                    get_online_voice(provider, 'zh-CN-XiaoxiaoNeural')
                if provider is not None:  # None intentionally means all providers.
                    with self.assertRaises(ValueError):
                        list_online_voices(provider=provider)
        for voice_id in (None, '', True, 1, 1.5, [], {}):
            with self.subTest(voice_id=voice_id):
                with self.assertRaises(ValueError):
                    get_online_voice('azure', voice_id)
        for locale in ('', True, 1, [], {}):
            with self.subTest(locale=locale):
                with self.assertRaises(ValueError):
                    list_online_voices(locale=locale)

    def test_import_and_queries_require_no_qt_sdk_or_network(self):
        # A fresh interpreter with site-packages disabled prevents accidental
        # dependency on Qt/provider SDKs. All socket activity is forbidden.
        script = '''
import builtins
import socket

def forbidden(*args, **kwargs):
    raise AssertionError('Catalog must not use network access')

socket.socket = forbidden
socket.create_connection = forbidden
socket.getaddrinfo = forbidden
original_import = builtins.__import__

def restricted_import(name, *args, **kwargs):
    if name.split('.')[0] in {'PySide6', 'azure', 'edge_tts', 'requests', 'httpx'}:
        raise AssertionError('Catalog must not import Qt, transport, or provider SDKs')
    return original_import(name, *args, **kwargs)

builtins.__import__ = restricted_import
from lumen.online_voice_catalog import list_online_voices, get_online_voice_status
voices = list_online_voices()
assert len(voices) == 5
for voice in voices:
    status = get_online_voice_status(voice.provider, voice.voice_id)
    assert not status.configured and not status.available
'''
        result = subprocess.run([sys.executable, '-S', '-c', script],
                                cwd=Path(__file__).resolve().parents[1],
                                capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == '__main__':
    unittest.main()
