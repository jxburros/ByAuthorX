"""Launcher for extension scripts: apply process limits, then become the extension's entry point.

``python -I -m byax.extension_worker <runtime> <script> <memory> <cpu> <file-bytes>`` is the only way
the application starts extension code. Limits are set before the extension's first line runs and
carry over an ``exec``. The caller also enforces a wall-clock timeout and an output size cap, which
hold on every platform; Windows has no ``resource`` module and macOS ignores the address-space limit
(``platform_support.apply_parser_limits`` reports which limits took effect).
"""

from __future__ import annotations

import os
import runpy
import subprocess
import sys
from pathlib import Path


def run(runtime, script, memory, cpu, file_bytes):
    from .platform_support import apply_parser_limits

    apply_parser_limits(int(memory), int(cpu), int(file_bytes))
    script = str(Path(script))
    if runtime == 'python':
        sys.argv = [script]
        # -I removes the script folder from sys.path; an extension may import its own sibling modules.
        sys.path.insert(0, str(Path(script).parent))
        runpy.run_path(script, run_name='__main__')
        return 0
    if runtime == 'executable':
        if sys.platform == 'win32':
            return subprocess.call([script], stdin=sys.stdin, stdout=sys.stdout)  # no exec-with-limits here
        os.execv(script, [script])
    print(f'Unknown runtime: {runtime}', file=sys.stderr)
    return 2


def main(argv=None):
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 5:
        print('usage: extension_worker <runtime> <script> <memory> <cpu> <file-bytes>', file=sys.stderr)
        return 2
    return run(*args)


def dispatch_worker(argv=None):
    """Frozen entry points hand ``--byax-extension-worker ...`` here before importing anything heavy."""
    args = sys.argv[1:] if argv is None else argv
    if args and args[0] == '--byax-extension-worker':
        raise SystemExit(main(args[1:]))


if __name__ == '__main__':
    raise SystemExit(main())
