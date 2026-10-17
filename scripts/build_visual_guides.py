"""Flowcharts, guides and a booklet that explain ByAuthorX, authored and checked in Vixl.

Every sheet is a Vixl document (`docs/visual-guides/vixl/*.vixl`) with exports beside it. The booklet is
one multi-page Vixl document exported as a PDF. Colours, type roles and artwork come from the brand
system in docs/brand.md: paper, ink and pen green, Irongall for text, Scholium for helper lines and
Onionskin for counters. The wordmark, icons and illustrations are the approved files, never redrawn.

    python -m venv .venv-assets
    .venv-assets/bin/python -m pip install -r assets/brand-fonts/requirements.txt
    .venv-assets/bin/python scripts/build_visual_guides.py [--only SHEET] [--no-booklet]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from PIL import Image
from vixl import Project
from vixl.fonts import import_font

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'docs' / 'visual-guides'
FONTS = ROOT / 'assets' / 'brand-fonts' / 'fonts'
UI = ROOT / 'assets' / 'logo-ui-library'
LOGO = ROOT / 'design' / 'brand' / 'kit' / 'logos' / 'primary.png'

PAPER, INK, GREEN, RED = '#F4EEE3', '#252927', '#367B6C', '#AD283E'
SURFACE, RULE, MUTED, DEEP = '#FFFCF7', '#D7D0C3', '#52615B', '#246052'
W, H = 1600, 1000
MARGIN = 80
FACES = {
    'irongall': 'irongall/Irongall-Regular.ttf',
    'scholium': 'scholium/Scholium-Regular.ttf',
    'onionskin': 'onionskin/Onionskin-Regular.ttf',
}


def text(name, content, size, x, y, w, h, font='irongall', color=INK, align='left', line_height=1.25, **extra):
    return [
        dict(type='text', name=name, text=content, font=font, size=size, color=color, x=x, y=y, align=align,
             line_height=line_height, **extra),
        dict(type='text-layout', target=name, width=w, height=h, fit=False),
    ]


def box(name, x, y, w, h, fill=SURFACE, stroke=RULE, radius=8, stroke_width=2):
    return [dict(type='shape', name=name, shape='rounded-rectangle', x=x, y=y, width=w, height=h, fill=fill,
                 stroke=stroke, stroke_width=stroke_width, radius=radius)]


def rule(name, x, y, w, color=RULE, h=2):
    return [dict(type='shape', name=name, shape='rectangle', x=x, y=y, width=w, height=h, fill=color)]


def image(name, path, x, y, w):
    with Image.open(path) as picture:
        ratio = picture.height / picture.width
    return [dict(type='add', name=name, path=str(path), x=x, y=y, width=w, height=round(w * ratio, 2))]


def icon(name, key, x, y, size=64):
    return image(f'icon-{name}', UI / 'icons' / f'{key}-light.png', x, y, size)


def art(name, key, x, y, w):
    return image(f'art-{name}', UI / 'illustrations' / f'{key}-light.png', x, y, w)


def badge(name, number, x, y, d=44):
    cx = d / 2
    return [
        dict(type='shape', name=f'{name}-disc', shape='ellipse', x=x, y=y, width=d, height=d, fill=GREEN),
        *text(f'{name}-n', str(number), d * 0.55, x, y + d * 0.2, d, d, color='#FFFFFF', align='center', line_height=1.0),
    ]


def chrome(sheet, page, total):
    """Wordmark, kicker, headline with the pen line and ink dot, and the footer."""
    ops = [dict(type='accessibility', lang='en', title=f"ByAuthorX: {sheet['title']}")]
    ops += image('wordmark', LOGO, MARGIN, 44, 240)
    ops += text('kicker', sheet['kicker'].upper(), 17, W - MARGIN - 700, 72, 700, 30, font='onionskin', color=MUTED,
                align='right', tracking=1.5)
    ops += text('headline', sheet['title'], 56, MARGIN, 136, W - 2 * MARGIN, 76, line_height=1.0)
    ops += [
        dict(type='shape', name='ink-dot', shape='ellipse', x=MARGIN, y=226, width=16, height=16, fill=RED),
        dict(type='shape', name='pen-line', shape='rectangle', x=MARGIN + 8, y=231, width=330, height=4, fill=GREEN,
             radius=2),
    ]
    ops += text('lede', sheet['lede'], 24, MARGIN, 252, W - 2 * MARGIN, 36, font='scholium', color=INK)
    ops += rule('footer-rule', MARGIN, 934, W - 2 * MARGIN)
    ops += text('footer', 'Your words. Your call.', 20, MARGIN, 952, 600, 28, color=MUTED)
    ops += text('footer-ref', f"{sheet['ref']}   {page} / {total}", 17, W - MARGIN - 700, 954, 700, 28,
                font='onionskin', color=MUTED, align='right')
    return ops


def node(nid, label, kind=None, key=False):
    spec = dict(id=nid, label=label)
    if kind:
        spec['kind'] = kind
    if key:
        spec.update(color=GREEN, text_color='#FFFFFF')
    return spec


def diagram(name, nodes, edges, y, h, x=MARGIN, w=W - 2 * MARGIN, direction='LR', size=22, **extra):
    return [dict(type='diagram', name=name, layout='layered', direction=direction, theme='light', font='irongall',
                 size=size, node_color=SURFACE, edge_color=INK, text_color=INK, x=x, y=y, width=w, height=h,
                 nodes=nodes, edges=edges, **extra)]


# --------------------------------------------------------------------------- flowcharts

def proposals():
    sources = [
        ('s1', 'Spelling and grammar checks'),
        ('s2', 'Fiction and document checks'),
        ('s3', "An editor's changes from Word"),
        ('s4', "A beta reader's suggestion"),
        ('s5', 'The assistant in Work mode'),
        ('s6', 'A saved draft or synced copy'),
    ]
    nodes = [node(i, t) for i, t in sources]
    nodes += [
        node('p', 'Proposed changes', key=True),
        node('d', 'You decide, one by one', 'decision'),
        node('yes', 'Your text changes. One revision, which you can undo.'),
        node('no', 'Your text stays exactly as it is.'),
        node('rep', 'Reply or leave it pending for later.'),
    ]
    edges = [[i, 'p'] for i, _ in sources]
    edges += [['p', 'd'], ['d', 'yes', 'accept'], ['d', 'no', 'reject'], ['d', 'rep', 'not yet']]
    return dict(
        key='proposals', ref='Flowchart 1', kicker='Flowchart', title='Nothing changes your text until you accept it',
        lede='Everything that wants to change your writing arrives as a proposal first.',
        ops=diagram('flow', nodes, edges, 310, 570, size=21),
        alt='Six sources of suggestions flow into Proposed changes. You accept, reject or postpone each one.',
    )


def share():
    chains = {
        'a': ('editing', ['An editor who uses Word', 'File → Send for review (Word)…', 'File → Merge reviewed copy…',
                          "The editor's changes arrive as proposals"]),
        'b': ('feedback', ['Readers who read and comment', 'File → Send to beta readers…', 'File → Import reader comments…',
                           'Comments and proposals arrive, marked with each name']),
        'c': ('publishing', ['A printer, shop or store', 'File → Compile…', 'Choose a preset and run preflight',
                             'A print PDF, EPUB or Word file']),
        'd': ('a copy', ['Someone who needs a copy', 'File → Export', 'PDF, Word, EPUB or Markdown',
                         'Read the note on what it could not carry']),
    }
    nodes = [node('start', 'I want to share my work', 'terminator', key=True), node('who', 'Who is it for?', 'decision')]
    edges = [['start', 'who']]
    for p, (label, labels) in chains.items():
        for i, t in enumerate(labels, 1):
            nodes.append(node(f'{p}{i}', t, key=(i == len(labels))))
        edges.append(['who', f'{p}1', label])
        edges += [[f'{p}{i}', f'{p}{i + 1}'] for i in range(1, len(labels))]
    return dict(
        key='share', ref='Flowchart 2', kicker='Flowchart', title='How do I share this?',
        lede='Your master never leaves your computer unless you send it. Every route makes a copy.',
        ops=diagram('flow', nodes, edges, 300, 610, size=20),
        alt='A decision tree from "Who is it for?" to Word review, beta readers, compile for publishing or a plain export.',
    )


def book():
    plan = [
        node('p1', 'Binder → Create binder', key=True),
        node('p2', 'Write. Move scenes in the outline or corkboard.'),
        node('p3', 'Tools → Writing goals…'),
        node('p4', 'Review → Versions and drafts → Save draft as…'),
        node('p5', 'Rework in a new draft?', 'decision'),
        node('p6', 'Compare drafts… then Bring changes from draft…'),
    ]
    plan_edges = [['p1', 'p2'], ['p2', 'p3'], ['p3', 'p4'], ['p4', 'p5'], ['p5', 'p6', 'yes'], ['p6', 'p2']]
    comp = [
        node('c1', 'File → Compile…', key=True),
        node('c2', 'Pick a preset: Novel, Non-fiction or Manuscript'),
        node('c3', 'Choose trim size and what goes in'),
        node('c4', 'Run preflight'),
        node('c5', 'Anything to fix?', 'decision'),
        node('c6', 'Compile… and choose where to save', key=True),
    ]
    comp_edges = [['c1', 'c2'], ['c2', 'c3'], ['c3', 'c4'], ['c4', 'c5'], ['c5', 'c6', 'no'], ['c5', 'c2', 'yes, fix and rerun']]
    ops = text('plan-label', 'PLAN AND WRITE', 17, MARGIN, 304, 400, 28, font='onionskin', color=DEEP, tracking=1.5)
    ops += diagram('plan', plan, plan_edges, 336, 250, size=17)
    ops += text('compile-label', 'COMPILE', 17, MARGIN, 614, 400, 28, font='onionskin', color=DEEP, tracking=1.5)
    ops += diagram('compile', comp, comp_edges, 646, 250, size=17)
    return dict(
        key='manuscript-to-book', ref='Flowchart 3', kicker='Flowchart', title='From manuscript to book',
        lede='Your master is never changed by a compile. Each format tells you what it could not carry.',
        ops=ops, alt='Two flows: planning and writing with drafts, then compiling a book with preflight.',
    )


def labelled(label, y):
    return text(f'{label.lower().replace(" ", "-")}-label', label, 17, MARGIN, y, 600, 28, font='onionskin', color=DEEP,
                tracking=1.5)


def ai():
    setup = [
        node('q', 'Do you want AI help?', 'decision'),
        node('no', 'Write, save, print and check locally. Nothing is sent.'),
        node('prov', 'Tools → Provider settings'),
        node('where', 'Where does the model run?', 'decision'),
        node('host', 'Hosted, with your own key in the OS keyring'),
        node('loc', 'On your computer: Ollama, whisper.cpp or a local server'),
    ]
    setup_edges = [['q', 'no', 'no'], ['q', 'prov', 'yes'], ['prov', 'where'], ['where', 'host', 'hosted'],
                   ['where', 'loc', 'local']]
    use = [
        node('mode', 'Chat or Work?', 'decision'),
        node('chat', 'Chat answers. It never touches your document.'),
        node('work', 'Work suggests exact edits'),
        node('pay', 'A hosted provider: you see the exact outbound text and authorize it'),
        node('prop', 'Edits appear in Proposed changes. You accept each one.', key=True),
    ]
    use_edges = [['mode', 'chat', 'chat'], ['mode', 'work', 'work'], ['work', 'pay'], ['pay', 'prop']]
    ops = labelled('SET UP', 296) + diagram('setup', setup, setup_edges, 322, 280, size=20)
    ops += labelled('USE IT', 622) + diagram('use', use, use_edges, 648, 270, size=20)
    return dict(
        key='ai-opt-in', ref='Flowchart 4', kicker='Flowchart', title='AI is opt-in, not opt-out',
        lede='No account, no telemetry, no background network traffic. You choose the provider and the moment.',
        ops=ops,
        alt='Two flows: deciding whether to use AI and choosing a provider, then Chat or Work ending in proposals you accept.',
    )


def conflict():
    refused = [
        node('s', 'Save says the file changed on disk', 'terminator', key=True),
        node('n', 'Nothing was overwritten'),
        node('sa', 'Save as, to keep both versions'),
        node('r', 'Tools → Review conflicted copies…'),
    ]
    review = [
        node('q', 'Same section edited on both?', 'decision'),
        node('ok', 'Different sections merge with nothing lost'),
        node('cf', 'Review → Versions and drafts → Resolve selected merge conflicts…'),
        node('acc', 'Accept the merge and save', key=True),
        node('mv', 'Optionally move the copy to a conflicted copies folder'),
    ]
    review_edges = [['q', 'ok', 'no'], ['q', 'cf', 'yes'], ['ok', 'acc'], ['cf', 'acc'], ['acc', 'mv']]
    ops = labelled('WHEN A SAVE IS REFUSED', 296) + diagram('refused', refused, [['s', 'n'], ['n', 'sa'], ['sa', 'r']], 322, 190, size=20)
    ops += labelled('REVIEW THE OTHER COPY', 536) + diagram('review', review, review_edges, 562, 340, size=20)
    return dict(
        key='synced-folders', ref='Flowchart 5', kicker='Flowchart', title='Two computers, one manuscript',
        lede='Work on one computer at a time. If both edited, you review the merge. Nothing is deleted.',
        ops=ops,
        alt='What to do when a save is refused in a synced folder: save as, review the conflicted copy, resolve and accept.',
    )


# --------------------------------------------------------------------------- guides

def window():
    ops = []
    sx, sy, sw, sh = MARGIN, 320, 900, 560
    ops += box('frame', sx, sy, sw, sh, fill=SURFACE, stroke=INK, radius=8, stroke_width=3)
    ops += box('ribbon', sx + 12, sy + 12, sw - 24, 70, fill=PAPER)
    ops += image('ribbon-logo', LOGO, sx + 24, sy + 24, 120)
    for i, key in enumerate(['save', 'undo', 'find', 'checks', 'comment', 'export']):
        ops += icon(f'ribbon-{key}', key, sx + 170 + i * 52, sy + 28, 40)
    ops += box('outline', sx + 12, sy + 94, 190, sh - 140, fill=PAPER)
    for i in range(6):
        ops += rule(f'outline-row-{i}', sx + 28, sy + 140 + i * 34, 120 - (i % 3) * 18, color=MUTED, h=3)
    ops += text('outline-title', 'Outline', 18, sx + 24, sy + 104, 160, 26, color=DEEP)
    ops += box('page', sx + 214, sy + 94, 470, sh - 140, fill='#FFFFFF')
    for i in range(11):
        ops += rule(f'page-line-{i}', sx + 250, sy + 140 + i * 30, 400 - (i % 4) * 40, color=RULE, h=3)
    ops += dict(type='shape', name='page-mark', shape='rectangle', x=sx + 300, y=sy + 202, width=120, height=4,
                fill=RED, radius=2),
    ops += box('inspector', sx + 696, sy + 94, 192, sh - 140, fill=PAPER)
    for i in range(4):
        ops += rule(f'tab-{i}', sx + 706 + i * 44, sy + 118, 36, color=GREEN if i == 0 else MUTED, h=4 if i == 0 else 3)
    for i in range(4):
        ops += box(f'card-{i}', sx + 706, sy + 150 + i * 70, 172, 56, fill=SURFACE)
    ops += box('status', sx + 12, sy + sh - 40, sw - 24, 30, fill=PAPER)
    ops += text('status-text', 'Saved locally        1,240 words', 15, sx + 24, sy + sh - 36, 400, 22,
                font='onionskin', color=MUTED)
    marks = [(1, sx + 600, sy + 27, 40), (2, sx + 148, sy + 98, 40), (3, sx + 600, sy + 104, 40),
             (4, sx + 770, sy + 420, 40), (5, sx + 820, sy + 157, 40), (6, sx + 840, sy + sh - 39, 28)]
    for n, mx, my, d in marks:
        ops += badge(f'mark-{n}', n, mx, my, d)
    items = [
        ('Ribbon', 'Two rows of tools. Each file view shows only what its format supports.'),
        ('Side panel', 'Choose Outline, Binder, Layout, Bookmarks, Statistics, Scratchpad or Library.'),
        ('Writing view', 'Continuous editing. Switch to Pages for the layout print and PDF use.'),
        ('Inspector', 'Proposed changes, Checks, AI and Configure.'),
        ('Proposed changes', 'Every suggestion waits here. Run all active checks from this tab.'),
        ('Status bar', 'Save state, counters and the limits of the current file format.'),
    ]
    ix, iy = 1030, 316
    for n, (head, body) in enumerate(items, 1):
        y = iy + (n - 1) * 94
        ops += badge(f'key-{n}', n, ix, y + 2, 36)
        ops += text(f'key-head-{n}', head, 22, ix + 52, y, 440, 28, color=INK)
        ops += text(f'key-body-{n}', body, 17, ix + 52, y + 30, 440, 56, color=MUTED, line_height=1.2)
    return dict(
        key='the-window', ref='Guide 1', kicker='Guide', title='Find your way around the window',
        lede='A schematic of the editor. The writing page stays white inside warm paper margins.',
        ops=ops, alt='A schematic of the ByAuthorX window with six numbered parts explained beside it.',
    )


def first_minutes():
    steps = [
        ('pages', 'Take the tour', 'Six short steps. Reopen any time with Help → Take the tour…'),
        ('open-document', 'Open the sample project', 'Help → Open sample project makes a copy of The Lamp at Harrow Point to practise on.'),
        ('save', 'Write, then save', 'A .byax file keeps your text, images, review notes and history together.'),
        ('checks', 'Run the local checks', 'In Proposed changes, choose Run all active checks. Nothing leaves your computer.'),
        ('comment', 'Accept or reject', 'Each proposal waits for you. Keep what works.'),
        ('export', 'Export a copy', 'File → Export. The master is never replaced, and you see what a format left out.'),
    ]
    ops = []
    cw, ch, gap = 460, 250, 30
    for i, (key, head, body) in enumerate(steps):
        col, row = i % 3, i // 3
        x, y = MARGIN + col * (cw + gap), 322 + row * (ch + 24)
        ops += box(f'card-{i}', x, y, cw, ch)
        ops += badge(f'step-{i}', i + 1, x + 24, y + 24, 44)
        ops += icon(f'step-{i}', key, x + cw - 24 - 72, y + 18, 72)
        ops += text(f'step-head-{i}', head, 30, x + 24, y + 92, cw - 48, 40)
        ops += text(f'step-body-{i}', body, 23, x + 24, y + 140, cw - 48, 100, color=MUTED, line_height=1.3)
    ops += box('install', MARGIN, 868, W - 2 * MARGIN, 52, fill=PAPER, stroke=RULE)
    ops += text('install-text',
                'Install: Windows 11 x64 setup, Ubuntu 24.04 x64 .deb, or macOS 14+ .dmg for Apple silicon. Installers may be unsigned; see troubleshooting.',
                18, MARGIN + 20, 882, W - 2 * MARGIN - 40, 28, color=MUTED)
    return dict(
        key='first-minutes', ref='Guide 2', kicker='Guide', title='Your first minutes',
        lede='Six steps from a fresh install to a first export, all on your own computer.',
        ops=ops, alt='Six numbered steps: tour, sample project, write and save, run checks, accept or reject, export.',
    )


def checks():
    rows = [
        ('On this computer', 'Spelling, grammar, document health.', 'Active by default', 'Stays on your computer'),
        ('Fiction analysis', 'Dialogue tags, filter words, repeated openings, rhythm, sensory balance, invented-name spelling.',
         'Inactive by default', 'Stays on your computer. Advisory.'),
        ('Extension checks', 'Added by extensions you install and enable under Tools → Extensions…', 'Inactive by default',
         'Depends on the extension'),
        ('With an AI provider', 'Information repository, factual accuracy, voice consistency and checks you write.',
         'Inactive by default', 'Asks for your authorization each time'),
    ]
    ops = []
    ops += text('h-what', 'CHECK', 16, MARGIN + 24, 318, 300, 24, font='onionskin', color=MUTED, tracking=1.5)
    ops += text('h-state', 'STARTS', 16, 800, 318, 200, 24, font='onionskin', color=MUTED, tracking=1.5)
    ops += text('h-where', 'WHERE IT RUNS', 16, 1010, 318, 300, 24, font='onionskin', color=MUTED, tracking=1.5)
    for i, (head, body, state, where) in enumerate(rows):
        y = 350 + i * 118
        ops += box(f'row-{i}', MARGIN, y, 1180, 104)
        ops += text(f'row-head-{i}', head, 26, MARGIN + 24, y + 14, 330, 36)
        ops += text(f'row-body-{i}', body, 18, MARGIN + 24, y + 52, 700, 44, color=MUTED, line_height=1.2)
        ops += text(f'row-state-{i}', state, 20, 800, y + 18, 190, 60, color=DEEP if i == 0 else INK)
        ops += text(f'row-where-{i}', where, 20, 1010, y + 18, 230, 70, color=INK, line_height=1.2)
    ops += art('marks', 'proofread-marks', 1290, 340, 260)
    ops += text('marks-note', 'Red pen marks spelling and green pen marks grammar. They are interface only and never appear in print or exports.',
                17, 1290, 540, 230, 150, font='scholium', color=INK, line_height=1.25)
    ops += text('foot-note', 'Results are proposals. Text changes only when you accept. Find the list in Inspector → Checks.',
                20, MARGIN, 830, 1180, 56, color=INK)
    return dict(
        key='checks', ref='Guide 3', kicker='Guide', title='Checks at a glance',
        lede='Four kinds of check, what each starts as, and where it runs.',
        ops=ops, alt='A table of the four kinds of check with their default state and where they run.',
    )


def formats():
    rows = [
        ('save', 'Keep a master you can edit later', 'Save as .byax'),
        ('open-document', 'Open Word, ODT, RTF, HTML, EPUB or text', 'File → Open. A report lists anything lost.'),
        ('export', 'Share a PDF or Word copy', 'File → Export. The master is never replaced.'),
        ('pages', 'See how a format will look', 'Use the File view selector in the toolbar.'),
        ('contents', 'Publish an e-book', 'File → Export → EPUB.'),
        ('print', 'Make a print book or a submission', 'File → Compile…'),
    ]
    ops = text('h-want', 'YOU WANT TO', 16, MARGIN + 100, 318, 400, 24, font='onionskin', color=MUTED, tracking=1.5)
    ops += text('h-do', 'DO THIS', 16, 780, 318, 300, 24, font='onionskin', color=MUTED, tracking=1.5)
    for i, (key, want, do) in enumerate(rows):
        y = 348 + i * 88
        ops += box(f'row-{i}', MARGIN, y, W - 2 * MARGIN, 76)
        ops += icon(f'row-{i}', key, MARGIN + 16, y + 6, 64)
        ops += text(f'row-want-{i}', want, 24, MARGIN + 100, y + 22, 640, 40)
        ops += text(f'row-do-{i}', do, 22, 780, y + 24, 700, 40, color=DEEP)
    ops += text('note', 'Hidden or unsupported content stays in the master. Word, ODT and RTF previews are approximate.',
                20, MARGIN, 884, W - 2 * MARGIN, 28, font='scholium', color=INK)
    return dict(
        key='files-and-formats', ref='Guide 4', kicker='Guide', title='Your file, your copies',
        lede='The master file holds everything. Every other format is a copy that says what it left out.',
        ops=ops, alt='A table matching six goals to the File command that does them.',
    )


def privacy():
    claims = [
        ('No account', 'Nothing to sign up for. Write the day you install.'),
        ('No telemetry', 'ByAuthorX does not report how you use it.'),
        ('No background traffic', 'No update pings. Help → Check for updates makes one request, only when you click it.'),
        ('Nothing leaves until you choose', 'AI sends nothing until you add a provider and press Send.'),
    ]
    ops = image('art-copy', ROOT / 'assets' / 'logo-companion-library' / 'illustrations' / 'local-copy-light.png', 1000, 330, 520)
    for i, (head, body) in enumerate(claims):
        y = 330 + i * 130
        ops += box(f'claim-{i}', MARGIN, y, 880, 112)
        ops += badge(f'claim-{i}', i + 1, MARGIN + 24, y + 34, 44)
        ops += text(f'claim-head-{i}', head, 28, MARGIN + 92, y + 16, 760, 40)
        ops += text(f'claim-body-{i}', body, 20, MARGIN + 92, y + 58, 760, 46, color=MUTED, line_height=1.2)
    ops += text('ai-note', 'Hosted or local, you choose the provider. Keys stay in your OS keyring or a named environment variable.',
                22, 1000, 700, 520, 120, font='scholium', color=INK, line_height=1.3)
    return dict(
        key='private-by-default', ref='Guide 5', kicker='Guide', title='Private by default',
        lede='Four claims, said the same way everywhere ByAuthorX speaks.',
        ops=ops, alt='Four privacy claims: no account, no telemetry, no background network traffic, nothing leaves until you choose.',
    )


SHEETS = [proposals, share, book, ai, conflict, window, first_minutes, checks, formats, privacy]


# --------------------------------------------------------------------------- build

def new_project(height=H):
    project = Project(W, height, PAPER, workspace=ROOT)
    for name, rel in FACES.items():
        import_font(project, FONTS / rel, name)
    return project


def report(label, project):
    result = project.check(checks=['bounds', 'contrast', 'composition'])
    findings = [i for i in result.get('issues', []) if i.get('severity') in ('error', 'fix')]
    print(f"{label}: {'ok' if result['passed'] else 'FAILED'}  ({len(findings)} to fix)")
    for issue in findings[:8]:
        print('   ', issue.get('rule') or issue.get('code'), issue.get('message', '')[:140])
    return result['passed']


def build_sheet(sheet, index, total):
    project = new_project()
    result = project.apply(chrome(sheet, index, total) + sheet['ops'])
    for warning in result.get('warnings', []):
        print('  warn', sheet['key'], str(warning)[:160])
    for sub in ('vixl', 'png', 'svg'):
        (OUT / sub).mkdir(parents=True, exist_ok=True)
    project.save(OUT / 'vixl' / f"{sheet['key']}.vixl", overwrite=True)
    project.export(OUT / 'png' / f"{sheet['key']}.png", overwrite=True)
    project.export(OUT / 'svg' / f"{sheet['key']}.svg", overwrite=True)
    return report(sheet['key'], project)


def build_booklet(sheets):
    project = new_project()
    total = len(sheets) + 2
    cover = dict(key='cover', ref='Booklet', kicker='Visual guides', title='ByAuthorX at a glance',
                 lede='Flowcharts and guides for writers. Your words. Your call.', ops=[])
    toc = [f"{i + 3}.  {s['title']}" for i, s in enumerate(sheets)]
    contents = dict(key='contents', ref='Contents', kicker='Contents', title='What is inside',
                    lede='Five flowcharts and five guides. Each page also stands alone as a PNG.', ops=[])
    contents['ops'] = text('toc', '\n'.join(toc), 26, MARGIN, 330, 1200, 560, line_height=1.55)
    pages = [cover, contents] + sheets
    ops = []
    for n, sheet in enumerate(pages, 1):
        name = f'p{n}'
        ops.append(dict(type='page', action='add', name=name))
        page_ops = chrome(sheet, n, total) + sheet['ops']
        for op in page_ops:
            if op['type'] != 'accessibility':
                op = {**op, 'page': name}
                if op['type'] == 'diagram':
                    op['name'] = f"{op['name']}-{n}"

            ops.append(op)
        if sheet is cover:
            ops += [{**op, 'page': name} for op in
                    art('cover-art', 'checks-clear', 520, 330, 560) + text(
                        'cover-line', 'Write. Review. Make it yours.', 34, MARGIN, 840, W - 2 * MARGIN, 50,
                        align='center', color=DEEP)]
    project.apply(ops)
    project.save(OUT / 'vixl' / 'byauthorx-at-a-glance.vixl', overwrite=True)
    project.export(OUT / 'ByAuthorX-at-a-glance.pdf', overwrite=True)
    print('booklet: pages', total)
    return report('booklet', project)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--only')
    parser.add_argument('--no-booklet', action='store_true')
    args = parser.parse_args(argv)
    sheets = [make() for make in SHEETS]
    total = len(sheets) + 2
    ok = True
    for index, sheet in enumerate(sheets, 2):
        if args.only and args.only != sheet['key']:
            continue
        ok &= build_sheet(sheet, index, total)
    if not args.only and not args.no_booklet:
        ok &= build_booklet(sheets)
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
