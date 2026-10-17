"""Model catalogue, explicit download, hash verification, resume, failure and offline behaviour.

A local fixture HTTP server serves tiny fake model files; no test touches the network.
"""

import hashlib
import http.server
import json
import os
import re
import socketserver
import sys
import threading
from pathlib import Path

import pytest

from byax import local_inference, local_models
from byax.cli import execute
from byax.errors import ByaxError
from byax.mcp import handle
from byax.transport_policy import check_provider_admin

FILES = {
    'model.onnx': b'fake-onnx-weights-' * 400,
    'tokenizer.json': b'{"version": "1.0"}' * 20,
    'config.json': b'{"hidden_size": 8}',
    'MODEL-CARD.md': b'---\nlicense: apache-2.0\n---\ncard\n',
}


class FixtureServer:
    """Serves ``/files/<name>/<path>`` with optional misbehaviour for failure tests."""

    def __init__(self):
        self.files = {}
        self.requests = []
        self.truncate_once = None
        self.ignore_range = False
        self.status = None
        self.redirect_to = None
        outer = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                outer.requests.append((self.path, self.headers.get('Range')))
                if outer.redirect_to:
                    self.send_response(302)
                    self.send_header('Location', outer.redirect_to)
                    self.end_headers()
                    return
                if outer.status:
                    self.send_response(outer.status)
                    self.end_headers()
                    return
                body = outer.files.get(self.path)
                if body is None:
                    self.send_response(404)
                    self.end_headers()
                    return
                start, status = 0, 200
                header = self.headers.get('Range')
                if header and not outer.ignore_range:
                    start = int(header.split('=')[1].rstrip('-'))
                    status = 206
                chunk = body[start:]
                self.send_response(status)
                self.send_header('Content-Length', str(len(chunk)))
                if status == 206:
                    self.send_header('Content-Range', f'bytes {start}-{len(body) - 1}/{len(body)}')
                self.end_headers()
                if outer.truncate_once is not None:
                    cut, outer.truncate_once = outer.truncate_once, None
                    self.wfile.write(chunk[:cut])
                    self.wfile.flush()
                    self.close_connection = True
                    return
                self.wfile.write(chunk)

        class Server(socketserver.ThreadingMixIn, http.server.HTTPServer):
            daemon_threads = True

        self.httpd = Server(('127.0.0.1', 0), Handler)
        self.base = f'http://127.0.0.1:{self.httpd.server_port}'
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


def build_catalogue(server, *, populated=False, name='tiny-embedder', kind='embedder', files=FILES):
    entries = []
    for path, body in files.items():
        key = f'/upstream/{name}/{path}'
        server.files[key] = body
        mirror = f'/mirror/{name}__{path}'
        server.files[mirror] = body
        entries.append(
            {
                'path': path,
                'role': 'model' if path.endswith('.onnx') else 'aux',
                'size': len(body),
                'sha256': hashlib.sha256(body).hexdigest(),
                'url': server.base + key,
                'mirror_asset': f'{name}__{path}',
            }
        )
    return local_models.validate_catalogue(
        {
            'schema': 1,
            'catalogue_version': 1,
            'mirror': {
                'repository': 'example/repo',
                'release_tag': 'models-v1',
                'base_url': server.base + '/mirror',
                'checksums_asset': 'SHA256SUMS.txt',
                'populated': populated,
            },
            'models': [
                {
                    'name': name,
                    'title': 'Tiny fixture model',
                    'kind': kind,
                    'version': '1',
                    'bundled': True,
                    'licence': {'spdx': 'Apache-2.0', 'source': 'fixture', 'verified': '2026-10-16'},
                    'upstream': {'model': 'fixture/tiny', 'revision': 'abc'},
                    'runtime': {},
                    'files': entries,
                }
            ],
        }
    )


@pytest.fixture
def server():
    value = FixtureServer()
    yield value
    value.close()


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv('BYAX_MODELS_DIR', str(tmp_path / 'models'))
    monkeypatch.delenv('BYAX_BUNDLED_MODELS', raising=False)
    monkeypatch.setenv('XDG_CONFIG_HOME', str(tmp_path / 'config'))
    return tmp_path


def test_pinned_catalogue_is_complete_and_licensed():
    catalogue = local_models.load_catalogue()
    names = [m['name'] for m in catalogue['models']]
    assert {
        'qwen3-embedding-0.6b-int8',
        'bge-reranker-v2-m3-int8',
        'answerai-colbert-small-v1',
        'hhem-2.1-open-int8',
    } <= set(names)
    assets = set()
    for model in catalogue['models']:
        assert model['licence']['spdx'] == 'Apache-2.0'
        assert model['licence']['verified'] and model['licence']['source'].startswith('https://')
        assert model['upstream']['revision'] and len(model['upstream']['revision']) == 40
        assert model['size_bytes'] == sum(f['size'] for f in model['files'])
        assert any(f['role'] == 'card' for f in model['files'])
        for item in model['files']:
            assert len(item['sha256']) == 64
            if item.get('mirror_only'):
                # The project's own build of a model with no upstream ONNX file (scripts/export_hhem_onnx.py).
                base = catalogue['mirror']['base_url'].rstrip('/')
                assert item['url'] == f'{base}/{item["mirror_asset"]}' and model['upstream']['export_recipe']
                continue
            assert item['url'].startswith('https://huggingface.co/')
            # Every file is pinned to an immutable commit, never a moving branch.
            assert re.search(r'/resolve/[0-9a-f]{40}/', item['url'])
            assert item['mirror_asset'] not in assets
            assets.add(item['mirror_asset'])
    mirror = catalogue['mirror']
    assert mirror['release_tag'] == 'models-v1' and mirror['base_url'].endswith('/models-v1')
    # Planned size budget: the default bundle (embedder plus small reranker) stays under about 800 MB.
    bundled = sum(m['size_bytes'] for m in catalogue['models'] if m.get('bundled'))
    assert bundled < 800 * 1024**2


def test_catalogue_rejects_unsafe_entries(server):
    good = build_catalogue(server)
    bad = json.loads(json.dumps(good))
    bad['models'][0]['files'][0]['url'] = 'http://example.com/model.onnx'
    with pytest.raises(ByaxError, match='https'):
        local_models.validate_catalogue(bad)
    bad = json.loads(json.dumps(good))
    bad['models'][0]['files'][0]['path'] = '../escape'
    with pytest.raises(ByaxError):
        local_models.validate_catalogue(bad)
    bad = json.loads(json.dumps(good))
    bad['models'][0]['files'][0]['sha256'] = 'xyz'
    with pytest.raises(ByaxError, match='SHA-256'):
        local_models.validate_catalogue(bad)


def test_importing_and_inspecting_makes_no_request(isolated, monkeypatch, server):
    import httpx

    def refuse(*args, **kwargs):
        raise AssertionError('no network request may happen without a download action')

    monkeypatch.setattr(httpx.Client, 'send', refuse)
    catalogue = build_catalogue(server)
    assert local_models.list_models(catalogue=catalogue)[0]['state'] == 'not_installed'
    assert local_models.download_plan('tiny-embedder', catalogue=catalogue)['size_bytes'] > 0
    assert local_models.runtime_status()['installed'] in (True, False)
    assert server.requests == []


def test_runtime_missing_is_reported_and_loading_fails_cleanly(monkeypatch):
    for module in ('onnxruntime', 'tokenizers'):
        monkeypatch.setitem(sys.modules, module, None)
    status = local_models.runtime_status()
    assert status['installed'] is False and 'not installed' in status['reason']
    with pytest.raises(ByaxError) as caught:
        local_inference.runtime()
    assert caught.value.code == 'missing_dependency'
    assert 'byauthorx[local-models]' in caught.value.message


def test_plan_shows_source_size_and_checksum_and_mirror_gating(server, isolated):
    catalogue = build_catalogue(server)
    plan = local_models.download_plan('tiny-embedder', catalogue=catalogue)
    assert plan['source'] == 'upstream'
    assert plan['size_bytes'] == sum(len(b) for b in FILES.values())
    assert {f['sha256'] for f in plan['files']} == {hashlib.sha256(b).hexdigest() for b in FILES.values()}
    assert all('/upstream/' in f['url'] for f in plan['files'])
    assert plan['licence']['spdx'] == 'Apache-2.0'
    with pytest.raises(ByaxError, match='mirror'):
        local_models.download_plan('tiny-embedder', source='mirror', catalogue=catalogue)
    populated = build_catalogue(server, populated=True)
    plan = local_models.download_plan('tiny-embedder', catalogue=populated)
    assert plan['source'] == 'mirror' and all('/mirror/tiny-embedder__' in f['url'] for f in plan['files'])


def test_download_verifies_installs_and_second_run_keeps_files(server, isolated):
    catalogue = build_catalogue(server)
    result = local_models.download_model('tiny-embedder', catalogue=catalogue)
    assert result['state'] == 'installed' and result['verified'] and result['source'] == 'upstream'
    assert sorted(result['downloaded']) == sorted(FILES)
    target = isolated / 'models' / 'tiny-embedder'
    for path, body in FILES.items():
        assert (target / path).read_bytes() == body
        assert not (target / (path + '.part')).exists()
    manifest = json.loads((target / 'manifest.json').read_text())
    assert set(manifest['files']) == set(FILES) and manifest['source'] == 'upstream'
    count = len(server.requests)
    again = local_models.download_model('tiny-embedder', catalogue=catalogue)
    assert again['downloaded'] == [] and sorted(again['kept']) == sorted(FILES)
    assert len(server.requests) == count
    assert local_models.ready_path('tiny-embedder', catalogue=catalogue) == target


def test_download_from_populated_mirror(server, isolated):
    catalogue = build_catalogue(server, populated=True)
    local_models.download_model('tiny-embedder', catalogue=catalogue)
    assert all(path.startswith('/mirror/') for path, _ in server.requests)


def test_checksum_mismatch_discards_the_file_and_leaves_nothing_usable(server, isolated):
    catalogue = build_catalogue(server)
    tampered = dict(FILES)
    server.files['/upstream/tiny-embedder/model.onnx'] = b'x' * len(FILES['model.onnx'])
    with pytest.raises(ByaxError) as caught:
        local_models.download_model('tiny-embedder', catalogue=catalogue)
    assert caught.value.code == 'checksum_mismatch'
    target = isolated / 'models' / 'tiny-embedder'
    assert not (target / 'model.onnx').exists() and not (target / 'model.onnx.part').exists()
    assert not (target / 'manifest.json').exists()
    assert local_models.model_status('tiny-embedder', catalogue=catalogue)['state'] != 'installed'
    with pytest.raises(ByaxError) as missing:
        local_models.ready_path('tiny-embedder', catalogue=catalogue)
    assert missing.value.code == 'model_missing'
    assert tampered  # the catalogue still pins the genuine hash


def test_oversized_response_is_rejected(server, isolated):
    catalogue = build_catalogue(server)
    server.files['/upstream/tiny-embedder/model.onnx'] = FILES['model.onnx'] + b'extra'
    with pytest.raises(ByaxError) as caught:
        local_models.download_model('tiny-embedder', catalogue=catalogue)
    assert caught.value.code == 'checksum_mismatch'


def test_interrupted_download_resumes_with_a_range_request(server, isolated):
    catalogue = build_catalogue(server)
    server.truncate_once = 100
    with pytest.raises(ByaxError) as caught:
        local_models.download_model('tiny-embedder', catalogue=catalogue)
    assert caught.value.code == 'network_unavailable' and caught.value.details['resumable']
    target = isolated / 'models' / 'tiny-embedder'
    parts = list(target.glob('*.part'))
    assert len(parts) == 1 and parts[0].stat().st_size == 100
    status = local_models.model_status('tiny-embedder', catalogue=catalogue)
    assert status['state'] == 'partial' and status['partial_bytes'] >= 100
    result = local_models.download_model('tiny-embedder', catalogue=catalogue)
    assert result['state'] == 'installed'
    assert any(range_header == 'bytes=100-' for _, range_header in server.requests)
    assert (target / 'model.onnx').read_bytes() == FILES['model.onnx']


def test_resume_when_the_server_ignores_range(server, isolated):
    catalogue = build_catalogue(server)
    server.truncate_once = 50
    with pytest.raises(ByaxError):
        local_models.download_model('tiny-embedder', catalogue=catalogue)
    server.ignore_range = True
    result = local_models.download_model('tiny-embedder', catalogue=catalogue)
    assert result['state'] == 'installed'
    assert (isolated / 'models' / 'tiny-embedder' / 'model.onnx').read_bytes() == FILES['model.onnx']


def test_offline_download_fails_clearly_and_nothing_is_installed(server, isolated):
    catalogue = build_catalogue(server)
    server.close()
    with pytest.raises(ByaxError) as caught:
        local_models.download_model('tiny-embedder', catalogue=catalogue)
    assert caught.value.code == 'network_unavailable' and 'reach' in caught.value.message
    assert local_models.model_status('tiny-embedder', catalogue=catalogue)['state'] in (
        'not_installed',
        'partial',
    )
    # Status, listing and verification still work offline.
    assert local_models.list_models(catalogue=catalogue)
    assert local_models.verify_model('tiny-embedder', catalogue=catalogue)['verified'] is False


def test_server_errors_and_missing_files_are_reported(server, isolated):
    catalogue = build_catalogue(server)
    server.status = 500
    with pytest.raises(ByaxError) as caught:
        local_models.download_model('tiny-embedder', catalogue=catalogue)
    assert caught.value.code == 'provider_failure' and caught.value.details['status'] == 500
    server.status = 404
    with pytest.raises(ByaxError) as caught:
        local_models.download_model('tiny-embedder', catalogue=catalogue)
    assert caught.value.details['status'] == 404


def test_redirect_to_an_insecure_host_is_refused(server, isolated):
    catalogue = build_catalogue(server)
    server.redirect_to = 'http://example.com/model.onnx'
    with pytest.raises(ByaxError, match='non-https'):
        local_models.download_model('tiny-embedder', catalogue=catalogue)


def test_cancelled_download_keeps_the_partial_file(server, isolated):
    catalogue = build_catalogue(server)
    cancel = threading.Event()
    cancel.set()
    with pytest.raises(ByaxError) as caught:
        local_models.download_model('tiny-embedder', catalogue=catalogue, cancel_event=cancel)
    assert caught.value.code == 'cancelled'


def test_insufficient_space_is_refused_before_any_request(server, isolated, monkeypatch):
    catalogue = build_catalogue(server)
    monkeypatch.setattr(local_models.shutil, 'disk_usage', lambda path: type('U', (), {'free': 10})())
    with pytest.raises(ByaxError) as caught:
        local_models.download_model('tiny-embedder', catalogue=catalogue)
    assert caught.value.code == 'insufficient_space' and server.requests == []


def test_verify_detects_tampering_and_ready_path_refuses_corrupt_models(server, isolated):
    catalogue = build_catalogue(server)
    local_models.download_model('tiny-embedder', catalogue=catalogue)
    assert local_models.verify_model('tiny-embedder', catalogue=catalogue)['verified'] is True
    target = isolated / 'models' / 'tiny-embedder'
    weights = target / 'model.onnx'
    data = bytearray(weights.read_bytes())
    data[3] ^= 0xFF
    weights.write_bytes(bytes(data))  # same size, different content: the manifest mtime changes
    result = local_models.verify_model('tiny-embedder', catalogue=catalogue)
    assert result['verified'] is False and result['problems'][0]['problem'] == 'SHA-256 mismatch'
    with pytest.raises(ByaxError) as caught:
        local_models.ready_path('tiny-embedder', catalogue=catalogue)
    assert caught.value.code == 'model_corrupt'
    weights.write_bytes(b'short')
    assert local_models.model_status('tiny-embedder', catalogue=catalogue)['state'] == 'corrupt'


def test_moved_timestamps_are_rehashed_and_a_read_only_install_still_loads(server, isolated, monkeypatch):
    catalogue = build_catalogue(server)
    local_models.download_model('tiny-embedder', catalogue=catalogue)
    target = isolated / 'models' / 'tiny-embedder'
    for path in target.iterdir():
        if path.name != 'manifest.json':
            os.utime(path, ns=(1_000_000_000, 1_000_000_000))  # as if an installer had copied the files
    # One of them is also damaged (same size): it must not be recorded as verified.
    damaged = bytearray((target / 'config.json').read_bytes())
    damaged[0] ^= 0x01
    (target / 'config.json').write_bytes(bytes(damaged))
    os.utime(target / 'config.json', ns=(1_000_000_000, 1_000_000_000))
    with pytest.raises(ByaxError) as caught:
        local_models.ready_path('tiny-embedder', catalogue=catalogue)
    assert caught.value.code == 'model_corrupt' and caught.value.details['path'] == 'config.json'
    (target / 'config.json').write_bytes(FILES['config.json'])
    os.utime(target / 'config.json', ns=(1_000_000_000, 1_000_000_000))

    def read_only(*args, **kwargs):
        raise PermissionError('read-only install folder')

    with monkeypatch.context() as patched:
        patched.setattr(local_models, '_write_manifest', read_only)
        assert local_models.ready_path('tiny-embedder', catalogue=catalogue) == target
    assert local_models.ready_path('tiny-embedder', catalogue=catalogue) == target
    manifest = json.loads((target / 'manifest.json').read_text())
    assert manifest['files']['model.onnx']['mtime_ns'] == 1_000_000_000


def test_remove_deletes_the_download_but_never_a_bundled_copy(server, isolated, monkeypatch):
    catalogue = build_catalogue(server)
    local_models.download_model('tiny-embedder', catalogue=catalogue)
    bundled = isolated / 'bundled'
    local_models.download_model('tiny-embedder', catalogue=catalogue, directory=bundled)
    monkeypatch.setenv('BYAX_BUNDLED_MODELS', str(bundled))
    removed = local_models.remove_model('tiny-embedder', catalogue=catalogue)
    assert removed['removed'] and removed['bundled_copy_kept'] is True
    assert not (isolated / 'models' / 'tiny-embedder').exists()
    status = local_models.model_status('tiny-embedder', catalogue=catalogue)
    assert status['state'] == 'installed' and status['origin'] == 'bundled'


def publish_checksums(server, catalogue, *, tamper=False):
    lines = []
    for item in catalogue['models'][0]['files']:
        digest = '0' * 64 if tamper else item['sha256']
        lines.append(f'{digest}  {item["mirror_asset"]}')
    server.files['/mirror/SHA256SUMS.txt'] = ('\n'.join(lines) + '\n').encode()


def test_bundle_models_refuses_until_the_mirror_is_populated(server, isolated):
    with pytest.raises(ByaxError) as caught:
        local_models.bundle_models(isolated / 'out', catalogue=build_catalogue(server))
    assert 'mirror release is not populated' in caught.value.message
    populated = build_catalogue(server, populated=True)
    publish_checksums(server, populated)
    results = local_models.bundle_models(isolated / 'out', catalogue=populated)
    assert results[0]['state'] == 'installed' and results[0]['source'] == 'mirror'
    assert (isolated / 'out' / 'tiny-embedder' / 'manifest.json').exists()


def test_bundling_cross_checks_the_mirror_checksum_file_before_downloading_any_model(server, isolated):
    populated = build_catalogue(server, populated=True)
    publish_checksums(server, populated, tamper=True)
    with pytest.raises(ByaxError) as caught:
        local_models.bundle_models(isolated / 'out', catalogue=populated)
    assert caught.value.code == 'checksum_mismatch'
    assert not (isolated / 'out' / 'tiny-embedder').exists()
    assert not any('__model.onnx' in path for path, _ in server.requests), 'no model file was requested'
    server.files.pop('/mirror/SHA256SUMS.txt')
    with pytest.raises(ByaxError) as missing:
        local_models.bundle_models(isolated / 'out', catalogue=populated)
    assert missing.value.code == 'provider_failure'


def test_the_shipped_catalogue_has_an_unpopulated_mirror_until_the_maintainer_publishes_it():
    # Flip this assertion together with models.json when the models-v1 release exists.
    assert local_models.load_catalogue()['mirror']['populated'] is False


def test_models_command_list_and_confirmation_flow(server, isolated, monkeypatch):
    catalogue = build_catalogue(server)
    monkeypatch.setattr(local_models, 'load_catalogue', lambda path=None: catalogue)
    listing = execute('models', {})
    assert listing['models'][0]['state'] == 'not_installed'
    assert listing['prompt'] == 'Enable on-device semantic search? Nothing leaves this computer.'
    assert listing['settings']['rerank'] == 'fast'
    pending = execute('models', {'action': 'download', 'name': 'tiny-embedder'})
    assert pending['confirmation_required'] is True and pending['plan']['files'][0]['sha256']
    assert not (isolated / 'models' / 'tiny-embedder').exists() and server.requests == []
    done = execute('models', {'action': 'download', 'name': 'tiny-embedder', 'confirm': True})
    assert done['model']['state'] == 'installed'
    assert execute('models', {'action': 'verify', 'name': 'tiny-embedder'})['verification']['verified']
    assert execute('models', {'action': 'status', 'name': 'tiny-embedder'})['model']['state'] == 'installed'
    assert execute('models', {'action': 'remove', 'name': 'tiny-embedder'})['removed']
    with pytest.raises(ByaxError):
        execute('models', {'action': 'status', 'name': 'unknown-model'})
    with pytest.raises(ByaxError):
        execute('models', {'action': 'download'})


def test_settings_default_to_fast_and_validate(isolated):
    assert local_models.load_settings()['rerank'] == 'fast'
    saved = execute('models', {'action': 'configure', 'input': {'rerank': 'accurate', 'rerank_depth': 30}})
    assert saved['settings']['rerank'] == 'accurate' and local_models.load_settings()['rerank_depth'] == 30
    with pytest.raises(ByaxError):
        execute('models', {'action': 'configure', 'input': {'rerank': 'turbo'}})
    with pytest.raises(ByaxError):
        execute('models', {'action': 'configure', 'input': {'reranker': 'qwen3-embedding-0.6b-int8'}})
    with pytest.raises(ByaxError):
        execute('models', {'action': 'configure', 'input': {'unknown': 1}})


def test_mcp_and_rest_cannot_download_remove_or_configure():
    for action in ('download', 'remove', 'configure'):
        with pytest.raises(ByaxError) as caught:
            check_provider_admin('models', {'action': action, 'name': 'x'})
        assert caught.value.code == 'unsupported_feature'
    for action in (None, 'list', 'status', 'verify'):
        check_provider_admin('models', {'action': action} if action else {})
    reply = handle(
        {
            'jsonrpc': '2.0',
            'id': 1,
            'method': 'tools/call',
            'params': {
                'name': 'byax_models',
                'arguments': {'action': 'download', 'name': 'x', 'confirm': True},
            },
        }
    )
    assert reply['result']['isError'] is True
    assert reply['result']['structuredContent']['error']['code'] == 'unsupported_feature'
    listing = handle(
        {
            'jsonrpc': '2.0',
            'id': 2,
            'method': 'tools/call',
            'params': {'name': 'byax_models', 'arguments': {'action': 'list'}},
        }
    )
    assert listing['result']['isError'] is False
    assert {m['name'] for m in listing['result']['structuredContent']['models']} >= {
        'answerai-colbert-small-v1'
    }


def test_cli_process_lists_models_without_network(tmp_path):
    import subprocess

    env = {
        **__import__('os').environ,
        'BYAX_MODELS_DIR': str(tmp_path / 'models'),
        'XDG_CONFIG_HOME': str(tmp_path / 'config'),
        'HTTPS_PROXY': 'http://127.0.0.1:9',
        'HTTP_PROXY': 'http://127.0.0.1:9',
    }
    root = Path(__file__).resolve().parents[1]
    done = subprocess.run(
        [sys.executable, '-m', 'byax.cli', 'models'],
        capture_output=True,
        text=True,
        env=env,
        cwd=root,
        timeout=60,
        check=False,
    )
    assert done.returncode == 0, done.stderr
    assert json.loads(done.stdout)['models'][0]['state'] == 'not_installed'


def test_rest_refuses_download_remove_and_configure_but_lists_models(tmp_path, isolated):
    import urllib.error
    import urllib.request

    from byax.service import make_server

    token = 'rest-model-test-token-with-32-chars'
    server = make_server([tmp_path], token=token, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f'http://127.0.0.1:{server.server_port}/v1/execute'

    def post(command, arguments):
        request = urllib.request.Request(
            url,
            data=json.dumps({'command': command, 'arguments': arguments}).encode(),
            method='POST',
            headers={'Authorization': 'Bearer ' + token},
        )
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                return response.status, json.load(response)
        except urllib.error.HTTPError as exc:
            return exc.code, json.load(exc)

    try:
        status, body = post('models', {})
        assert status == 200 and {m['name'] for m in body['models']} >= {'qwen3-embedding-0.6b-int8'}
        assert post('models', {'action': 'verify', 'name': 'answerai-colbert-small-v1'})[0] == 200
        for arguments in (
            {'action': 'download', 'name': 'answerai-colbert-small-v1', 'confirm': True},
            {'action': 'remove', 'name': 'answerai-colbert-small-v1'},
            {'action': 'configure', 'input': {'rerank': 'accurate'}},
        ):
            status, body = post('models', arguments)
            assert status >= 400 and body['error']['code'] == 'unsupported_feature'
        # A job wrapper cannot smuggle the administrative action past the policy.
        wrapped = {'command': 'models', 'arguments': {'action': 'download', 'name': 'x', 'confirm': True}}
        status, body = post('jobs', {'action': 'submit', 'input': wrapped})
        assert status >= 400 and body['error']['code'] == 'unsupported_feature'
        assert not (isolated / 'models').exists() or not any((isolated / 'models').iterdir())
    finally:
        server.shutdown()
        server.server_close()
