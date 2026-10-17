"""Opt-in, manual update check against the project's GitHub Releases.

The only request ByAuthorX ever makes to learn about a new version is the one a person asks for
with Help > Check for updates or ``byax version --check-updates``. Nothing runs at startup or in
the background, nothing is downloaded or installed, and the response is treated as data: only the
tag, the publication date and a release link on this project's own page are read from it.
"""

from __future__ import annotations

import os
import re
from typing import Any
from urllib.parse import urlparse

import httpx

from .errors import ByaxError

REPOSITORY = 'jxburros/ByAuthorX'
RELEASES_API = f'https://api.github.com/repos/{REPOSITORY}/releases/latest'
RELEASES_PAGE = f'https://github.com/{REPOSITORY}/releases'
DISABLE_ENV = 'BYAX_DISABLE_UPDATE_CHECK'
MAX_RESPONSE_BYTES = 1024 * 1024
MAX_REDIRECTS = 2

_VERSION = re.compile(r'^v?(\d+)\.(\d+)\.(\d+)(?:-([0-9A-Za-z.-]+))?(?:\+[0-9A-Za-z.-]+)?$')


def update_checks_disabled(environ=None) -> bool:
    """Managed desktops can switch the feature off entirely with ``BYAX_DISABLE_UPDATE_CHECK=1``."""
    value = (os.environ if environ is None else environ).get(DISABLE_ENV, '')
    return value.strip().lower() in ('1', 'true', 'yes')


def parse_version(text: Any) -> tuple | None:
    """Sortable key for ``x.y.z[-prerelease]`` (a leading ``v`` is allowed), or ``None``."""
    match = _VERSION.match(text.strip()) if isinstance(text, str) else None
    if not match:
        return None
    major, minor, patch, pre = match.groups()
    if pre is None:
        return (int(major), int(minor), int(patch), 1, ())
    parts = tuple((0, int(p), '') if p.isdigit() else (1, 0, p) for p in pre.split('.'))
    return (int(major), int(minor), int(patch), 0, parts)


def compare_versions(current: str, latest: str) -> int:
    """-1 when ``latest`` is newer than ``current``, 0 when equal, 1 when ``current`` is newer."""
    a, b = parse_version(current), parse_version(latest)
    if a is None or b is None:
        raise ByaxError('invalid_input', 'Not a version number', current=current, latest=latest)
    return (a > b) - (a < b)


def _fetch(client: httpx.Client) -> httpx.Response:
    url = RELEASES_API
    for _ in range(MAX_REDIRECTS + 1):
        parsed = urlparse(url)
        if parsed.scheme != 'https' or parsed.hostname != 'api.github.com':
            raise ByaxError('provider_failure', 'Update check was redirected away from GitHub', target=url)
        with client.stream('GET', url) as response:
            if response.is_redirect and 'location' in response.headers:
                url = str(response.url.join(response.headers['location']))
                continue
            body = bytearray()
            for chunk in response.iter_bytes():
                body.extend(chunk)
                if len(body) > MAX_RESPONSE_BYTES:
                    raise ByaxError('limit_exceeded', 'Update response is larger than 1 MiB')
            return httpx.Response(
                response.status_code, headers=response.headers, content=bytes(body), request=response.request
            )
    raise ByaxError('provider_failure', 'Too many redirects while checking for updates')


def check_for_updates(*, current: str | None = None, timeout: float = 10.0) -> dict[str, Any]:
    """Make exactly one request to the GitHub Releases API and compare versions.

    Proxy settings come from the standard environment variables through httpx. Raises
    ``ByaxError`` when the check is disabled, the network or GitHub is unavailable, or the answer
    is not a release of this project.
    """
    from . import __version__

    current = current or __version__
    if update_checks_disabled():
        raise ByaxError(
            'unsupported_feature',
            f'Update checks are disabled by {DISABLE_ENV}',
            retry_guidance=RELEASES_PAGE,
        )
    if parse_version(current) is None:
        raise ByaxError('invalid_input', 'This build has no comparable version number', current=current)
    headers = {
        'Accept': 'application/vnd.github+json',
        'X-GitHub-Api-Version': '2022-11-28',
        'User-Agent': f'ByAuthorX/{current}',
    }
    try:
        with httpx.Client(timeout=timeout, follow_redirects=False, trust_env=True, headers=headers) as client:
            response = _fetch(client)
    except httpx.TimeoutException as exc:
        raise ByaxError(
            'provider_failure', 'The update check timed out', retry_guidance=RELEASES_PAGE
        ) from exc
    except httpx.HTTPError as exc:
        raise ByaxError(
            'provider_failure',
            'Could not reach GitHub to check for updates (' + type(exc).__name__ + ')',
            retry_guidance=RELEASES_PAGE,
        ) from exc
    if response.status_code in (403, 429):
        raise ByaxError(
            'provider_failure',
            'GitHub is rate limiting update checks; try again later',
            retry_guidance=RELEASES_PAGE,
        )
    if response.status_code == 404:
        raise ByaxError('not_found', 'No release has been published yet', retry_guidance=RELEASES_PAGE)
    if response.status_code != 200:
        raise ByaxError('provider_failure', f'GitHub answered with status {response.status_code}')
    try:
        release = response.json()
    except ValueError as exc:
        raise ByaxError('provider_failure', 'GitHub returned an unreadable answer') from exc
    tag = release.get('tag_name') if isinstance(release, dict) else None
    latest = parse_version(tag)
    if latest is None:
        raise ByaxError('provider_failure', 'The latest release does not carry a version tag')
    link = release.get('html_url')
    if not isinstance(link, str) or not link.startswith(RELEASES_PAGE + '/'):
        link = RELEASES_PAGE  # never show a link GitHub's answer chose outside this project
    published = release.get('published_at')
    order = compare_versions(current, tag)
    return {
        'status': 'update_available' if order < 0 else 'up_to_date' if order == 0 else 'ahead_of_release',
        'current_version': current,
        'latest_version': tag.lstrip('v'),
        'release_url': link,
        'published_at': published if isinstance(published, str) else None,
        'requests_made': 1,
        'source': RELEASES_API,
    }
