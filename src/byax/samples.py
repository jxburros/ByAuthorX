"""The bundled sample project: a regression fixture that doubles as the first thing a writer opens.

The shipped file is never opened for writing. :func:`install_sample` copies it to a folder the
writer owns, so edits, saves and lock files land in the copy.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any

from .errors import ByaxError

SAMPLE_FILE = 'sample-project.byax'
SAMPLE_TITLE = 'ByAuthorX sample project'


def sample_path() -> Path:
    """Location of the read-only bundled sample (inside the package, wheel or frozen bundle)."""
    return Path(__file__).parent / 'resources' / 'samples' / SAMPLE_FILE


def default_destination() -> Path:
    """The writer's documents folder, in a ByAuthorX subfolder (created on demand)."""
    from platformdirs import user_documents_path

    return user_documents_path() / 'ByAuthorX'


def _free_name(directory: Path, stem: str, suffix: str) -> Path:
    candidate = directory / (stem + suffix)
    number = 2
    while candidate.exists():
        candidate = directory / f'{stem} ({number}){suffix}'
        number += 1
    return candidate


def install_sample(destination: str | os.PathLike[str] | None = None) -> dict[str, Any]:
    """Copy the sample project and return where it went.

    ``destination`` is a ``.byax`` file path that must not exist yet, or a folder (default: the
    documents folder). A folder receives ``ByAuthorX sample project.byax``, numbered when taken, so
    an earlier copy that the writer edited is never replaced.
    """
    source = sample_path()
    if not source.is_file():
        raise ByaxError('missing_dependency', 'The bundled sample project is missing from this install')
    target = Path(destination) if destination else default_destination()
    if target.suffix.lower() == '.byax':
        if target.exists():
            raise ByaxError(
                'conflict', 'The destination already exists; choose another name', target=str(target)
            )
        directory, final = target.parent, target
    else:
        directory, final = target, None
    directory.mkdir(parents=True, exist_ok=True)
    final = final or _free_name(directory, SAMPLE_TITLE, '.byax')
    fd, temporary = tempfile.mkstemp(prefix='.' + final.name + '.', suffix='.tmp', dir=directory)
    try:
        with os.fdopen(fd, 'wb') as out, source.open('rb') as handle:
            shutil.copyfileobj(handle, out)
        os.replace(temporary, final)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return {
        'path': str(final),
        'sample': str(source),
        'sha256': hashlib.sha256(final.read_bytes()).hexdigest(),
        'copied': True,
    }
