"""Exercise an installed/frozen CLI and MCP without importing the source checkout."""

import argparse
import json
import subprocess
import tempfile
from pathlib import Path


def run(command, *, text=None):
    result = subprocess.run(command, input=text, capture_output=True, text=True, check=True, timeout=60)
    return json.loads(result.stdout)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('cli', type=Path)
    parser.add_argument('mcp', type=Path)
    parser.add_argument(
        '--expect-bundled', action='store_true', help='The standard variant: bundled models must be installed'
    )
    parser.add_argument(
        '--plain-wheel',
        action='store_true',
        help='A wheel installed without the local-models extra: the on-device runtime may be absent',
    )
    args = parser.parse_args()
    cli, mcp = str(args.cli.resolve()), str(args.mcp.resolve())
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        document, output = root / 'smoke.byax', root / 'smoke.txt'
        assert run([cli, 'version'])['engine_version']
        assert run([cli, 'capabilities'])['execution'] == 'completed'
        listing = run([cli, 'models'])
        assert listing['models'], 'the model catalogue is missing'
        if not args.plain_wheel:
            assert listing['runtime']['installed'], 'frozen app lacks the on-device runtime'
        if args.expect_bundled:
            bundled = [m for m in listing['models'] if m['bundled_by_default']]
            assert bundled and all(m['state'] == 'installed' and m['origin'] == 'bundled' for m in bundled), (
                bundled
            )
            for model in bundled:
                assert run([cli, 'models', '--action', 'verify', '--name', model['name']])['verification'][
                    'verified'
                ]
        else:
            assert all(m['state'] != 'installed' or m['origin'] != 'bundled' for m in listing['models'])
        run([cli, 'new', '--out', str(document), '--text', 'Packaged document smoke'])
        run([cli, 'export', str(document), '--out', str(output)])
        assert 'Packaged document smoke' in output.read_text(encoding='utf-8')
        run([cli, 'inspect', str(document)])
        response = run([mcp], text=json.dumps({'jsonrpc': '2.0', 'id': 1, 'method': 'ping'}) + '\n')
        assert response == {'jsonrpc': '2.0', 'id': 1, 'result': {}}
    print('Distribution CLI roundtrip and MCP smoke passed')


if __name__ == '__main__':
    main()
