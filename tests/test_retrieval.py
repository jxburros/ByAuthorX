import pytest

from byax.errors import ByaxError
from byax.libraries import LibraryIndex, LibraryRegistry
from byax.model import new_document
from byax.retrieval import ConsentRegistry, authorize_library_context, retrieve_context, validate_citations


def test_retrieval_attachment_policy_and_budget(tmp_path):
    root = tmp_path / 'books'
    root.mkdir()
    (root / 'notes.txt').write_text('A copper moon hangs over the city.')
    registry = LibraryRegistry(tmp_path / 'config')
    library = registry.create('Bible', [str(root)], retrieval_policy='local_only')
    LibraryIndex(library, cache_dir=tmp_path / 'cache').refresh()
    doc = new_document()
    kwargs = {'registry': registry, 'cache_dir': tmp_path / 'cache'}
    assert retrieve_context(doc, 'copper', **kwargs)['included'] == []
    doc['settings']['libraries'] = [{'library_id': library['library_id'], 'role': 'supplementary'}]
    result = retrieve_context(doc, 'copper', **kwargs)
    assert result['included'][0]['text'] == 'A copper moon hangs over the city.'
    assert (
        retrieve_context(doc, 'copper', remote=True, **kwargs)['omitted'][0]['reason']
        == 'Library is local_only'
    )
    assert retrieve_context(doc, 'copper', max_chars=1, **kwargs)['included'] == []
    doc['settings']['libraries'][0]['role'] = 'open'
    assert (
        retrieve_context(doc, 'copper', profile={'source_authority': 'exclusive'}, **kwargs)['included'] == []
    )


def test_independent_consent_and_local_only(tmp_path):
    consent = ConsentRegistry(tmp_path)
    context = {'library_passages': [{'retrieval_policy': 'ask'}]}
    kwargs = {'remote': True, 'provider': 'host', 'config_dir': tmp_path}
    with pytest.raises(ByaxError):
        authorize_library_context(context, **kwargs)
    consent.set('host', 'library_embeddings', True)
    assert not consent.allows('host', 'library_passages')
    consent.set('host', 'library_passages', True)
    authorize_library_context(context, **kwargs)
    assert not consent.allows('other', 'library_passages')
    context['library_passages'][0]['retrieval_policy'] = 'local_only'
    with pytest.raises(ByaxError):
        authorize_library_context(context, authorized=True, **kwargs)
    authorize_library_context(context, remote=False, provider='local')
    consent.set('host', 'library_passages', False)
    assert not consent.allows('host', 'library_passages')


def test_citations_only_resolve_to_supplied_material():
    context = {'references': {'ref_known': {}}, 'library_passages': [{'chunk_id': 'chunk_known'}]}
    known = 'Claim [[cite:chunk_known]] and [[cite:ref_known]]'
    assert validate_citations(known, context) == {'text': known, 'invalid': []}
    # Plan 6.1: an invented ID no longer rejects the reply; only its token is stripped.
    checked = validate_citations('Invented [[cite:chunk_unknown]]. Real [[cite:chunk_known]].', context)
    assert checked == {'text': 'Invented. Real [[cite:chunk_known]].', 'invalid': ['chunk_unknown']}
    with pytest.raises(ByaxError):
        validate_citations(None, context)


def test_review_library_evidence_retains_locator_and_rejects_unknown_tokens():
    from byax.providers import normalize_review_context

    context = normalize_review_context(
        {
            'library_passages': [
                {
                    'chunk_id': 'chunk_provided',
                    'text': 'The moon is copper.',
                    'content_hash': 'abc',
                    'library_id': 'lib_test',
                    'document_id': 'doc_test',
                    'locator': {'page': 2},
                }
            ]
        }
    )
    assert context['references']['chunk_provided']['text'] == 'The moon is copper.'
    assert context['source_versions']['chunk_provided']['locator'] == {'page': 2}
    assert context['source_versions']['chunk_provided']['content_hash'] == 'abc'
    assert context['retrieval']['performed']
