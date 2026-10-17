"""Token estimates, budgets and Ollama num_ctx (context engine plan 5.5)."""

import json

import httpx
import pytest

from byax import context_assembly as ca
from byax.errors import ByaxError
from byax.libraries import LibraryIndex, LibraryRegistry
from byax.model import new_document
from byax.providers import ProviderConfig, ProviderRegistry
from byax.retrieval import retrieve_context


@pytest.fixture(autouse=True)
def no_tokenizers():
    saved = dict(ca._TOKENIZERS)
    ca._TOKENIZERS.clear()
    yield
    ca._TOKENIZERS.clear()
    ca._TOKENIZERS.update(saved)


def test_estimate_is_conservative_and_offline():
    assert ca.estimate_tokens('') == 0
    assert ca.estimate_tokens('abcdefg') == 2  # 7 / 3.5
    assert ca.estimate_tokens('a' * 35) == 10
    # English prose is about four characters per token, so the estimate never under-counts it.
    prose = 'The quick brown fox jumps over the lazy dog. ' * 40
    assert ca.estimate_tokens(prose) >= len(prose) / 4
    # CJK characters cost roughly a token each.
    assert ca.estimate_tokens('中文' * 10) == 20
    assert ca.CHARS_PER_TOKEN == 3.5


def test_registered_tokenizer_is_used_per_provider_kind_and_failures_fall_back():
    ca.register_tokenizer('ollama', lambda text: 7)
    assert ca.estimate_tokens('anything at all', 'ollama') == 7
    assert ca.estimate_tokens('anything at all', ProviderConfig('a', 'ollama', 'm')) == 7
    assert ca.estimate_tokens('anything at all', {'kind': 'ollama'}) == 7
    assert ca.estimate_tokens('a' * 35, 'openai') == 10
    assert ca.estimate_tokens('a' * 35) == 10

    def broken(text):
        raise RuntimeError('tokenizer unavailable')

    ca.register_tokenizer('ollama', broken)
    assert ca.estimate_tokens('a' * 35, 'ollama') == 10
    ca.register_tokenizer('ollama', None)
    assert 'ollama' not in ca._TOKENIZERS


def test_character_budgets_convert_and_stay_as_hard_caps():
    budget = ca.budget_from_chars(12000)
    assert budget.tokens == ca.chars_to_tokens(12000) == 3429 and budget.chars == 12000
    assert budget.fits('a' * 35)
    assert not budget.fits('a' * 35, used_tokens=budget.tokens - 5)
    # A tokenizer that undercounts cannot lift the character cap.
    ca.register_tokenizer('openai', lambda text: 1)
    assert not budget.fits('a' * 12001, provider='openai')
    assert budget.fits('a' * 12000, provider='openai')
    assert ca.tokens_to_chars(ca.chars_to_tokens(7000)) >= 7000


def test_input_and_library_budgets_follow_the_provider_window():
    hosted = ProviderConfig('h', 'openai', 'm', max_input_chars=70_000)
    assert ca.input_budget(hosted) == ca.TokenBudget(tokens=20_000, chars=70_000)
    assert ca.library_budget(hosted).tokens == ca.chars_to_tokens(12000)
    assert ca.library_budget(hosted).chars == 12000
    small = ProviderConfig('s', 'ollama', 'm', max_context_tokens=2048)
    assert ca.input_budget(small).tokens == 2048 - 512  # headroom shrinks with the window
    assert ca.library_budget(small).tokens == (2048 - 512) // 2
    roomy = ProviderConfig('r', 'ollama', 'm', max_context_tokens=32768, context_tokens=4096)
    assert ca.context_window(roomy) == 4096  # the model's real window wins over a larger cap
    assert ca.input_budget(roomy).tokens == 4096 - 1024


def test_retrieval_respects_the_token_budget_and_keeps_the_character_cap(tmp_path):
    root = tmp_path / 'books'
    root.mkdir()
    for number in range(4):
        (root / f'note{number}.txt').write_text(f'copper moon {number} ' + 'lantern ' * 60)
    registry = LibraryRegistry(tmp_path / 'config')
    library = registry.create('Notes', [str(root)])
    LibraryIndex(library, cache_dir=tmp_path / 'cache').refresh()
    doc = new_document()
    doc['settings']['libraries'] = [{'library_id': library['library_id'], 'role': 'supplementary'}]
    kwargs = {'registry': registry, 'cache_dir': tmp_path / 'cache'}
    everything = retrieve_context(doc, 'copper', **kwargs)
    assert len(everything['included']) == 4 and 'token_budget' not in everything
    per_passage = ca.estimate_tokens(everything['included'][0]['text'])
    limited = retrieve_context(doc, 'copper', max_tokens=per_passage * 2 + 1, **kwargs)
    assert len(limited['included']) == 2
    assert limited['token_budget'] == {'maximum': per_passage * 2 + 1, 'used': per_passage * 2}
    assert {o['reason'] for o in limited['omitted']} == {'Retrieval token budget exceeded'}
    assert retrieve_context(doc, 'copper', max_tokens=0, **kwargs)['included'] == []
    # The character limit still applies when the token budget would allow more.
    chars = retrieve_context(doc, 'copper', max_chars=len(everything['included'][0]['text']), **kwargs)
    assert len(chars['included']) == 1
    with pytest.raises(ByaxError):
        retrieve_context(doc, 'copper', max_tokens=-1, **kwargs)


# --- Ollama -------------------------------------------------------------------------------------


def ollama_registry(tmp_path, monkeypatch, handler, **config):
    registry = ProviderRegistry(tmp_path)
    registry.save(ProviderConfig('local', 'ollama', 'fixture-model', **config))
    client = httpx.Client
    monkeypatch.setattr(
        httpx, 'Client', lambda **kwargs: client(transport=httpx.MockTransport(handler), **kwargs)
    )
    return registry


def recorder(requests, reply=None):
    def handler(request):
        requests.append(request)
        return httpx.Response(200, json=reply or {'message': {'content': 'Fine.'}})

    return handler


def test_ollama_chat_sets_num_ctx_from_the_assembled_payload(tmp_path, monkeypatch):
    requests = []
    registry = ollama_registry(tmp_path, monkeypatch, recorder(requests))
    registry.chat('local', [{'role': 'user', 'content': 'Short question.'}])
    body = json.loads(requests[0].content)
    num_ctx = body['options']['num_ctx']
    assert num_ctx % ca.NUM_CTX_STEP == 0
    assert 1024 <= num_ctx <= 8192
    assert body['stream'] is False

    requests.clear()
    registry.chat('local', [{'role': 'user', 'content': 'word ' * 4000}])
    bigger = json.loads(requests[0].content)['options']['num_ctx']
    assert bigger > num_ctx and bigger <= 8192


def test_ollama_review_also_sets_num_ctx(tmp_path, monkeypatch):
    requests = []
    reply = {'message': {'content': '{"findings": []}'}}
    registry = ollama_registry(tmp_path, monkeypatch, recorder(requests, reply))
    registry.review('local', 'Review this sentence.')
    assert json.loads(requests[0].content)['options']['num_ctx'] >= 1024


def test_num_ctx_is_capped_by_the_provider_setting(tmp_path, monkeypatch):
    requests = []
    registry = ollama_registry(tmp_path, monkeypatch, recorder(requests), max_context_tokens=4096)
    registry.chat('local', [{'role': 'user', 'content': 'word ' * 2000}])
    assert json.loads(requests[0].content)['options']['num_ctx'] <= 4096


def test_oversized_payload_fails_before_sending_with_limit_exceeded(tmp_path, monkeypatch):
    requests = []
    registry = ollama_registry(tmp_path, monkeypatch, recorder(requests), max_context_tokens=2048)
    with pytest.raises(ByaxError) as caught:
        registry.chat('local', [{'role': 'user', 'content': 'word ' * 4000}])
    error = caught.value
    assert error.code == 'limit_exceeded'
    needed = error.details['tokens_needed']
    assert error.message == f'{needed} tokens needed / 2048 available'
    assert needed > 2048 and error.details['tokens_available'] == 2048
    assert 'max_context_tokens' in error.details['guidance']
    assert requests == []  # nothing was sent, and nothing was truncated
    with pytest.raises(ByaxError) as review:
        registry.review('local', 'word ' * 4000)
    assert review.value.code == 'limit_exceeded' and requests == []


def test_model_window_below_the_cap_is_the_limit(tmp_path, monkeypatch):
    requests = []
    registry = ollama_registry(
        tmp_path, monkeypatch, recorder(requests), max_context_tokens=16384, context_tokens=2048
    )
    with pytest.raises(ByaxError, match=r'tokens needed / 2048 available'):
        registry.chat('local', [{'role': 'user', 'content': 'word ' * 4000}])
    assert requests == []


def test_other_providers_keep_their_character_limit_and_get_no_num_ctx(tmp_path, monkeypatch):
    registry = ProviderRegistry(tmp_path)
    registry.save(ProviderConfig('hosted', 'openai', 'm', credential_ref='env:BYAX_X', max_input_chars=1000))
    monkeypatch.setenv('BYAX_X', 'k')
    with pytest.raises(ByaxError, match='character limit'):
        registry.chat('hosted', [{'role': 'user', 'content': 'x' * 2000}], authorized=True)
    from byax.providers import _wire

    body = _wire(registry.get('hosted'), 'system', [{'role': 'user', 'content': 'hi'}])[1]
    assert 'options' not in body


def test_connection_test_records_the_model_context_window_without_background_calls(tmp_path, monkeypatch):
    requests = []

    def handler(request):
        requests.append(request.url.path)
        if request.url.path == '/api/show':
            return httpx.Response(
                200, json={'model_info': {'general.architecture': 'llama', 'llama.context_length': 131072}}
            )
        return httpx.Response(200, json={'models': []})

    registry = ollama_registry(tmp_path, monkeypatch, handler)
    assert registry.get('local').context_tokens is None
    assert requests == []  # saving or reading configuration never contacts the server
    report = registry.test('local')
    assert report['status'] == 'connected' and report['context_tokens'] == 131072
    assert requests == ['/api/tags', '/api/show']
    assert registry.get('local').context_tokens == 131072
    # The cap still protects memory: num_ctx never exceeds max_context_tokens.
    assert ca.context_window(registry.get('local')) == 8192


def test_connection_test_survives_a_server_without_model_details(tmp_path, monkeypatch):
    def handler(request):
        if request.url.path == '/api/show':
            return httpx.Response(404, json={'error': 'model not found'})
        return httpx.Response(200, json={'models': []})

    registry = ollama_registry(tmp_path, monkeypatch, handler)
    report = registry.test('local')
    assert report['status'] == 'connected' and report['context_tokens'] is None
    assert 'unavailable' in report['context_note']


def test_preview_reports_tokens_and_num_ctx_without_raising(tmp_path, monkeypatch):
    registry = ollama_registry(tmp_path, monkeypatch, recorder([]), max_context_tokens=2048)
    preview = registry.preview('local', text='word ' * 4000)
    limits = preview['limits']
    assert limits['estimated_tokens'] > 2048
    assert limits['tokens_available'] == 2048 and limits['tokens_needed'] > 2048
    assert limits['within_limit'] is False
    assert preview['request']['json']['options']['num_ctx'] == 2048
    assert ProviderRegistry(tmp_path).preview('local', text='short')['limits']['within_limit'] is True


def test_provider_config_validates_context_settings():
    for bad in ({'max_context_tokens': 10}, {'max_context_tokens': True}, {'context_tokens': 'big'}):
        with pytest.raises(ByaxError):
            ProviderConfig('a', 'ollama', 'm', **bad).validate()
    ProviderConfig('a', 'ollama', 'm', max_context_tokens=65536, context_tokens=131072).validate()
