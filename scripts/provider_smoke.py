"""Opt-in live provider smoke tests. Nothing runs unless credentials and models are supplied.

Each provider runs only when its environment variables are set; secrets are read
through ``env:`` references and never printed or written. Hosted calls also
require ``--allow-remote``. The JSON report records date, model, language and
outcome per capability so it can be attached as acceptance evidence.

  OPENAI_API_KEY + BYAX_SMOKE_OPENAI_MODEL [+ BYAX_SMOKE_OPENAI_STT_MODEL]
  ANTHROPIC_API_KEY + BYAX_SMOKE_ANTHROPIC_MODEL
  GEMINI_API_KEY + BYAX_SMOKE_GEMINI_MODEL
  BYAX_SMOKE_OLLAMA_MODEL [+ BYAX_SMOKE_OLLAMA_ENDPOINT]
  ELEVENLABS_API_KEY + BYAX_SMOKE_ELEVENLABS_MODEL
  BYAX_SMOKE_WHISPER_EXECUTABLE + BYAX_SMOKE_WHISPER_MODEL
  BYAX_SMOKE_AUDIO (16 kHz mono 16-bit WAV speech sample), BYAX_SMOKE_LANGUAGE (default en)

Usage: python scripts/provider_smoke.py --allow-remote [--out evidence.json]
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from byax.errors import ByaxError
from byax.providers import ProviderConfig, ProviderRegistry, list_capabilities

SAMPLE = 'Their going to the libary tomorow to return the books.'
PLANS = [
    ('openai', 'OPENAI_API_KEY', 'BYAX_SMOKE_OPENAI_MODEL', 'BYAX_SMOKE_OPENAI_STT_MODEL'),
    ('anthropic', 'ANTHROPIC_API_KEY', 'BYAX_SMOKE_ANTHROPIC_MODEL', None),
    ('gemini', 'GEMINI_API_KEY', 'BYAX_SMOKE_GEMINI_MODEL', None),
    ('ollama', None, 'BYAX_SMOKE_OLLAMA_MODEL', None),
    ('elevenlabs', 'ELEVENLABS_API_KEY', None, 'BYAX_SMOKE_ELEVENLABS_MODEL'),
    ('whisper.cpp', None, None, 'BYAX_SMOKE_WHISPER_MODEL'),
]


def run(name, fn):
    started = time.monotonic()
    try:
        result = fn()
        return {
            'check': name,
            'outcome': 'passed',
            'seconds': round(time.monotonic() - started, 2),
            'detail': result,
        }
    except ByaxError as exc:
        return {
            'check': name,
            'outcome': 'failed',
            'seconds': round(time.monotonic() - started, 2),
            'error': exc.to_dict(),
        }


def summarize_review(result):
    return {
        'findings': len(result['findings']),
        'proposed_text': result.get('proposed_text'),
        'usage_summary': result.get('usage_summary'),
        'request_id': result.get('request_id'),
    }


def summarize_transcript(result):
    return {
        'text': result['text'],
        'segments': len(result.get('segments', [])),
        'timestamps': result.get('timestamps'),
        'streamed': result.get('streamed'),
        'usage_summary': result.get('usage_summary'),
    }


def main(argv=None):
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument('--allow-remote', action='store_true', help='authorize hosted inference calls')
    parser.add_argument('--out', help='write the JSON evidence report here')
    args = parser.parse_args(argv)
    language = os.environ.get('BYAX_SMOKE_LANGUAGE', 'en')
    audio = os.environ.get('BYAX_SMOKE_AUDIO')
    capabilities = {row['kind']: row for row in list_capabilities()}
    report = {
        'date': datetime.datetime.now(datetime.UTC).isoformat(),
        'language': language,
        'audio_sample': audio,
        'providers': [],
    }
    with tempfile.TemporaryDirectory(prefix='byax-smoke-') as config_dir:
        registry = ProviderRegistry(config_dir)
        for kind, key_env, review_env, speech_env in PLANS:
            entry = {'kind': kind, 'capabilities': capabilities[kind], 'checks': []}
            report['providers'].append(entry)
            if key_env and not os.environ.get(key_env):
                entry['skipped'] = f'{key_env} is not set'
                continue
            review_model = os.environ.get(review_env) if review_env else None
            speech_model = os.environ.get(speech_env) if speech_env else None
            if not review_model and not speech_model:
                entry['skipped'] = 'no model configured for this provider'
                continue
            remote = kind not in {'ollama', 'whisper.cpp'}
            if remote and not args.allow_remote:
                entry['skipped'] = 'hosted calls require --allow-remote'
                continue
            common = {'credential_ref': 'env:' + key_env if key_env else None, 'timeout': 120}
            if kind == 'ollama' and os.environ.get('BYAX_SMOKE_OLLAMA_ENDPOINT'):
                common['endpoint'] = os.environ['BYAX_SMOKE_OLLAMA_ENDPOINT']
            if review_model:
                alias = kind + '-review'
                registry.save(ProviderConfig(alias, kind, review_model, **common))
                entry['review_model'] = review_model
                entry['checks'].append(
                    run('connection', lambda alias=alias: registry.test(alias, authorized=True))
                )
                entry['checks'].append(
                    run(
                        'text_review',
                        lambda alias=alias: summarize_review(
                            registry.review(alias, SAMPLE, context={'revision': 'smoke'}, authorized=True)
                        ),
                    )
                )
            if speech_model:
                alias = kind + '-speech'
                extra = (
                    {'executable': os.environ.get('BYAX_SMOKE_WHISPER_EXECUTABLE')}
                    if kind == 'whisper.cpp'
                    else {}
                )
                registry.save(ProviderConfig(alias, kind, speech_model, **common, **extra))
                entry['speech_model'] = speech_model
                if not audio:
                    entry['checks'].append(
                        {
                            'check': 'audio_transcription',
                            'outcome': 'skipped',
                            'reason': 'BYAX_SMOKE_AUDIO is not set',
                        }
                    )
                    continue
                entry['checks'].append(
                    run(
                        'audio_transcription',
                        lambda alias=alias, kind=kind: summarize_transcript(
                            registry.transcribe(
                                alias,
                                audio,
                                language=language,
                                authorized=True,
                                timestamps=kind != 'whisper.cpp',
                            )
                        ),
                    )
                )
                events = []
                entry['checks'].append(
                    run(
                        'streaming_transcription',
                        lambda alias=alias, events=events: {
                            **summarize_transcript(
                                registry.transcribe_stream(
                                    alias, audio, on_event=events.append, language=language, authorized=True
                                )
                            ),
                            'provisional_events': sum(event['type'] == 'provisional' for event in events),
                            'final_events': sum(event['type'] == 'final' for event in events),
                        },
                    )
                )
    text = json.dumps(report, ensure_ascii=False, indent=2)
    if args.out:
        Path(args.out).write_text(text, encoding='utf-8')
    print(text)
    failed = any(
        check.get('outcome') == 'failed' for entry in report['providers'] for check in entry['checks']
    )
    return 1 if failed else 0


if __name__ == '__main__':
    raise SystemExit(main())
