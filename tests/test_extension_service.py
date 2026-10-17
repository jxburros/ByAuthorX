"""Scoped REST tokens for extensions (CP 7.4): read and propose only, bound to an enabled extension."""

import json
import threading
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest
from extension_support import install, make

from byax import extensions, service
from byax.cli import execute, main
from byax.errors import ByaxError
from byax.service import (
    ScopeDenied,
    check_extension_request,
    issue_extension_token,
    make_server,
    verify_extension_token,
)

MAIN = 'service-token-with-at-least-32-chars'


@pytest.fixture
def setup(tmp_path):
    root = tmp_path / 'root'
    root.mkdir()
    document = str(root / 'doc.byax')
    execute('new', {'out': document, 'text': 'Alpha beta gamma.'})
    install(make(tmp_path, scopes=('checks', 'read', 'propose')))
    server = make_server([root], token=MAIN, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f'http://127.0.0.1:{server.server_port}/v1/execute'

    def call(command, arguments, token):
        request = Request(
            url,
            data=json.dumps({'command': command, 'arguments': arguments}).encode(),
            method='POST',
            headers={'Authorization': 'Bearer ' + token},
        )
        try:
            with urlopen(request, timeout=30) as response:
                return response.status, json.load(response)
        except HTTPError as exc:
            return exc.code, json.load(exc)

    yield document, call
    server.shutdown()
    server.server_close()
    thread.join(timeout=5)


def inspect(document):
    return execute('inspect', {'path': document, 'limit': 5})


# Issuing and verifying --------------------------------------------------------------------
def test_tokens_are_signed_scoped_expiring_and_only_for_usable_extensions(tmp_path):
    install(make(tmp_path, scopes=('checks', 'read', 'propose')))
    token = issue_extension_token(MAIN, 'ext-one', ['read', 'propose'], ttl=600, now=1000)
    assert token.startswith('bxe1.ext-one.propose,read.1600.')
    assert verify_extension_token(MAIN, token, now=1500) == ('ext-one', {'read', 'propose'})
    assert verify_extension_token(MAIN, token, now=1601) is None, 'expired'
    assert verify_extension_token('another-service-token-0000000000', token, now=1500) is None
    head, signature = token.rsplit('.', 1)
    assert verify_extension_token(MAIN, head + '.' + '0' * len(signature), now=1500) is None
    forged = token.replace('propose,read', 'read').replace('.1600.', '.9999999999.')
    assert verify_extension_token(MAIN, forged, now=1500) is None
    widened = token.replace('ext-one', 'ext-two')
    assert verify_extension_token(MAIN, widened, now=1500) is None
    for junk in ('', 'bxe1.a', 'x.y.z.1.2', None, 'bxe1.ext-one.read.notanumber.sig'):
        assert verify_extension_token(MAIN, junk, now=1500) is None
    extensions.set_enabled('ext-one', False)
    assert verify_extension_token(MAIN, token, now=1500) is None, 'disabling revokes its tokens'
    extensions.set_enabled('ext-one', True)
    assert verify_extension_token(MAIN, token, now=1500) is not None
    extensions.remove('ext-one')
    assert verify_extension_token(MAIN, token, now=1500) is None


def test_issuing_refuses_unknown_scopes_ungranted_scopes_short_secrets_and_missing_extensions(tmp_path):
    install(make(tmp_path, scopes=('checks', 'read')))
    for scopes in (['export-hook'], ['checks'], [], ['read', 'read'], 'read'):
        with pytest.raises(ByaxError) as error:
            issue_extension_token(MAIN, 'ext-one', scopes)
        assert error.value.code == 'invalid_input'
    with pytest.raises(ByaxError, match='not granted'):
        issue_extension_token(MAIN, 'ext-one', ['propose'])
    with pytest.raises(ByaxError, match='24 characters'):
        issue_extension_token('short', 'ext-one', ['read'])
    with pytest.raises(ByaxError, match='installed, enabled'):
        issue_extension_token(MAIN, 'nobody-here', ['read'])
    for ttl in (10, 10**9, 'soon'):
        with pytest.raises(ByaxError, match='ttl'):
            issue_extension_token(MAIN, 'ext-one', ['read'], ttl=ttl)


def test_the_token_command_reads_the_service_secret_from_the_environment_and_is_local_only(
    tmp_path, monkeypatch, capsys
):
    install(make(tmp_path, scopes=('checks', 'read')))
    monkeypatch.setenv('BYAX_SERVICE_TOKEN', MAIN)
    code = main(['extensions', '--action', 'token', '--id', 'ext-one', '--scopes', 'read', '--ttl', '120'])
    out = json.loads(capsys.readouterr().out)
    assert code == 0 and verify_extension_token(MAIN, out['token']) == ('ext-one', {'read'})
    assert MAIN not in json.dumps(out)
    default = execute('extensions', {'action': 'token', 'id': 'ext-one'})
    assert verify_extension_token(MAIN, default['token']) == ('ext-one', {'read'}), (
        'defaults to read and propose that were granted'
    )
    monkeypatch.delenv('BYAX_SERVICE_TOKEN')
    with pytest.raises(ByaxError, match='24 characters'):
        execute('extensions', {'action': 'token', 'id': 'ext-one'})


# The policy -------------------------------------------------------------------------------
def test_read_scope_allows_only_read_only_commands_and_never_extension_checks():
    allowed = [
        ('inspect', {'path': 'x'}),
        ('find', {'path': 'x', 'query': 'a'}),
        ('check', {'path': 'x', 'suite': ['grammar']}),
        ('review', {'path': 'x', 'action': 'list'}),
        ('review', {'path': 'x'}),
        ('project', {'path': 'x', 'action': 'tree'}),
    ]
    for command, arguments in allowed:
        check_extension_request('ext-one', {'read'}, command, arguments)
    denied = [
        ('apply', {'path': 'x', 'input': {}}),
        ('transact', {'path': 'x', 'input': {}}),
        ('review', {'path': 'x', 'action': 'accept'}),
        ('review', {'path': 'x', 'action': 'propose', 'input': {}}),
        ('project', {'path': 'x', 'action': 'add'}),
        ('history', {'path': 'x', 'action': 'restore'}),
        ('export', {'path': 'x', 'out': 'y'}),
        ('macro', {'action': 'run'}),
        ('extensions', {'action': 'list'}),
        ('providers', {'action': 'list'}),
        ('check', {'path': 'x', 'suite': ['extension']}),
        ('check', {'path': 'x', 'family': ['extension']}),
    ]
    for command, arguments in denied:
        with pytest.raises(ScopeDenied):
            check_extension_request('ext-one', {'read'}, command, arguments)


def operation(kind='replace_text'):
    return {'type': kind, 'target': 'p_1', 'match': 'a', 'replacement': 'b'}


def test_propose_scope_allows_text_edit_proposals_with_a_revision_and_stamps_the_author():
    arguments = {
        'path': 'x',
        'action': 'propose',
        'expect_revision': 'rev_1',
        'input': {'operations': [operation()], 'explanation': 'why', 'author': 'someone else'},
    }
    check_extension_request('ext-one', {'propose'}, 'review', arguments)
    assert arguments['input']['author'] == 'extension:ext-one'
    bad = [
        {'input': {'operations': [operation()]}},  # no expect_revision
        {'expect_revision': 'r', 'input': {'operations': [operation('delete_block')]}},
        {'expect_revision': 'r', 'input': {'operations': [{'type': 'set_settings', 'values': {}}]}},
        {'expect_revision': 'r', 'input': {'operations': []}},
        {'expect_revision': 'r', 'input': {'operations': ['x']}},
        {'expect_revision': 'r', 'input': {'operations': [operation()], 'kind': 'merge'}},
        {'expect_revision': 'r', 'input': 'text'},
    ]
    for extra in bad:
        with pytest.raises(ScopeDenied):
            check_extension_request(
                'ext-one', {'propose'}, 'review', {'path': 'x', 'action': 'propose', **extra}
            )
    with pytest.raises(ScopeDenied):  # propose does not imply read
        check_extension_request('ext-one', {'propose'}, 'inspect', {'path': 'x'})


# Over REST --------------------------------------------------------------------------------
def test_a_read_token_reads_but_cannot_edit_propose_or_administer(setup):
    document, call = setup
    token = issue_extension_token(MAIN, 'ext-one', ['read'])
    status, body = call('inspect', {'path': document, 'limit': 5}, token)
    assert status == 200 and body['items'][0]['text'] == 'Alpha beta gamma.'
    revision = body['revision']
    assert call('check', {'path': document, 'suite': ['structure']}, token)[0] == 200
    for command, arguments in (
        ('apply', {'path': document, 'input': {'operations': [{'type': 'insert_paragraph', 'text': 'x'}]}}),
        ('extensions', {'action': 'remove', 'id': 'ext-one'}),
        ('export', {'path': document, 'out': document + '.md'}),
        ('macro', {'action': 'run', 'document': document, 'input': {}}),
        ('review', {'path': document, 'action': 'propose', 'expect_revision': revision, 'input': {}}),
        ('check', {'path': document, 'suite': ['extension']}),
    ):
        status, body = call(command, arguments, token)
        assert status == 403 and body['error']['code'] == 'forbidden', command
    assert inspect(document)['revision'] == revision and extensions.get('ext-one')['enabled']


def test_a_propose_token_leaves_a_pending_proposal_and_the_text_untouched(setup):
    document, call = setup
    token = issue_extension_token(MAIN, 'ext-one', ['propose'])
    info = inspect(document)
    block = info['items'][0]['id']
    arguments = {
        'path': document,
        'action': 'propose',
        'expect_revision': info['revision'],
        'input': {
            'operations': [
                {
                    'type': 'replace_text',
                    'target': block,
                    'match': 'beta',
                    'replacement': 'BETA',
                    'expected_text': 'beta',
                }
            ],
            'explanation': 'Capitalise the second word.',
            'author': 'forged-author',
        },
    }
    status, body = call('review', arguments, token)
    assert status == 200, body
    listing = execute('review', {'path': document, 'action': 'list'})['items']
    (proposal,) = listing['proposals']
    assert proposal['status'] == 'pending' and proposal['author'] == 'extension:ext-one'
    assert execute('inspect', {'path': document})['items'][0]['text'] == 'Alpha beta gamma.', 'proposals only'
    stale = dict(arguments, expect_revision='rev_old')
    status, body = call('review', stale, token)
    assert status == 409 and body['error']['code'] == 'stale_revision'
    for forbidden in (
        dict(
            arguments, input={**arguments['input'], 'operations': [{'type': 'delete_block', 'target': block}]}
        ),
        {k: v for k, v in arguments.items() if k != 'expect_revision'},
    ):
        assert call('review', forbidden, token)[0] == 403
    assert call('inspect', {'path': document}, token)[0] == 403, 'a propose-only token cannot read'


def test_rest_rejects_expired_forged_and_revoked_extension_tokens(setup):
    document, call = setup
    good = issue_extension_token(MAIN, 'ext-one', ['read'])
    assert call('inspect', {'path': document}, good)[0] == 200
    expired = issue_extension_token(MAIN, 'ext-one', ['read'], ttl=60, now=1000)
    assert call('inspect', {'path': document}, expired)[0] == 401
    forged = good[:-4] + ('0000' if not good.endswith('0000') else '1111')
    assert call('inspect', {'path': document}, forged)[0] == 401
    other = issue_extension_token('a-different-service-token-0000000', 'ext-one', ['read'])
    assert call('inspect', {'path': document}, other)[0] == 401
    extensions.set_enabled('ext-one', False)
    assert call('inspect', {'path': document}, good)[0] == 401, (
        'disabling the extension revokes its token at once'
    )
    extensions.set_enabled('ext-one', True)
    assert call('inspect', {'path': document}, good)[0] == 200
    assert call('inspect', {'path': document}, MAIN)[0] == 200, 'the service token itself is unchanged'


def test_a_scoped_token_is_still_confined_to_the_authorized_roots(setup, tmp_path):
    _, call = setup
    outside = tmp_path / 'outside.byax'
    execute('new', {'out': str(outside), 'text': 'Secret.'})
    token = issue_extension_token(MAIN, 'ext-one', ['read'])
    status, body = call('inspect', {'path': str(outside)}, token)
    assert status == 400 and body['error']['code'] == 'invalid_input'
    assert service.TOKEN_SCOPES == ('read', 'propose')
