"""Helpers that build small extension folders for the extension, macro, service and GUI tests."""

import json
import shutil
import textwrap
from pathlib import Path

from byax import extensions

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / 'examples' / 'extensions'

GOOD_SCRIPT = """
import json, sys
request = json.load(sys.stdin)
target = request['targets'][0]
json.dump(
    {'api_version': 1, 'findings': [
        {'rule': 'r1', 'target': target['id'], 'start': 0, 'end': 4, 'message': 'hello there'}]},
    sys.stdout,
)
"""


def manifest(identifier='ext-one', *, scopes=('checks', 'read'), timeout=5, rules=None, **extra):
    check = {
        'id': 'c1',
        'type': 'script',
        'path': 'check.py',
        'timeout': timeout,
        'title': 'Test check',
        **({'rules': rules} if rules else {}),
    }
    return {
        'id': identifier,
        'name': 'Test extension ' + identifier,
        'version': '1.0.0',
        'api_version': 1,
        'description': 'A test extension.',
        'author': 'Tests',
        'licence': 'MIT',
        'kinds': ['check'],
        'scopes': list(scopes),
        'entry_points': {'checks': [check]},
        **extra,
    }


def make(tmp_path, identifier='ext-one', script=GOOD_SCRIPT, **options):
    """A folder holding a script check; ``options`` go to ``manifest``."""
    folder = tmp_path / 'src' / identifier
    folder.mkdir(parents=True)
    (folder / 'check.py').write_text(textwrap.dedent(script), encoding='utf-8')
    (folder / extensions.MANIFEST_NAME).write_text(
        json.dumps(manifest(identifier, **options)), encoding='utf-8'
    )
    return folder


def make_hook(
    tmp_path, identifier='hook-one', script='', *, formats=None, timeout=5, scopes=('export-hook',)
):
    folder = tmp_path / 'src' / identifier
    folder.mkdir(parents=True)
    (folder / 'hook.py').write_text(textwrap.dedent(script), encoding='utf-8')
    entry = {'id': 'h1', 'path': 'hook.py', 'timeout': timeout, 'title': 'Test hook'}
    if formats:
        entry['formats'] = formats
    data = {
        'id': identifier,
        'name': 'Test hook ' + identifier,
        'version': '1.0.0',
        'api_version': 1,
        'description': 'A test hook.',
        'author': 'Tests',
        'licence': 'MIT',
        'kinds': ['export-hook'],
        'scopes': list(scopes),
        'entry_points': {'export_hooks': [entry]},
    }
    (folder / extensions.MANIFEST_NAME).write_text(json.dumps(data), encoding='utf-8')
    return folder


def install(folder, *, enable=True, replace=False):
    """Install the way a person does: inspect, then repeat the shown hash and scopes."""
    plan = extensions.inspect_package(folder)
    return extensions.install(
        folder, sha256=plan['sha256'], scopes=plan['requested_scopes'], enable=enable, replace=replace
    )


def install_example(name, *, enable=True):
    return install(EXAMPLES / name, enable=enable, replace=True)


def copy_example(tmp_path, name):
    target = tmp_path / 'copies' / name
    shutil.copytree(EXAMPLES / name, target)
    return target
