"""Every provider and embedder says whether it is local or remote (context engine plan 5.6, Q13)."""

import pytest

from byax import cli
from byax.embeddings import embedder_locality, embedding_preview
from byax.libraries import LibraryIndex, LibraryRegistry
from byax.model import new_document
from byax.providers import (
    ProviderConfig,
    ProviderRegistry,
    endpoint_locality,
    list_capabilities,
    provider_locality,
)
from byax.storage import load_document, save_document


@pytest.mark.parametrize(
    'endpoint,expected',
    [
        ('http://localhost:11434', 'local'),
        ('http://LOCALHOST:11434/v1', 'local'),
        ('http://127.0.0.1:11434', 'local'),
        ('http://127.0.0.7:8080', 'local'),
        ('http://[::1]:11434', 'local'),
        ('unix:///run/ollama.sock', 'local'),
        ('http+unix://%2Frun%2Follama.sock', 'local'),
        ('https://api.openai.com/v1', 'remote'),
        ('https://api.anthropic.com/v1', 'remote'),
        ('https://localhost.example.com', 'remote'),
        ('http://192.168.1.20:11434', 'remote'),
        ('https://ollama.my-lan.example', 'remote'),
        (None, 'remote'),
        ('', 'remote'),
    ],
)
def test_endpoint_locality_table(endpoint, expected):
    assert endpoint_locality(endpoint) == expected


def test_whisper_runs_locally_whatever_the_endpoint_field_says():
    assert endpoint_locality(None, 'whisper.cpp') == 'local'
    info = provider_locality({'kind': 'whisper.cpp', 'endpoint': None})
    assert info['locality'] == 'local' and info['host'] is None


def test_provider_locality_uses_the_configured_endpoint_or_the_kind_default():
    assert provider_locality({'kind': 'ollama', 'endpoint': None})['locality'] == 'local'
    assert provider_locality({'kind': 'openai', 'endpoint': None})['locality'] == 'remote'
    assert (
        provider_locality(ProviderConfig('x', 'openai', 'm', endpoint='http://127.0.0.1:1234/v1'))['locality']
        == 'local'
    )
    remote = provider_locality({'kind': 'anthropic', 'endpoint': None})
    assert remote['host'] == 'api.anthropic.com'
    assert remote['label'] == 'Remote · leaves this computer (api.anthropic.com)'
    assert provider_locality({'kind': 'ollama'})['label'].startswith('Local · stays on this computer')


def test_capabilities_carry_a_locality_for_every_kind():
    rows = {row['kind']: row for row in list_capabilities()}
    assert {k: v['locality'] for k, v in rows.items()} == {
        'openai': 'remote',
        'anthropic': 'remote',
        'gemini': 'remote',
        'ollama': 'local',
        'elevenlabs': 'remote',
        'whisper.cpp': 'local',
    }
    assert all(row['locality_basis'] for row in rows.values())
    capabilities = cli.execute('capabilities', {})
    assert {row['kind']: row['locality'] for row in capabilities['providers']} == {
        k: v['locality'] for k, v in rows.items()
    }


def test_providers_list_labels_each_saved_provider(tmp_path):
    registry = ProviderRegistry(tmp_path)
    registry.save(ProviderConfig('home', 'ollama', 'llama', endpoint='http://localhost:11434'))
    registry.save(ProviderConfig('cloud', 'openai', 'gpt', credential_ref='env:BYAX_KEY'))
    registry.save(ProviderConfig('lan', 'ollama', 'llama', endpoint='https://gpu.example.org'))
    result = cli.execute('providers', {'action': 'list', 'config_dir': str(tmp_path)})
    labels = {p['alias']: (p['locality'], p['locality_label']) for p in result['providers']}
    assert labels['home'][0] == 'local' and labels['home'][1].startswith('Local')
    assert labels['cloud'][0] == 'remote' and 'api.openai.com' in labels['cloud'][1]
    assert labels['lan'][0] == 'remote' and 'gpu.example.org' in labels['lan'][1]
    # Saved configuration files are unchanged: the label is derived, not stored.
    assert 'locality' not in registry.path.read_text()
    assert ProviderRegistry(tmp_path).get('home').endpoint == 'http://localhost:11434'


def test_payload_preview_names_where_the_request_goes(tmp_path):
    registry = ProviderRegistry(tmp_path)
    registry.save(ProviderConfig('home', 'ollama', 'llama'))
    registry.save(ProviderConfig('cloud', 'openai', 'gpt', credential_ref='env:BYAX_KEY'))
    local = registry.preview('home', text='x')
    assert local['locality'] == 'local' and local['remote'] is False
    assert local['locality_label'].startswith('Local')
    remote = registry.preview('cloud', text='x')
    assert remote['locality'] == 'remote' and remote['remote'] is True
    assert registry.locality('cloud')['host'] == 'api.openai.com'


def test_embedder_locality_reads_configuration_only(tmp_path):
    registry = ProviderRegistry(tmp_path)
    registry.save(ProviderConfig('home', 'ollama', 'nomic'))
    registry.save(ProviderConfig('cloud', 'openai', 'embed', credential_ref='env:BYAX_KEY'))
    assert embedder_locality({}, registry) is None
    local = embedder_locality({'embedding': {'provider': 'home', 'dimension': 3}}, registry)
    assert local['provider'] == 'home' and local['locality'] == 'local'
    remote = embedder_locality({'embedding': {'provider': 'cloud', 'dimension': 3}}, registry)
    assert remote['locality'] == 'remote' and 'api.openai.com' in remote['label']
    missing = embedder_locality({'embedding': {'provider': 'gone', 'dimension': 3}}, registry)
    assert missing['locality'] == 'unknown'


class FakeEmbedder:
    model_identity = 'fake'
    dimension = 3
    max_input_chars = 1000

    def __init__(self, remote):
        self.remote = remote

    def embed(self, texts):
        return [[1.0, 0.0, 0.0] for _ in texts]


def test_embedding_preview_reports_locality(tmp_path):
    root = tmp_path / 'books'
    root.mkdir()
    (root / 'a.txt').write_text('A copper moon.')
    library = LibraryRegistry(tmp_path / 'config').create('Notes', [str(root)])
    index = LibraryIndex(library, cache_dir=tmp_path / 'cache')
    index.refresh()
    assert embedding_preview(index, FakeEmbedder(False))['locality'] == 'local'
    assert embedding_preview(index, FakeEmbedder(True))['locality'] == 'remote'


def test_library_status_command_names_the_embedder(tmp_path, monkeypatch):
    import platformdirs

    from byax import libraries

    monkeypatch.setattr(libraries, '_default_dir', lambda: tmp_path / 'config')
    monkeypatch.setattr('byax.providers._default_dir', lambda: tmp_path / 'providers')
    monkeypatch.setattr(platformdirs, 'user_cache_path', lambda *a, **k: tmp_path / 'cache')
    monkeypatch.setattr(platformdirs, 'user_state_path', lambda *a, **k: tmp_path / 'state')
    root = tmp_path / 'research'
    root.mkdir()
    (root / 'facts.md').write_text('# Mars\n\nMars has two moons.', encoding='utf-8')
    path = tmp_path / 'draft.byax'
    save_document(new_document(text='Mars.'), path)
    cli.execute(
        'library',
        {'path': str(path), 'action': 'create', 'input': {'name': 'Research', 'roots': [str(root)]}},
    )
    identity = load_document(path)['settings']['libraries'][0]['library_id']
    status = cli.execute('library', {'path': str(path), 'action': 'status', 'library_id': identity})
    assert status['embedder'] is None  # keyword search only; nothing leaves the computer
    LibraryRegistry().configure_embedding(identity, {'provider': 'home', 'dimension': 3})
    ProviderRegistry().save(ProviderConfig('home', 'ollama', 'nomic'))
    status = cli.execute('library', {'path': str(path), 'action': 'status', 'library_id': identity})
    assert status['embedder']['provider'] == 'home'
    assert status['embedder']['locality'] == 'local'
