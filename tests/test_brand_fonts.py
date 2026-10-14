"""The six brand fonts: files match their manifest, cover the character set, and keep their design promises.

Reads the committed fonts with the standard library and Pillow, so no authoring dependency is needed. The
authoring tests at the end run only where the font toolchain (fontTools, shapely, numpy) is installed.
"""

import hashlib
import json
import struct
import sys
import zipfile
from pathlib import Path

import pytest
from PIL import Image, ImageFont

ROOT = Path(__file__).resolve().parents[1]
BRAND = ROOT / 'assets/brand-fonts'
MANIFEST = json.loads((BRAND / 'manifest.json').read_text(encoding='utf-8'))
FAMILIES = {entry['key']: entry for entry in MANIFEST['families']}
EXPECTED = {
    'irongall': ('Irongall', 'Regular', 600),
    'scholium': ('Scholium', 'Regular', 400),
    'tenuis': ('Tenuis', 'Regular', 200),
    'brobdingnag': ('Brobdingnag', 'Black', 900),
    'onionskin': ('Onionskin', 'Regular', 400),
    'decussate': ('Decussate', 'Regular', 700),
}
BASIC = (
    'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789 .,:;!?\'"()[]{}-_/\\|@#$%&*+=<>~^`'
    '“”‘’–—…•€'
    'ÀÉÎÕÜàéîõüñçßæœøÿ¿¡«»'
)


def path(key, kind='ttf'):
    return BRAND / FAMILIES[key]['files'][kind]['path']


# A small TrueType reader: enough to check names, metrics and the character map without fontTools ------------


def tables(data):
    count = struct.unpack('>H', data[4:6])[0]
    found = {}
    for i in range(count):
        tag, _, offset, length = struct.unpack('>4sIII', data[12 + 16 * i : 28 + 16 * i])
        found[tag.decode('latin-1')] = (offset, length)
    return found


def names(data, found):
    offset, _ = found['name']
    count, string_offset = struct.unpack('>HH', data[offset + 2 : offset + 6])
    out = {}
    for i in range(count):
        platform, _, _, name_id, length, start = struct.unpack(
            '>6H', data[offset + 6 + 12 * i : offset + 18 + 12 * i]
        )
        if platform == 3:
            raw = data[offset + string_offset + start : offset + string_offset + start + length]
            out[name_id] = raw.decode('utf-16-be')
    return out


def cmap(data, found):
    offset, _ = found['cmap']
    count = struct.unpack('>H', data[offset + 2 : offset + 4])[0]
    chars = set()
    for i in range(count):
        _, _, sub = struct.unpack('>HHI', data[offset + 4 + 8 * i : offset + 12 + 8 * i])
        start = offset + sub
        fmt = struct.unpack('>H', data[start : start + 2])[0]
        if fmt == 4:
            segs = struct.unpack('>H', data[start + 6 : start + 8])[0] // 2
            ends = struct.unpack(f'>{segs}H', data[start + 14 : start + 14 + 2 * segs])
            begins = struct.unpack(f'>{segs}H', data[start + 16 + 2 * segs : start + 16 + 4 * segs])
            for lo, hi in zip(begins, ends):
                if lo != 0xFFFF:
                    chars.update(range(lo, hi + 1))
        elif fmt == 12:
            groups = struct.unpack('>I', data[start + 12 : start + 16])[0]
            for g in range(groups):
                lo, hi, _ = struct.unpack('>3I', data[start + 16 + 12 * g : start + 28 + 12 * g])
                chars.update(range(lo, hi + 1))
    return chars


def read(key):
    data = path(key).read_bytes()
    found = tables(data)
    return data, found


# manifest and files -------------------------------------------------------------------------------------


def test_manifest_lists_the_six_families_in_order():
    assert list(FAMILIES) == list(EXPECTED)
    assert MANIFEST['license'].startswith('MIT')


@pytest.mark.parametrize('key', EXPECTED)
def test_every_file_matches_its_manifest_entry(key):
    for entry in FAMILIES[key]['files'].values():
        file = BRAND / entry['path']
        assert file.stat().st_size == entry['bytes']
        assert hashlib.sha256(file.read_bytes()).hexdigest() == entry['sha256']


@pytest.mark.parametrize('key', EXPECTED)
def test_names_weight_and_metrics(key):
    family, style, weight = EXPECTED[key]
    data, found = read(key)
    n = names(data, found)
    assert n.get(16, n[1]) == family
    assert n[6] == f'{family}-{style}'.replace(' ', '')
    assert family in n[4]
    assert 'MIT' in n[13]
    head = found['head'][0]
    assert struct.unpack('>H', data[head + 18 : head + 20])[0] == 1000
    os2 = found['OS/2'][0]
    assert struct.unpack('>H', data[os2 + 4 : os2 + 6])[0] == weight
    assert (
        struct.unpack('>H', data[os2 + 8 : os2 + 10])[0] == 0
    )  # installable embedding, as the supplied fonts


def test_family_names_are_distinct_and_not_the_first_draft():
    families = [EXPECTED[key][0] for key in EXPECTED]
    assert len(set(families)) == 6
    # these first-draft names were already taken by other typefaces
    assert not {'Inkprint', 'Spire', 'Broadside', 'Carbon', 'X Marks', 'Margin Hand'} & set(families)


@pytest.mark.parametrize('key', EXPECTED)
def test_character_set(key):
    data, found = read(key)
    missing = [ch for ch in BASIC if ord(ch) not in cmap(data, found)]
    assert not missing, f'{key} lacks {missing}'
    assert FAMILIES[key]['characters'] >= 240


@pytest.mark.parametrize('key', EXPECTED)
def test_woff2_is_smaller_than_the_ttf(key):
    ttf, woff2 = FAMILIES[key]['files']['ttf'], FAMILIES[key]['files']['woff2']
    assert (BRAND / woff2['path']).read_bytes()[:4] == b'wOF2'
    assert woff2['bytes'] < ttf['bytes']


def test_features():
    for key in EXPECTED:
        _, found = read(key)
        has_kerning = 'GPOS' in found
        assert has_kerning == (key not in ('onionskin', 'decussate')), key
    assert 'GSUB' in read('scholium')[1]
    assert 'calt' in FAMILIES['scholium']['features']
    assert 'kern' in FAMILIES['irongall']['features']


# the design promises ------------------------------------------------------------------------------------


def ink(key, text):
    """The width and height of a string's ink at 1000 px, ignoring sidebearings."""
    font = ImageFont.truetype(str(path(key)), 1000)
    left, top, right, bottom = font.getmask(text).getbbox()
    return right - left, bottom - top


def test_tenuis_is_tall_and_thin():
    width, height = ink('tenuis', 'H')
    assert height / width > 2.4
    assert ink('tenuis', '|')[0] < 40  # a hairline stem
    assert ink('tenuis', 'o')[1] / ink('tenuis', 'o')[0] > 2.2
    # a line of it is about a quarter shorter than the same words in a regular-width face
    assert (
        ImageFont.truetype(str(path('tenuis')), 100).getlength('Hamburgefonstiv')
        < ImageFont.truetype(str(path('irongall')), 100).getlength('Hamburgefonstiv') * 0.8
    )


def test_brobdingnag_is_big_and_bold():
    assert ink('brobdingnag', '|')[0] >= 140  # a stem a seventh of the em wide
    assert ink('irongall', '|')[0] < ink('brobdingnag', '|')[0]
    assert ink('brobdingnag', '|')[0] > 3.5 * ink('tenuis', '|')[0]


def test_onionskin_is_monospaced():
    font = ImageFont.truetype(str(path('onionskin')), 1000)
    widths = {font.getlength(ch) for ch in 'iIlmwWM.,;@ aeo'}
    assert widths == {600.0}


def test_decussate_letters_are_made_of_stitches():
    # a stitched stem is a column of separate X marks, so its ink is far lighter than a solid stem's
    font = ImageFont.truetype(str(path('decussate')), 400)
    mask = font.getmask('l')
    solid = ImageFont.truetype(str(path('brobdingnag')), 400).getmask('l')
    ink_decussate = sum(1 for p in mask if p > 127)
    ink_solid = sum(1 for p in solid if p > 127)
    assert 0 < ink_decussate < ink_solid


@pytest.mark.parametrize('key', EXPECTED)
def test_every_font_renders_text(key):
    font = ImageFont.truetype(str(path(key)), 80)
    box = font.getbbox('Your words, your call.')
    assert box[2] - box[0] > 200 and box[3] - box[1] > 40
    assert font.getname()[0].startswith(EXPECTED[key][0])


# Vixl masters and specimens -----------------------------------------------------------------------------


@pytest.mark.parametrize('key', EXPECTED)
def test_glyph_masters_are_editable_vector_documents(key):
    entry = FAMILIES[key]['masters']
    with zipfile.ZipFile(BRAND / entry['path']) as archive:
        project = json.loads(archive.read('project.json'))
    state = project.get('state') or project
    layers = state['layers']
    glyphs = [layer for layer in layers if layer['name'].startswith('g-')]
    assert len(glyphs) == entry['glyph_layers'] >= 140
    assert all(
        layer['type'] == 'shape' and layer['shape'] == 'path' and layer['path'].startswith('M')
        for layer in glyphs
    )
    names = {layer['name'] for layer in glyphs}
    assert any(name.endswith('-A') for name in names) and any(name.endswith('-a') for name in names)
    assert any(layer['name'] == 'metrics' for layer in layers)
    assert entry['check_passed']


@pytest.mark.parametrize('key', EXPECTED)
def test_specimens_passed_their_vixl_checks(key):
    entry = FAMILIES[key]['specimen']
    assert entry['check']['passed'] and entry['check']['by_action']['fix'] == 0
    with Image.open(BRAND / entry['png']) as image:
        assert image.width == 1600 and image.height > 900
    assert (BRAND / entry['vixl']).stat().st_size > 10_000


def test_overview_sheet_exists():
    with Image.open(BRAND / 'overview.png') as image:
        assert image.width == 1600 and image.height > 1400
    assert zipfile.is_zipfile(BRAND / 'overview.vixl')


# the desktop toolkit can load them ----------------------------------------------------------------------


def test_qt_loads_every_font(qapp):
    from PySide6.QtGui import QFontDatabase

    for key, (family, _, _) in EXPECTED.items():
        font_id = QFontDatabase.addApplicationFont(str(path(key)))
        assert font_id >= 0, key
        assert any(name.startswith(family) for name in QFontDatabase.applicationFontFamilies(font_id)), key


# authoring toolchain (skipped where fontTools, shapely and numpy are not installed) ------------------------


@pytest.fixture(scope='module')
def brandfonts():
    pytest.importorskip('fontTools')
    pytest.importorskip('shapely')
    pytest.importorskip('numpy')
    sys.path.insert(0, str(ROOT / 'scripts'))
    try:
        import brandfonts as package

        yield package
    finally:
        sys.path.remove(str(ROOT / 'scripts'))


def test_nib_sweep_makes_the_stroke_the_nib_is_wide(brandfonts):
    from brandfonts import geom

    nib = geom.Nib.ellipse(20, 20)
    stem = geom.sweep(geom.Stroke(geom.line((50, 0), (50, 400)), clip=(0, 400)), nib)
    assert stem.bounds[0] == pytest.approx(30, abs=1)
    assert stem.bounds[2] == pytest.approx(70, abs=1)
    assert stem.bounds[1] == pytest.approx(0, abs=0.01) and stem.bounds[3] == pytest.approx(400, abs=0.01)


def test_outline_round_trips_through_svg_path_data(brandfonts):
    from brandfonts import geom, masters, outline

    ring = geom.arc(200, 200, 150, 120, 0, 360)
    shape = geom.sweep(geom.Stroke(ring), geom.Nib.ellipse(18, 18))
    contours = outline.quantize(outline.contours_from_geom(shape))
    assert len(contours) == 2  # the ring and its counter
    d = outline.svg_path(contours, masters.SCALE, -masters.SCALE, 0, 400 * masters.SCALE)
    layer = {'x': 0, 'y': 0, 'width': 400 * masters.SCALE, 'height': 400 * masters.SCALE}
    back = masters.geometry_from_path(d, layer, 0, 400 * masters.SCALE)
    assert back.area == pytest.approx(shape.area, rel=0.02)
    assert back.symmetric_difference(shape).area / shape.area < 0.03
