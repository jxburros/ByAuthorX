"""Seeded mutation fuzzing for exchange importers: hostile bytes may only raise ByaxError."""

import io
import random
import signal
import threading
import time
import zipfile

import pytest

from byax.errors import ByaxError
from byax.exchange import export_document, import_document
from byax.model import new_document, validate_document

# python-docx parsing dominates runtime; counts keep the module well under 30 s.
CASES = {'rtf': 300, 'docx': 150, 'odt': 300, 'html': 300, 'md': 300, 'txt': 200, 'epub': 250}
CASE_SECONDS = 5
FORMATS = tuple(CASES)
TOKENS = [
    b'\\',
    b'{',
    b'}',
    b'<',
    b'>',
    b'&',
    b'&#',
    b'"',
    b'\x00',
    b'\xff',
    b'\xc3\xa9',
    b'\xe6\x96\x87',
    b'\\u-1?',
    b"\\'",
    b'\\par',
    b'</w:p>',
    b'<w:r>',
    b'</text:p>',
    b'<!--',
    b'*',
    b'#',
    b'|',
    b'`',
    b'[',
    b']',
    b'(',
    b'\n',
    b'\r',
    b'9999999999',
    b'-1',
]


def _seed_document():
    doc = new_document('Fuzz seed', 'Opening paragraph with café and 文字.')
    para = doc['blocks'][0]
    para['marks'] = [{'type': 'bold', 'start': 0, 'end': 7}, {'type': 'italic', 'start': 13, 'end': 22}]
    heading = dict(para, id='blk_fuzz_heading', type='heading', text='Section heading', marks=[])
    heading['style'] = 'heading1'
    item = dict(para, id='blk_fuzz_item', type='list_item', text='List entry', marks=[], attrs={'level': 0})
    doc['blocks'] = [heading, para, item]
    return doc


@pytest.fixture(scope='module')
def seeds(tmp_path_factory):
    root = tmp_path_factory.mktemp('fuzz-seeds')
    doc = _seed_document()
    result = {}
    for fmt in FORMATS:
        path = root / f'seed.{fmt}'
        try:
            export_document(doc, path, format=fmt)
        except ByaxError:
            path.write_text('Seed text\n\nSecond paragraph\n', encoding='utf-8')
        result[fmt] = path.read_bytes()
    result['rtf'] += b'{\\rtf1\\ansi{\\fonttbl{\\f0 Arial;}}\\b Bold\\b0 \\u233? \\par Next\\tab x}'
    result['html'] += b'<table><tr><td>a</td></tr></table><ol><li>x<b>y</i></li></ol><a href="z">l</a>'
    return result


def _mutate_bytes(rng, data):
    data = bytearray(data)
    for _ in range(rng.randint(1, 6)):
        kind = rng.randrange(5)
        position = rng.randrange(len(data) + 1)
        if kind == 0 and data:
            data[min(position, len(data) - 1)] = rng.randrange(256)
        elif kind == 1:
            data[position:position] = rng.choice(TOKENS)
        elif kind == 2:
            del data[position : position + rng.randint(1, 16)]
        elif kind == 3:
            start = rng.randrange(len(data) + 1)
            data[position:position] = data[start : start + rng.randint(1, 64)]
        else:
            data = data[: max(1, position)]
    return bytes(data)


def _mutate_package(rng, data):
    """Mutate XML members inside a still-valid ZIP so parsers, not the ZIP reader, see the damage."""
    source = zipfile.ZipFile(io.BytesIO(data))
    names = source.namelist()
    victims = set(rng.sample(names, k=min(len(names), rng.randint(1, 2))))
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as target:
        for name in names:
            member = source.read(name)
            if name in victims:
                member = _mutate_bytes(rng, member)
            if rng.random() < 0.05:
                continue
            target.writestr(name, member)
    return output.getvalue()


class _Timeout(Exception):
    pass


def _alarm(_signum, _frame):
    raise _Timeout


def _import_with_timeout(path):
    """Use a per-case SIGALRM deadline where available (POSIX main thread)."""
    usable = hasattr(signal, 'setitimer') and threading.current_thread() is threading.main_thread()
    if usable:
        previous = signal.signal(signal.SIGALRM, _alarm)
        signal.setitimer(signal.ITIMER_REAL, CASE_SECONDS)
    started = time.monotonic()
    try:
        doc, _report = import_document(path)
        validate_document(doc)
    finally:
        if usable:
            signal.setitimer(signal.ITIMER_REAL, 0)
            signal.signal(signal.SIGALRM, previous)
    assert time.monotonic() - started < CASE_SECONDS, f'{path.name} took too long'


@pytest.mark.parametrize('fmt', FORMATS)
def test_mutated_imports_raise_only_byax_errors(fmt, seeds, tmp_path):
    rng = random.Random(f'byax-fuzz-{fmt}')
    seed = seeds[fmt]
    path = tmp_path / f'mutated.{fmt}'
    crashes = []
    for case in range(CASES[fmt]):
        if fmt in ('docx', 'odt', 'epub') and case % 2:
            data = _mutate_package(rng, seed)
        else:
            data = _mutate_bytes(rng, seed)
        path.write_bytes(data)
        try:
            _import_with_timeout(path)
        except ByaxError:
            pass
        except _Timeout:
            crashes.append((case, 'timeout'))
        except Exception as exc:  # noqa: BLE001 - collect every non-structured failure
            crashes.append((case, f'{type(exc).__name__}: {exc}'))
    assert not crashes, crashes


def test_seeds_import_cleanly(seeds, tmp_path):
    for fmt, data in seeds.items():
        path = tmp_path / f'seed.{fmt}'
        path.write_bytes(data)
        doc, _report = import_document(path)
        validate_document(doc)


def test_damaged_packages_report_invalid_input(seeds, tmp_path):
    source = zipfile.ZipFile(io.BytesIO(seeds['docx']))
    path = tmp_path / 'damaged.docx'
    for dropped in ('word/document.xml', '[Content_Types].xml', '_rels/.rels'):
        with zipfile.ZipFile(path, 'w') as target:
            for name in source.namelist():
                if name != dropped:
                    target.writestr(name, source.read(name))
        with pytest.raises(ByaxError) as caught:
            import_document(path)
        assert caught.value.code in ('invalid_input', 'unsafe_xml'), dropped
    stored = io.BytesIO()
    with zipfile.ZipFile(stored, 'w', zipfile.ZIP_STORED) as target:
        target.writestr('mimetype', 'application/vnd.oasis.opendocument.text')
        target.writestr('content.bin', b'payload-bytes')
    raw = bytearray(stored.getvalue())
    raw[raw.index(b'payload-bytes')] ^= 0xFF
    path = tmp_path / 'corrupt.odt'
    path.write_bytes(bytes(raw))
    with pytest.raises(ByaxError) as caught:
        import_document(path)
    assert caught.value.code == 'invalid_input'
