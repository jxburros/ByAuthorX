"""Series files: a codex, library attachments and member documents shared by several projects (C2).

A series is a small UTF-8 JSON file with the ``.byaxseries`` suffix, usually in the folder that
holds the books of the series::

    {
      "format": "byax-series",
      "version": 1,
      "id": "series_…",
      "title": "The Longbourn Novels",
      "codex": {"entries": {entry_id: entry}},
      "libraries": [{"library_id": "lib_…", "role": "supplementary"}],
      "documents": ["Book 1/book-1.byax", "Book 2/book-2.byax"]
    }

A document refers to its series with ``settings.series = {"path": "../longbourn.byaxseries"}``,
relative to the document's folder. The path must stay inside the document's folder or its parent
folder, and series ``documents`` must stay inside the series file's folder, so a document cannot
point the application at arbitrary files. Reading a series never writes and never uses the network.

Precedence: a document codex entry overrides a series entry with the same identity or the same
name (case-insensitive); a document library attachment overrides the series attachment of the same
library. Attaching a series attaches its libraries, so, like library attachment, it is a local CLI
or desktop action and is refused over MCP and REST.
"""

from __future__ import annotations

import copy
import json
import os
import uuid
from collections.abc import Callable
from pathlib import Path, PurePosixPath
from typing import Any

from .errors import ByaxError

FORMAT = 'byax-series'
VERSION = 1
SUFFIX = '.byaxseries'
FIELDS = frozenset(('format', 'version', 'id', 'title', 'codex', 'libraries', 'documents'))
MAX_BYTES = 8 * 1024 * 1024
MAX_DOCUMENTS = 500
ROLES = ('authoritative', 'supplementary', 'open')


def _relative(value: Any, suffix: str) -> bool:
    if not isinstance(value, str) or not value or len(value) > 1024 or '\0' in value or '\\' in value:
        return False
    path = PurePosixPath(value)
    return (
        not path.is_absolute()
        and not value.startswith('~')
        and path.suffix.lower() == suffix
        and ':' not in value
    )


def validate_reference(value: Any, fail: Callable[[str, str], Any]) -> None:
    """``settings.series``: ``{"path": "<relative>.byaxseries"}`` or absent."""
    if value is None:
        return
    if not isinstance(value, dict) or set(value) != {'path'} or not _relative(value['path'], SUFFIX):
        fail('settings.series must be {"path": "<relative path>.byaxseries"}.', 'settings.series')


def validate(data: Any) -> dict[str, Any]:
    """Validate a parsed series file; returns it unchanged or raises ``invalid_input``."""
    from .codex import validate_entries

    def fail(message, field):
        raise ByaxError('invalid_input', 'Invalid series file: ' + message, field=field)

    if not isinstance(data, dict) or data.get('format') != FORMAT:
        fail('not a ByAuthorX series file.', 'format')
    if data.get('version') != VERSION:
        raise ByaxError(
            'unsupported_version',
            'Series file version is not supported.',
            found=data.get('version'),
            supported=[1],
        )
    unknown = set(data) - FIELDS
    if unknown:
        fail('unknown fields ' + ', '.join(sorted(unknown)), 'series')
    if not isinstance(data.get('id'), str) or not data['id']:
        fail('a series needs an identity.', 'id')
    if 'title' in data and (not isinstance(data['title'], str) or len(data['title']) > 500):
        fail('the title must be text.', 'title')
    codex = data.get('codex', {'entries': {}})
    if not isinstance(codex, dict) or set(codex) - {'entries'}:
        fail('codex must be {"entries": {...}}.', 'codex')
    validate_entries(codex.get('entries', {}), fail, 'codex.entries')
    libraries = data.get('libraries', [])
    if not isinstance(libraries, list) or any(
        not isinstance(item, dict)
        or set(item) - {'library_id', 'role'}
        or not isinstance(item.get('library_id'), str)
        or not item['library_id'].startswith('lib_')
        or item.get('role', 'supplementary') not in ROLES
        for item in libraries
    ):
        fail('libraries must be attachments with a library_id and an optional role.', 'libraries')
    documents = data.get('documents', [])
    if (
        not isinstance(documents, list)
        or len(documents) > MAX_DOCUMENTS
        or any(not _relative(d, '.byax') for d in documents)
    ):
        fail('documents must be relative .byax paths.', 'documents')
    return data


def new_series(
    title: str = '', entries: dict[str, Any] | None = None, libraries: Any = None, documents: Any = None
) -> dict[str, Any]:
    return validate(
        {
            'format': FORMAT,
            'version': VERSION,
            'id': 'series_' + uuid.uuid4().hex,
            'title': title,
            'codex': {'entries': copy.deepcopy(entries or {})},
            'libraries': copy.deepcopy(libraries or []),
            'documents': list(documents or []),
        }
    )


def load(path: str | os.PathLike[str]) -> dict[str, Any]:
    """Read and validate a series file (size-limited, UTF-8 JSON)."""
    target = Path(path)
    if target.suffix.lower() != SUFFIX:
        raise ByaxError('invalid_input', 'Series files use the .byaxseries suffix.', path=str(target))
    try:
        with target.open('rb') as handle:
            raw = handle.read(MAX_BYTES + 1)
    except FileNotFoundError:
        raise ByaxError('not_found', 'Series file does not exist.', path=str(target)) from None
    if len(raw) > MAX_BYTES:
        raise ByaxError('limit_exceeded', 'Series file exceeds 8 MiB.', path=str(target))
    try:
        data = json.loads(raw.decode('utf-8'))
    except (UnicodeDecodeError, ValueError) as exc:
        raise ByaxError('invalid_input', 'Series file is not UTF-8 JSON.', path=str(target)) from exc
    return validate(data)


def save(path: str | os.PathLike[str], data: dict[str, Any], *, overwrite: bool = False) -> dict[str, Any]:
    """Write a series file atomically. Existing files are replaced only with ``overwrite``."""
    from .providers import _atomic_json

    target = Path(path)
    if target.suffix.lower() != SUFFIX:
        raise ByaxError('invalid_input', 'Series files use the .byaxseries suffix.', field='out')
    if target.exists() and not overwrite:
        raise ByaxError(
            'conflict', 'Series file already exists; explicitly select overwrite.', target=str(target)
        )
    validate(data)
    target.parent.mkdir(parents=True, exist_ok=True)
    _atomic_json(target, data)
    return {
        'path': str(target),
        'entries': len(data['codex']['entries']),
        'libraries': len(data['libraries']),
    }


def resolve(doc: dict[str, Any], document_path: str | os.PathLike[str] | None) -> dict[str, Any] | None:
    """The document's series reference resolved against its folder, or None when it has none.

    Returns ``{"path", "state", "series"?, "message"?}`` with state ``ok``, ``missing``,
    ``invalid``, ``outside_project`` or ``unsaved`` (no document path to resolve against).
    """
    reference = (doc.get('settings') or {}).get('series')
    if not isinstance(reference, dict) or not isinstance(reference.get('path'), str):
        return None
    relative = reference['path']
    if document_path is None:
        return {'path': relative, 'state': 'unsaved', 'message': 'Save the document to resolve its series.'}
    folder = Path(document_path).resolve().parent
    target = (folder / relative).resolve()
    if not _relative(relative, SUFFIX) or not target.is_relative_to(folder.parent):
        return {
            'path': relative,
            'state': 'outside_project',
            'message': "A series file must be in the document's folder or its parent folder.",
        }
    try:
        data = load(target)
    except ByaxError as exc:
        state = 'missing' if exc.code == 'not_found' else 'invalid'
        return {'path': relative, 'resolved': str(target), 'state': state, 'message': exc.message}
    return {'path': relative, 'resolved': str(target), 'state': 'ok', 'series': data}


def series_of(doc: dict[str, Any], document_path: str | os.PathLike[str] | None) -> dict[str, Any] | None:
    """The loaded series data when the reference resolves, else None."""
    info = resolve(doc, document_path)
    return info['series'] if info and info['state'] == 'ok' else None


def reference_for(
    document_path: str | os.PathLike[str], series_path: str | os.PathLike[str]
) -> dict[str, str]:
    """The ``settings.series`` value pointing from a document to a series file."""
    folder = Path(document_path).resolve().parent
    target = Path(series_path).resolve()
    if target.suffix.lower() != SUFFIX:
        raise ByaxError('invalid_input', 'Series files use the .byaxseries suffix.', field='series_path')
    if not target.is_relative_to(folder.parent):
        raise ByaxError(
            'invalid_input',
            "A series file must be in the document's folder or its parent folder.",
            field='series_path',
        )
    load(target)
    return {'path': Path(os.path.relpath(target, folder)).as_posix()}


def effective_libraries(doc: dict[str, Any], series: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Document library attachments, then series attachments for libraries the document lacks."""
    own = copy.deepcopy((doc.get('settings') or {}).get('libraries', []) or [])
    present = {item.get('library_id') for item in own}
    shared = [
        dict(copy.deepcopy(item), scope='series')
        for item in (series or {}).get('libraries', [])
        if item['library_id'] not in present
    ]
    return own + shared


def documents(series_path: str | os.PathLike[str], series: dict[str, Any]) -> list[Path]:
    """Member documents that exist inside the series folder (others are skipped)."""
    folder = Path(series_path).resolve().parent
    result = []
    for relative in series.get('documents', []):
        target = (folder / relative).resolve()
        if target.is_relative_to(folder) and target.is_file():
            result.append(target)
    return result


def with_series(doc: dict[str, Any], document_path: str | os.PathLike[str] | None) -> dict[str, Any]:
    """A read-only view of ``doc`` with the series codex and libraries merged (document first).

    Use it to read (retrieval, codex queries, AI context); never pass the view to
    ``engine.apply``, since that would copy series data into the document.
    """
    data = series_of(doc, document_path)
    if data is None:
        return doc
    from .codex import effective_entries

    view = dict(doc)
    view['settings'] = dict(doc.get('settings') or {}, libraries=effective_libraries(doc, data))
    view['codex'] = {
        'entries': {
            key: {k: v for k, v in entry.items() if k != 'scope'}
            for key, entry in effective_entries(doc, data).items()
        }
    }
    return view
