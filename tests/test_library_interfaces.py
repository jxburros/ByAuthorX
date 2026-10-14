"""Libraries are attached explicitly and expose identical bounded transport results."""

import json

import pytest

from byax import cli, engine
from byax.errors import ByaxError
from byax.mcp import handle
from byax.model import new_document
from byax.storage import load_document, save_document
from byax.transport_policy import check_provider_admin


@pytest.fixture
def library_doc(tmp_path, monkeypatch):
    import platformdirs

    from byax import libraries

    monkeypatch.setattr(libraries, '_default_dir', lambda: tmp_path / 'config')
    monkeypatch.setattr(platformdirs, 'user_cache_path', lambda *a, **k: tmp_path / 'cache')
    monkeypatch.setattr(platformdirs, 'user_state_path', lambda *a, **k: tmp_path / 'state')
    root = tmp_path / 'research'
    root.mkdir()
    (root / 'facts.md').write_text('# Mars\n\nMars has two moons: Phobos and Deimos.', encoding='utf-8')
    path = tmp_path / 'draft.byax'
    save_document(new_document(text='Mars has two moons.'), path)
    cli.execute(
        'library',
        {'path': str(path), 'action': 'create', 'input': {'name': 'Research', 'roots': [str(root)]}},
    )
    identity = load_document(path)['settings']['libraries'][0]['library_id']
    refresh = cli.execute('library', {'path': str(path), 'action': 'refresh', 'library_id': identity})
    assert refresh['job']['state'] == 'completed', refresh
    return str(path), identity


def test_search_read_context_and_mcp_are_bounded(library_doc):
    path, identity = library_doc
    args = {'path': path, 'library_id': identity, 'query': 'moons'}
    result = cli.execute('library-search', args)
    assert result['revision'] == load_document(path)['revision']
    hit = result['results'][0]
    assert 'Phobos' in hit['text']
    response = handle(
        {
            'jsonrpc': '2.0',
            'id': 1,
            'method': 'tools/call',
            'params': {'name': 'byax_library_search', 'arguments': args},
        }
    )['result']
    assert not response['isError']
    assert response['structuredContent']['results'] == result['results']
    read = cli.execute(
        'library-read',
        {
            'path': path,
            'library_id': identity,
            'document_id': hit['document_id'],
            'chunk_id': hit['chunk_id'],
        },
    )
    assert 'Phobos' in json.dumps(read)
    context = cli.execute('context', {'path': path, 'library_query': 'moons'})
    assert context['library_passages'][0]['chunk_id'] == hit['chunk_id']
    ordinary = cli.execute('context', {'path': path})
    assert 'library_passages' not in ordinary
    tools = handle({'jsonrpc': '2.0', 'id': 1, 'method': 'tools/list'})['result']['tools']
    for tool in tools:
        if tool['name'] in ('byax_library_search', 'byax_library_read'):
            assert tool['annotations']['readOnlyHint'] is True


def test_unattached_library_and_transport_admin_are_refused(library_doc, tmp_path):
    path, identity = library_doc
    other = tmp_path / 'other.byax'
    save_document(new_document(text='Other'), other)
    with pytest.raises(ByaxError, match='not attached'):
        cli.execute('library-search', {'path': str(other), 'library_id': identity, 'query': 'moons'})
    for action in ('create', 'attach', 'detach', 'relink', 'remove'):
        with pytest.raises(ByaxError, match='local CLI'):
            check_provider_admin('library', {'path': path, 'action': action})
        with pytest.raises(ByaxError):
            check_provider_admin(
                'jobs',
                {
                    'action': 'submit',
                    'input': {'command': 'library', 'arguments': {'path': path, 'action': action}},
                },
            )
    with pytest.raises(ByaxError):
        cli.execute('library-search', {'path': path, 'library_id': identity, 'query': 'moons', 'offset': -1})


def test_suggest_creates_proposal_then_accept_inserts_citation(library_doc):
    path, _identity = library_doc
    original = load_document(path)
    target = original['blocks'][0]['id']
    result = cli.execute('source', {'path': path, 'action': 'suggest', 'target': target, 'query': 'moons'})
    assert result['retrieval']['included']
    proposed = load_document(path)
    assert proposed['blocks'] == original['blocks']
    assert proposed['references'] == original['references']
    proposal = proposed['review']['proposals'][0]
    accepted, _ = engine.apply(
        proposed, {'operations': [{'type': 'accept_proposal', 'target': proposal['id']}]}
    )
    assert accepted['blocks'][0]['marks'][0]['type'] == 'citation'
    assert len(accepted['references']) == 1


def test_transport_cannot_claim_verified_source():
    for command in ('apply', 'transact', 'review'):
        with pytest.raises(ByaxError, match='cannot assert'):
            check_provider_admin(
                command,
                {
                    'input': {
                        'operations': [
                            {'type': 'add_reference', 'id': 'source', 'values': {'verification': 'verified'}}
                        ]
                    }
                },
            )


def test_missing_registry_attachment_can_be_detached_or_relinked(library_doc, tmp_path):
    from byax.libraries import LibraryRegistry

    path, identity = library_doc
    registry = LibraryRegistry()
    registry.remove(identity)
    result = cli.execute(
        'library',
        {
            'path': path,
            'action': 'relink',
            'library_id': identity,
            'input': {'roots': [str(tmp_path / 'research')]},
        },
    )
    assert result['library']['library_id'] == identity
    registry.remove(identity)
    cli.execute('library', {'path': path, 'action': 'detach', 'library_id': identity})
    assert not load_document(path)['settings']['libraries']


@pytest.mark.parametrize('command', ['apply', 'transact', 'review'])
def test_transport_cannot_attach_libraries_via_operations(command):
    operation = {'type': 'set_settings', 'values': {'libraries': []}}
    with pytest.raises(ByaxError, match='local CLI'):
        check_provider_admin(command, {'input': {'operations': [operation]}})
    with pytest.raises(ByaxError, match='local CLI'):
        check_provider_admin(
            command, {'input': {'operations': [{'type': 'propose', 'values': {'operations': [operation]}}]}}
        )


def test_source_suggestions_resolve_the_selected_section_profile(library_doc, monkeypatch):
    from byax import retrieval

    path, _identity = library_doc
    target = load_document(path)['blocks'][0]['id']
    original = retrieval.retrieve_context
    targets = []

    def scoped(doc, query, **kwargs):
        targets.append(kwargs.get('target'))
        return original(doc, query, **kwargs)

    monkeypatch.setattr(retrieval, 'retrieve_context', scoped)
    cli.execute('source', {'path': path, 'action': 'suggest', 'target': target, 'query': 'moons'})
    assert targets == [target]
