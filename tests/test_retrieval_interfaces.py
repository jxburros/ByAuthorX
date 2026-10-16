"""CLI, MCP and library parity for search methods, query syntax and verified absence."""

import json

import pytest

from byax import cli
from byax.errors import ByaxError
from byax.mcp import handle
from byax.model import new_document
from byax.storage import load_document, save_document
from byax.transport_policy import check_provider_admin

VOLATILE = {'revision', 'base_revision', 'document_id', 'exit_code'}


@pytest.fixture
def library_doc(tmp_path, monkeypatch):
    import platformdirs

    from byax import libraries

    monkeypatch.setattr(libraries, '_default_dir', lambda: tmp_path / 'config')
    monkeypatch.setattr(platformdirs, 'user_cache_path', lambda *a, **k: tmp_path / 'cache')
    monkeypatch.setattr(platformdirs, 'user_state_path', lambda *a, **k: tmp_path / 'state')
    root = tmp_path / 'research'
    root.mkdir()
    (root / 'facts.md').write_text(
        '# Mars\n\nMars has two moons: Phobos and Deimos.\n\n# Venus\n\nVenus has a thick atmosphere.',
        encoding='utf-8',
    )
    path = tmp_path / 'draft.byax'
    save_document(new_document(text='Mars has two moons. Olympus Mons is tall.'), path)
    cli.execute(
        'library',
        {'path': str(path), 'action': 'create', 'input': {'name': 'Research', 'roots': [str(root)]}},
    )
    identity = load_document(path)['settings']['libraries'][0]['library_id']
    cli.execute('library', {'path': str(path), 'action': 'refresh', 'library_id': identity})
    return str(path), identity


def _mcp(command, arguments):
    response = handle(
        {
            'jsonrpc': '2.0',
            'id': 3,
            'method': 'tools/call',
            'params': {'name': 'byax_' + command.replace('-', '_'), 'arguments': arguments},
        }
    )['result']
    return response


def _strip(value):
    return {k: v for k, v in value.items() if k not in VOLATILE}


def test_library_search_method_and_syntax_match_across_cli_mcp_and_library(library_doc, capsys):
    path, identity = library_doc
    args = {'path': path, 'library_id': identity, 'query': 'heading:venus -moons', 'method': 'auto'}
    library = cli.execute('library-search', args)
    assert library['method'] == 'keyword' and library['embedding_state'] == 'disabled'
    assert [h['heading'] for h in library['results']] == ['Venus', 'Venus']
    assert library['query']['clauses'][0] == {'kind': 'term', 'text': 'venus', 'field': 'heading'}
    mcp = _mcp('library-search', args)
    assert not mcp['isError']
    assert _strip(mcp['structuredContent']) == _strip(library)
    code = cli.main(
        [
            'library-search',
            path,
            '--library-id',
            identity,
            '--query',
            'heading:venus -moons',
            '--method',
            'auto',
        ]
    )
    assert code == 0 and _strip(json.loads(capsys.readouterr().out)) == _strip(library)
    alias = cli.execute('library', dict(args, action='search'))
    assert _strip(alias) == _strip(library)
    hybrid = cli.execute('library-search', dict(args, method='hybrid'))
    assert hybrid['method'] == 'keyword' and 'not configured' in hybrid['notice']


def test_invalid_method_and_typo_expansion_are_consistent(library_doc):
    path, identity = library_doc
    args = {'path': path, 'library_id': identity, 'query': 'Phobsos'}
    with pytest.raises(ByaxError) as caught:
        cli.execute('library-search', dict(args, method='semantic'))
    mcp = _mcp('library-search', dict(args, method='semantic'))
    assert (
        mcp['isError'] and mcp['structuredContent']['error']['code'] == caught.value.code == 'invalid_input'
    )
    plain = cli.execute('library-search', args)
    assert not plain['results'] and plain['suggestions']['phobsos'][0]['term'] == 'phobos'
    expanded = cli.execute('library-search', dict(args, expand=True))
    assert 'Phobos' in expanded['results'][0]['text']


def test_verify_absence_cli_and_mcp_parity(library_doc, capsys):
    path, identity = library_doc
    args = {
        'path': path,
        'action': 'verify-absence',
        'input': {'terms': ['Deimos', 'Olympus Mons', 'Ganymede']},
    }
    check_provider_admin('library', args)  # read-only, so allowed over MCP and REST
    library = cli.execute('library', args)
    assert library['found'] == ['Deimos', 'Olympus Mons'] and library['absent'] == ['Ganymede']
    assert library['searched']['libraries'][0]['library_id'] == identity
    assert library['searched']['manuscript']['objects'] == 1
    mcp = _mcp('library', args)
    assert not mcp['isError'] and _strip(mcp['structuredContent']) == _strip(library)
    by_query = cli.execute(
        'library', {'path': path, 'action': 'verify-absence', 'query': '"Olympus Mons" Ganymede'}
    )
    assert by_query['terms'] == ['Olympus Mons', 'Ganymede']
    code = cli.main(
        ['library', path, '--action', 'verify-absence', '--query', 'Deimos', '--library-id', identity]
    )
    output = json.loads(capsys.readouterr().out)
    assert code == 0 and output['found'] == ['Deimos']
    with pytest.raises(ByaxError):
        cli.execute('library', {'path': path, 'action': 'verify-absence', 'input': {'other': 1}})


def test_context_command_accepts_method_and_pinned(library_doc):
    path, _identity = library_doc
    first = cli.execute('context', {'path': path, 'library_query': 'moons', 'library_method': 'keyword'})
    retrieval = first['library_retrieval']
    assert retrieval['method'] == 'keyword' and retrieval['libraries'][0]['method'] == 'keyword'
    chunk = first['library_passages'][0]['chunk_id']
    pinned = cli.execute('context', {'path': path, 'library_query': 'moons', 'library_pinned': [chunk]})
    assert chunk not in [p['chunk_id'] for p in pinned.get('library_passages', [])]
    assert pinned['library_retrieval']['pinned'][0]['excluded_chunks'] == [chunk]


def test_embedding_actions_stay_local_only(library_doc):
    path, identity = library_doc
    for action in ('embed', 'embedding-configure', 'hybrid'):
        with pytest.raises(ByaxError, match='local CLI'):
            check_provider_admin('library', {'path': path, 'action': action, 'library_id': identity})


def test_embed_runs_as_one_job_with_counts(library_doc, monkeypatch):
    from byax import embeddings

    path, identity = library_doc

    class Local:
        model_identity = 'local-fixture'
        dimension = 2
        max_input_chars = 1000
        max_batch = 8
        remote = False

        def authorize(self):
            return None

        def embed(self, texts):
            return [[1.0, float(len(t))] for t in texts]

    monkeypatch.setattr(embeddings, '_library_provider', lambda index, authorized=False: Local())
    result = cli.execute('library', {'path': path, 'action': 'embed', 'library_id': identity})
    assert result['job']['kind'] == 'library-embed' and result['execution'] == 'completed'
    rebuild = result['rebuild']
    assert (rebuild['done'], rebuild['skipped'], rebuild['failed']) == (4, 0, 0)
    again = cli.execute('library', {'path': path, 'action': 'embed', 'library_id': identity})['rebuild']
    assert (again['done'], again['skipped']) == (0, 4)
