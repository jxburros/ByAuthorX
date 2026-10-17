"""Small platform seams: macOS font folders, durable flushes, lock identity and process limits.

Every function reads ``sys.platform`` when called, so tests can monkeypatch it and exercise the
macOS, Windows and Linux branches on any host.
"""

from __future__ import annotations

import os
import sys
import unicodedata
from pathlib import Path


def is_macos() -> bool:
    return sys.platform == 'darwin'


def system_font_dirs(home: Path | None = None) -> list[Path]:
    """Folders macOS searches for installed fonts, user folder first (it can shadow system fonts)."""
    home = Path.home() if home is None else home
    return [home / 'Library' / 'Fonts', Path('/Library/Fonts'), Path('/System/Library/Fonts')]


FONT_SUFFIXES = ('.ttf', '.otf', '.ttc')


def _squash(value: str) -> str:
    return ''.join(ch for ch in value.casefold() if ch.isalnum())


def find_system_font(family, bold=False, italic=False, directories=None):
    """File of an installed family/style found by scanning the macOS font folders, or ``None``.

    Candidates are chosen by file name and then confirmed against the family and style names
    stored inside the font, so ``Georgia.ttf`` is never returned for ``Georgia Pro``.
    """
    from .errors import ByaxError
    from .fonts import font_file_info

    wanted = _squash(family)
    if not wanted:
        return None
    dirs = system_font_dirs() if directories is None else [Path(d) for d in directories]
    candidates = []
    for directory in dirs:
        try:
            files = sorted(p for p in directory.rglob('*') if p.suffix.lower() in FONT_SUFFIXES)
        except OSError:
            continue
        candidates.extend(p for p in files if _squash(p.stem).startswith(wanted))
    style = ('bold ' if bold else '') + ('italic' if italic else '')
    style = style.strip() or 'regular'
    best = None
    for path in candidates:
        try:
            info = font_file_info(path)
        except (ByaxError, OSError):
            continue
        if _squash(info['family'] or '') != wanted:
            continue
        found = (info['style'] or 'Regular').casefold()
        found_bold, found_italic = 'bold' in found, ('italic' in found or 'oblique' in found)
        if found_bold == bold and found_italic == italic:
            return path
        if style == 'regular' and best is None and not found_bold and not found_italic:
            best = path
    return best


def fsync_file(fd: int) -> None:
    """Flush a file to stable storage. macOS ``fsync`` only reaches the drive cache, ``F_FULLFSYNC`` does."""
    if is_macos():
        try:
            import fcntl

            fcntl.fcntl(fd, getattr(fcntl, 'F_FULLFSYNC', 51))
            return
        except (ImportError, OSError):
            pass  # Some volumes (network shares) reject it; the plain fsync below is the fallback.
    os.fsync(fd)


def lock_identity(path: str) -> str:
    """Stable text for the document a lock protects.

    Windows paths are compared case-insensitively. macOS volumes are case-insensitive by default
    and store names decomposed, so the same file can be reached through differently spelled paths;
    the key folds both. A case-sensitive macOS volume only risks reporting two files whose names
    differ by case as locked together, never the reverse.
    """
    if sys.platform == 'darwin':
        return unicodedata.normalize('NFC', path).casefold()
    return os.path.normcase(path)


def apply_parser_limits(
    memory_bytes: int | None, cpu_seconds: int, file_bytes: int | None = None
) -> dict[str, bool]:
    """Apply resource limits to an isolated worker (library parser, extension script); report which took effect.

    ``file_bytes`` also caps the size of any single file the process writes (extension scripts).

    Windows has no ``resource`` module. macOS accepts ``RLIMIT_AS`` in some releases, rejects it with
    ``ValueError`` in others and does not enforce it either way, so a refusal is not an error: the
    parent still enforces a wall-clock timeout and an output size cap on every platform.
    ``memory_bytes=None`` skips the address-space limit.
    """
    applied = {'memory': False, 'cpu': False}
    if sys.platform == 'win32':
        return applied
    try:
        import resource
    except ImportError:
        return applied
    limits = [('RLIMIT_AS', memory_bytes, 'memory'), ('RLIMIT_CPU', cpu_seconds, 'cpu')]
    if file_bytes is not None:
        applied['file'] = False
        limits.append(('RLIMIT_FSIZE', file_bytes, 'file'))
    for name, limit, key in limits:
        if limit is None:  # no address-space cap for a layout model, which needs far more than a text parser
            continue
        try:
            resource.setrlimit(getattr(resource, name), (limit, limit))
            applied[key] = True
        except (ValueError, OSError, AttributeError):
            continue
    if sys.platform == 'darwin':
        # setrlimit(RLIMIT_AS) can succeed but is not enforced by the macOS kernel.
        applied['memory'] = False
    return applied
