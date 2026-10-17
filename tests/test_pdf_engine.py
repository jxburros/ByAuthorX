"""The optional Docling PDF path: page and bounding-box provenance, offline, annotations as linked notes.

Docling itself is not installed in the test environment. A small fake ``docling`` package on the parser
subprocess's PYTHONPATH stands in for it, so the real subprocess, locator and chunk code is exercised;
the real library was checked by hand (see docs/library-and-citations-guide.md for the recorded run).
"""

import hashlib
import importlib.machinery
import json
import sys
import textwrap
import types
from pathlib import Path
from types import SimpleNamespace

import pytest
from pypdf import PdfWriter
from pypdf.annotations import Highlight, Text
from pypdf.generic import ArrayObject, FloatObject

from byax import cli, libraries, library_parser, local_models
from byax.errors import ByaxError
from byax.libraries import LibraryIndex, LibraryRegistry
from byax.transport_policy import check_provider_admin

FAKE_DOCLING = {
    '__init__.py': '',
    'datamodel/__init__.py': '',
    'datamodel/base_models.py': 'class InputFormat:\n    PDF = "pdf"\n',
    'datamodel/pipeline_options.py': textwrap.dedent("""
        import json, os

        class PdfPipelineOptions:
            def __init__(self, **options):
                folder = options['artifacts_path']
                # Record what the engine was given: an installed folder, OCR and tables off, offline.
                record = {'artifacts_path': folder, 'exists': os.path.isdir(folder),
                          'do_ocr': options['do_ocr'], 'do_table_structure': options['do_table_structure'],
                          'offline': os.environ.get('HF_HUB_OFFLINE')}
                open(os.environ['FAKE_DOCLING_LOG'], 'w').write(json.dumps(record))
                self.options = options
        """),
    'document_converter.py': textwrap.dedent("""
        import json, os
        from types import SimpleNamespace as NS

        class PdfFormatOption:
            def __init__(self, pipeline_options):
                self.pipeline_options = pipeline_options

        class Box:
            def __init__(self, l, t, r, b):
                self.l, self.t, self.r, self.b = l, t, r, b

            def to_top_left_origin(self, page_height):
                return Box(self.l, page_height - self.t, self.r, page_height - self.b)

        class Document:
            def __init__(self, items):
                self.items = items
                self.pages = {n: NS(size=NS(height=792.0)) for n in (1, 2)}

            def iterate_items(self):
                for spec in self.items:
                    prov = [NS(page_no=spec['page'], bbox=Box(*spec['bbox']))] if 'page' in spec else []
                    item = NS(label=NS(value=spec['label']), text=spec.get('text'), level=spec.get('level', 1), prov=prov)
                    yield item, 0

        class DocumentConverter:
            def __init__(self, format_options):
                self.options = format_options

            def convert(self, path):
                items = json.load(open(os.environ['FAKE_DOCLING_ITEMS']))
                return NS(document=Document(items))
        """),
}

ITEMS = [
    {'label': 'page_header', 'text': 'RUNNING HEAD', 'page': 1, 'bbox': [72, 780, 300, 770]},
    {'label': 'section_header', 'text': 'The Lighthouse', 'level': 1, 'page': 1, 'bbox': [72, 720, 200, 700]},
    {'label': 'section_header', 'text': 'Chapter One', 'level': 2, 'page': 1, 'bbox': [72, 690, 180, 675]},
    {'label': 'text', 'text': 'The keeper climbed the stair.', 'page': 1, 'bbox': [72, 660, 400, 640]},
    {'label': 'text', 'text': 'He lit the great lamp.', 'page': 1, 'bbox': [72, 630, 380, 610]},
    {'label': 'page_footer', 'text': 'page 1', 'page': 1, 'bbox': [300, 40, 320, 30]},
    {'label': 'section_header', 'text': 'Chapter Two', 'level': 2, 'page': 2, 'bbox': [72, 720, 180, 705]},
    {'label': 'text', 'text': 'A storm arrived in October.', 'page': 2, 'bbox': [72, 690, 420, 670]},
]


def fake_docling(directory):
    for name, body in FAKE_DOCLING.items():
        path = directory / 'docling' / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding='utf-8')
    return directory


def tiny_layout_catalogue(directory):
    body = b'layout-weights'
    entry = {
        'name': 'docling-layout-heron',
        'title': 'Layout',
        'kind': 'layout',
        'version': '1',
        'licence': {'spdx': 'Apache-2.0'},
        'files': [
            {
                'path': 'docling-project--docling-layout-heron/model.safetensors',
                'size': len(body),
                'sha256': hashlib.sha256(body).hexdigest(),
                'url': 'https://example.invalid/model',
                'mirror_asset': 'docling-layout-heron__model.safetensors',
            }
        ],
    }
    path = directory / 'catalogue.json'
    path.write_text(
        json.dumps(
            {
                'schema': 1,
                'mirror': {'base_url': 'https://example.invalid', 'populated': False},
                'models': [entry],
            }
        ),
        encoding='utf-8',
    )
    return path, entry, body


def make_pdf(path, *, annotations=True):
    writer = PdfWriter()
    writer.add_blank_page(612, 792)
    writer.add_blank_page(612, 792)
    if annotations:
        writer.add_annotation(0, Text(rect=(50, 700, 150, 750), text='Check this date against the log.'))
        writer.add_annotation(
            1,
            Highlight(
                rect=(50, 400, 200, 420),
                quad_points=ArrayObject(FloatObject(v) for v in (50, 420, 200, 420, 50, 400, 200, 400)),
            ),
        )
    with path.open('wb') as handle:
        writer.write(handle)
    return path


def fake_document(items=ITEMS):
    """A duck-typed stand-in for a Docling document (the parser reads only these attributes)."""

    def make_item(spec):
        prov = []
        if 'page' in spec:
            left, top, right, bottom = spec['bbox']
            box = SimpleNamespace(
                l=left,
                t=top,
                r=right,
                b=bottom,
                to_top_left_origin=lambda page_height, v=(left, top, right, bottom): SimpleNamespace(
                    l=v[0], t=page_height - v[1], r=v[2], b=page_height - v[3]
                ),
            )
            prov = [SimpleNamespace(page_no=spec['page'], bbox=box)]
        return SimpleNamespace(
            label=SimpleNamespace(value=spec['label']),
            text=spec.get('text'),
            level=spec.get('level', 1),
            prov=prov,
        )

    return SimpleNamespace(
        iterate_items=lambda: ((make_item(s), 0) for s in items),
        pages={n: SimpleNamespace(size=SimpleNamespace(height=792.0)) for n in (1, 2)},
    )


# Extraction pieces ---------------------------------------------------------------------------------


def test_annotations_become_linked_notes_with_a_top_left_bounding_box(tmp_path):
    notes = library_parser.pdf_annotations(make_pdf(tmp_path / 'a.pdf'))
    assert len(notes) == 1, 'an annotation without text is not a note'
    (note,) = notes
    assert note['text'] == 'Check this date against the log.'
    assert note['locator'] == {
        'page': 1,
        'ordinal': 0,
        'heading_path': [],
        'note': 'Text',
        'bbox': [50.0, 42.0, 150.0, 92.0],
    }
    assert library_parser.pdf_annotations(make_pdf(tmp_path / 'b.pdf', annotations=False)) == []


def test_docling_items_become_passages_with_page_bbox_and_heading_path():
    passages = library_parser.docling_passages(fake_document(), maximum=10_000)
    assert [p['text'] for p in passages] == [
        'The Lighthouse',
        'Chapter One',
        'The keeper climbed the stair.',
        'He lit the great lamp.',
        'Chapter Two',
        'A storm arrived in October.',
    ], 'page furniture is dropped'
    keeper = passages[2]['locator']
    assert keeper['heading_path'] == ['The Lighthouse', 'Chapter One'] and keeper['page'] == 1
    assert keeper['bbox'] == [72.0, 132.0, 400.0, 152.0]  # converted to a top-left origin
    assert passages[-1]['locator']['heading_path'] == ['The Lighthouse', 'Chapter Two']
    assert [p['locator']['ordinal'] for p in passages] == list(range(6))
    with pytest.raises(ValueError, match='Maximum extracted text'):
        library_parser.docling_passages(fake_document(), maximum=20)


def test_pypdf_stays_the_default_and_pymupdf_is_not_used():
    source = Path(library_parser.__file__).read_text(encoding='utf-8').lower()
    assert "engine='pypdf'" in source and 'import fitz' not in source and 'import pymupdf' not in source
    assert libraries.PDF_ENGINES == ('pypdf', 'docling')
    command = libraries._parser_command('s', 10, 'r')
    assert command[-3:] == ['s', '10', 'r'], 'the default parser contract is unchanged'
    assert libraries._parser_command('s', 10, 'r', 'docling', 900)[-2:] == ['docling', '900']


# The subprocess end to end -------------------------------------------------------------------------


@pytest.fixture
def engine(tmp_path, monkeypatch):
    fakes = fake_docling(tmp_path / 'fakes')
    catalogue_path, entry, body = tiny_layout_catalogue(tmp_path)
    # The parser subprocess imports byax from the environment and Docling from this fake package.
    monkeypatch.setenv('PYTHONPATH', str(fakes))
    monkeypatch.setenv('BYAX_MODELS_CATALOGUE', str(catalogue_path))
    monkeypatch.setenv('BYAX_MODELS_DIR', str(tmp_path / 'models'))
    monkeypatch.setenv('XDG_CONFIG_HOME', str(tmp_path / 'xdg'))
    monkeypatch.setenv('FAKE_DOCLING_LOG', str(tmp_path / 'docling-log.json'))
    items = tmp_path / 'items.json'
    items.write_text(json.dumps(ITEMS), encoding='utf-8')
    monkeypatch.setenv('FAKE_DOCLING_ITEMS', str(items))
    root = tmp_path / 'sources'
    root.mkdir()
    make_pdf(root / 'book.pdf')
    registry = LibraryRegistry(tmp_path / 'config')
    definition = registry.create('Books', [str(root)])
    return SimpleNamespace(
        root=root,
        registry=registry,
        definition=definition,
        cache=tmp_path / 'cache',
        tmp=tmp_path,
        entry=entry,
        body=body,
    )


def install_layout_model(engine):
    target = engine.tmp / 'models' / 'docling-layout-heron'
    file = target / engine.entry['files'][0]['path']
    file.parent.mkdir(parents=True)
    file.write_bytes(engine.body)
    local_models._write_manifest(local_models.get_model('docling-layout-heron'), target, 'upstream')


def test_docling_engine_indexes_page_bbox_headings_and_annotations_offline(engine):
    install_layout_model(engine)
    library = engine.registry.configure_pdf_engine(engine.definition['library_id'], 'docling')
    index = LibraryIndex(library, cache_dir=engine.cache)
    assert index.chunking['pdf_engine'] == 'docling' and index.status()['chunking']['pdf_engine'] == 'docling'
    result = index.refresh()
    assert result['files'][0]['status'] == 'ready', result['files'][0]
    record = json.loads((engine.tmp / 'docling-log.json').read_text())
    assert record['exists'] and record['do_ocr'] is False and record['do_table_structure'] is False
    assert record['offline'] == '1', 'the parser subprocess runs Docling offline'
    keeper = index.search('stair')['results'][0]
    # Two paragraphs of one section on one page merge; the chunk keeps the page and the box covering both.
    assert 'He lit the great lamp.' in keeper['text']
    assert keeper['locator']['page'] == 1 and keeper['locator']['heading_path'] == [
        'The Lighthouse',
        'Chapter One',
    ]
    # The section heading, both paragraphs: one chunk, and the box covers all three.
    assert keeper['text'].startswith('Chapter One') and keeper['locator']['bbox'] == [
        72.0,
        102.0,
        400.0,
        182.0,
    ]
    assert keeper['heading'] == 'The Lighthouse > Chapter One'
    storm = index.search('storm')['results'][0]
    assert storm['locator']['page'] == 2 and storm['locator']['bbox'][0] == 72.0
    note = index.search('Check this date')['results'][0]
    assert note['text'] == 'Check this date against the log.'
    assert note['locator']['note'] == 'Text' and note['locator']['page'] == 1
    assert note['locator']['bbox'] == [50.0, 42.0, 150.0, 92.0]
    assert note['chunk_id'] != keeper['chunk_id'], 'a linked note is never merged into body text'
    # The chunk's locator survives citation validation, including the box.
    from byax import engine as document_engine
    from byax.citations import source_from_hit
    from byax.model import new_document

    doc = new_document(text='The keeper climbed.')
    source = source_from_hit({**keeper, 'library_name': 'Books'})
    doc['references'][source['id']] = source
    locator = {**keeper['locator'], 'chunk': keeper['chunk_id']}
    op = {
        'type': 'cite',
        'target': doc['blocks'][0]['id'],
        'start': 0,
        'end': 10,
        'source': source['id'],
        'locator': locator,
    }
    updated, _ = document_engine.apply(doc, {'expected_revision': doc['revision'], 'operations': [op]})
    assert updated['blocks'][0]['marks'][0]['locator']['bbox'] == keeper['locator']['bbox']


def test_switching_engine_marks_pdfs_stale_and_vectors_incompatible(engine):
    install_layout_model(engine)
    default = LibraryIndex(engine.definition, cache_dir=engine.cache)
    default.refresh()
    assert 'pdf_engine' not in default.chunking and not default.status()['stale']
    library = engine.registry.configure_pdf_engine(engine.definition['library_id'], 'docling')
    switched = LibraryIndex(library, cache_dir=engine.cache)
    assert switched.status()['stale']
    from byax.embeddings import fingerprint

    class Embedder:
        model_identity, dimension = 'x', 2

    assert fingerprint(Embedder, default.chunking) != fingerprint(Embedder, switched.chunking)
    switched.refresh()
    assert not switched.status()['stale']
    back = engine.registry.configure_pdf_engine(engine.definition['library_id'], 'pypdf')
    assert 'pdf_engine' not in back or back['pdf_engine'] is None
    assert LibraryIndex(back, cache_dir=engine.cache).status()['stale']
    with pytest.raises(ByaxError):
        engine.registry.configure_pdf_engine(engine.definition['library_id'], 'pymupdf')
    with pytest.raises(ByaxError):
        LibraryIndex({**library, 'pdf_engine': 'pymupdf'}, cache_dir=engine.cache)


def test_missing_layout_model_or_docling_is_reported_per_file_and_never_downloads(engine, monkeypatch):
    library = engine.registry.configure_pdf_engine(engine.definition['library_id'], 'docling')
    index = LibraryIndex(library, cache_dir=engine.cache)
    status = index.refresh()['files'][0]
    assert (
        status['status'] == 'error'
        and 'byax models --action download --name docling-layout-heron' in status['error']
    )
    install_layout_model(engine)
    monkeypatch.delenv('PYTHONPATH')
    index.refresh(rebuild=True)
    again = index.status()['files'][0]
    assert again['status'] == 'error' and 'byauthorx[docling]' in again['error']


def test_pdf_engine_command_checks_prerequisites_and_is_admin_only(engine, monkeypatch):
    from byax.model import new_document
    from byax.storage import save_document

    monkeypatch.setattr(libraries, '_default_dir', lambda: engine.tmp / 'libconfig')
    monkeypatch.setattr('platformdirs.user_cache_path', lambda *a, **k: engine.cache)
    monkeypatch.setattr('platformdirs.user_state_path', lambda *a, **k: engine.tmp / 'state')
    document = engine.tmp / 'doc.byax'
    save_document(new_document(), document)
    cli.execute(
        'library',
        {'path': str(document), 'action': 'create', 'input': {'name': 'B', 'roots': [str(engine.root)]}},
    )
    identity = LibraryRegistry().list()[0]['library_id']
    base = {'path': str(document), 'library_id': identity, 'action': 'pdf-engine'}
    with pytest.raises(ByaxError) as caught:
        cli.execute('library', {**base, 'input': {'engine': 'docling'}})
    assert caught.value.code == 'missing_dependency' and 'byauthorx[docling]' in caught.value.message
    stand_in = types.ModuleType('docling')
    stand_in.__spec__ = importlib.machinery.ModuleSpec('docling', None)
    monkeypatch.setitem(sys.modules, 'docling', stand_in)
    with pytest.raises(ByaxError) as caught:
        cli.execute('library', {**base, 'input': {'engine': 'docling'}})
    assert caught.value.code == 'model_missing' and 'byax models --action download' in caught.value.message
    assert LibraryRegistry().get(identity).get('pdf_engine') is None
    install_layout_model(engine)
    done = cli.execute('library', {**base, 'input': {'engine': 'docling'}})
    assert done['pdf_engine'] == 'docling' and LibraryRegistry().get(identity)['pdf_engine'] == 'docling'
    assert cli.execute('library', {**base, 'input': {'engine': 'pypdf'}})['library']['pdf_engine'] is None
    with pytest.raises(ByaxError):
        cli.execute('library', {**base, 'input': {'engine': 'pymupdf'}})
    with pytest.raises(ByaxError) as admin:
        check_provider_admin('library', {'action': 'pdf-engine'})
    assert admin.value.code == 'unsupported_feature'


def test_docling_layout_model_is_in_the_catalogue_with_its_licence():
    entry = local_models.get_model('docling-layout-heron')
    assert entry['kind'] == 'layout' and entry['licence']['spdx'] == 'Apache-2.0' and not entry['bundled']
    assert {f['path'].split('/')[0] for f in entry['files'] if f['role'] != 'card'} == {
        'docling-project--docling-layout-heron'
    }
