"""Emulate an editor working in Microsoft Word on a ByAuthorX review copy (test support).

The edits are written as raw WordprocessingML the way Word 2016+ saves them: ``w:ins``/``w:del`` with
``w:id``/``w:author``/``w:date`` and ``w:rsidR``/``w:rsidDel`` run attributes, ``w:delText``, paragraph
mark revisions in ``w:pPr/w:rPr``, ``w:moveFrom``/``w:moveTo`` with named move ranges, ``w:rPrChange``
and ``w:pPrChange``, comments with ``w14:paraId`` and ``commentsExtended``/``commentsIds``/
``commentsExtensible`` parts for threads, done state and durable identities, plus constructs the
adapter must report (complex fields, SmartArt, content controls, ink, equations, table cell
revisions). The package is renumbered the way Word renumbers comment identifiers on save.
"""

from __future__ import annotations

import copy
import io
import zipfile

from lxml import etree

W_NS = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'
NS = {
    'w': W_NS,
    'w14': 'http://schemas.microsoft.com/office/word/2010/wordml',
    'w15': 'http://schemas.microsoft.com/office/word/2012/wordml',
    'w16cid': 'http://schemas.microsoft.com/office/word/2016/wordml/cid',
    'w16cex': 'http://schemas.microsoft.com/office/word/2018/wordml/cex',
    'm': 'http://schemas.openxmlformats.org/officeDocument/2006/math',
    'mc': 'http://schemas.openxmlformats.org/markup-compatibility/2006',
    'a': 'http://schemas.openxmlformats.org/drawingml/2006/main',
    'wp': 'http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing',
    'r': 'http://schemas.openxmlformats.org/officeDocument/2006/relationships',
}
XML_SPACE = '{http://www.w3.org/XML/1998/namespace}space'
PARTS = {
    'comments': (
        'word/comments.xml',
        'application/vnd.openxmlformats-officedocument.wordprocessingml.comments+xml',
        'http://schemas.openxmlformats.org/officeDocument/2006/relationships/comments',
        'w:comments',
    ),
    'extended': (
        'word/commentsExtended.xml',
        'application/vnd.openxmlformats-officedocument.wordprocessingml.commentsExtended+xml',
        'http://schemas.microsoft.com/office/2011/relationships/commentsExtended',
        'w15:commentsEx',
    ),
    'ids': (
        'word/commentsIds.xml',
        'application/vnd.openxmlformats-officedocument.wordprocessingml.commentsIds+xml',
        'http://schemas.microsoft.com/office/2016/09/relationships/commentsIds',
        'w16cid:commentsIds',
    ),
    'extensible': (
        'word/commentsExtensible.xml',
        'application/vnd.openxmlformats-officedocument.wordprocessingml.commentsExtensible+xml',
        'http://schemas.microsoft.com/office/2018/08/relationships/commentsExtensible',
        'w16cex:commentsExtensible',
    ),
}


def q(name):
    prefix, local = name.split(':')
    return f'{{{NS[prefix]}}}{local}'


def text_of(paragraph):
    """Accept-all text of a paragraph, for assertions."""
    parts = []
    for node in paragraph.iter(q('w:t')):
        if not any(a.tag in (q('w:del'), q('w:moveFrom')) for a in node.iterancestors()):
            parts.append(node.text or '')
    return ''.join(parts)


class WordEditor:
    """Apply Word-style edits to a review copy and save it as Word would."""

    def __init__(self, data, author='Jane Editor', date='2026-10-02T09:15:00Z', initials='JE'):
        self.author, self.date, self.initials = author, date, initials
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            self.members = {item.filename: archive.read(item) for item in archive.infolist()}
        self.document = etree.fromstring(self.members['word/document.xml'])
        self.body = self.document.find(q('w:body'))
        self.parts = {}
        for key, (name, *_rest, root) in PARTS.items():
            if name in self.members:
                self.parts[key] = etree.fromstring(self.members[name])
        self.next_id = 900
        self.rsid = '00C4D2E1'
        self.para_counter = 0x1A2B0000
        self.durable_counter = 0x3C4D0000
        self.comment_ids = []

    # identities

    def rev_id(self):
        self.next_id += 1
        return str(self.next_id)

    def attrs(self, element, author=None, date=None):
        element.set(q('w:id'), self.rev_id())
        element.set(q('w:author'), author or self.author)
        element.set(q('w:date'), date or self.date)
        return element

    def paragraphs(self):
        return self.body.findall(q('w:p'))

    # runs

    def _runs(self, paragraph):
        """(start, end, run) for visible runs, in order, including runs inside hyperlinks."""
        result, offset = [], 0
        for run in paragraph.iter(q('w:r')):
            if any(a.tag in (q('w:del'), q('w:moveFrom')) for a in run.iterancestors()):
                continue
            text = ''.join(t.text or '' for t in run.findall(q('w:t')))
            result.append((offset, offset + len(text), run))
            offset += len(text)
        return result

    def _split(self, paragraph, offset):
        """Make ``offset`` a run boundary; return the run starting there (or None at the end)."""
        for start, end, run in self._runs(paragraph):
            if start == offset and end > start:
                return run
            if start < offset < end:
                text = run.find(q('w:t'))
                second = copy.deepcopy(run)
                text.text, second.find(q('w:t')).text = (
                    text.text[: offset - start],
                    text.text[offset - start :],
                )
                for element in (text, second.find(q('w:t'))):
                    element.set(XML_SPACE, 'preserve')
                run.addnext(second)
                return second
        return None

    def _covered(self, paragraph, start, end):
        self._split(paragraph, start)
        self._split(paragraph, end)
        return [run for a, b, run in self._runs(paragraph) if a >= start and b <= end and b > a]

    def _new_run(self, text, rpr=None):
        run = etree.Element(q('w:r'))
        run.set(q('w:rsidR'), self.rsid)
        if rpr is not None:
            run.append(copy.deepcopy(rpr))
        node = etree.SubElement(run, q('w:t'))
        node.text = text
        node.set(XML_SPACE, 'preserve')
        return run

    def _insert_point(self, paragraph, offset):
        """Element to insert new inline content before, or None to append."""
        run = self._split(paragraph, offset)
        if run is None:
            return None
        while run.getparent() is not paragraph:
            run = run.getparent()
        return run

    def _place(self, paragraph, offset, element):
        anchor = self._insert_point(paragraph, offset)
        if anchor is None:
            paragraph.append(element)
        else:
            anchor.addprevious(element)

    # tracked edits

    def insert(self, index, offset, text, **who):
        paragraph = self.paragraphs()[index]
        wrapper = self.attrs(etree.Element(q('w:ins')), **who)
        wrapper.append(self._new_run(text))
        self._place(paragraph, offset, wrapper)
        return wrapper

    def delete(self, index, start, end, **who):
        paragraph = self.paragraphs()[index]
        runs = self._covered(paragraph, start, end)
        wrapper = self.attrs(etree.Element(q('w:del')), **who)
        runs[0].addprevious(wrapper)
        for run in runs:
            run.set(q('w:rsidDel'), self.rsid)
            for node in run.findall(q('w:t')):
                node.tag = q('w:delText')
                node.set(XML_SPACE, 'preserve')
            wrapper.append(run)
        return wrapper

    def replace(self, index, start, end, text, **who):
        removed = self.delete(index, start, end, **who)
        inserted = self.attrs(etree.Element(q('w:ins')), **who)
        inserted.append(self._new_run(text))
        removed.addnext(inserted)

    def _mark(self, paragraph, kind, **who):
        ppr = paragraph.find(q('w:pPr'))
        if ppr is None:
            ppr = etree.Element(q('w:pPr'))
            paragraph.insert(0, ppr)
        rpr = ppr.find(q('w:rPr'))
        if rpr is None:
            rpr = etree.SubElement(ppr, q('w:rPr'))
        rpr.append(self.attrs(etree.Element(q('w:' + kind)), **who))

    def split(self, index, offset, **who):
        """Press Enter inside a paragraph with Track Changes on."""
        paragraph = self.paragraphs()[index]
        anchor = self._insert_point(paragraph, offset)
        second = etree.Element(q('w:p'))
        for name, value in paragraph.attrib.items():
            second.set(name, value)
        second.set(q('w14:paraId'), self._para_id())
        ppr = paragraph.find(q('w:pPr'))
        if ppr is not None:
            second.append(copy.deepcopy(ppr))
        moving = False
        for child in list(paragraph):
            if child is anchor:
                moving = True
            if moving:
                second.append(child)
        paragraph.addnext(second)
        self._mark(paragraph, 'ins', **who)
        return second

    def insert_paragraph_after(self, index, text, **who):
        """Type Enter at the end of a paragraph, then new text (Word marks the first mark inserted)."""
        paragraph = self.paragraphs()[index]
        new = etree.Element(q('w:p'))
        new.set(q('w14:paraId'), self._para_id())
        new.set(q('w14:textId'), '77777777')
        new.set(q('w:rsidR'), self.rsid)
        ppr = paragraph.find(q('w:pPr'))
        if ppr is not None:
            clean = copy.deepcopy(ppr)
            for rpr in clean.findall(q('w:rPr')):
                clean.remove(rpr)
            new.append(clean)
        wrapper = self.attrs(etree.Element(q('w:ins')), **who)
        wrapper.append(self._new_run(text))
        new.append(wrapper)
        paragraph.addnext(new)
        self._mark(paragraph, 'ins', **who)
        return new

    def delete_paragraph(self, index, **who):
        paragraph = self.paragraphs()[index]
        length = sum(b - a for a, b, _ in self._runs(paragraph))
        if length:
            self.delete(index, 0, length, **who)
        self._mark(paragraph, 'del', **who)

    def move_paragraph(self, source, target, name='move463915822', **who):
        """Drag a whole paragraph to sit before paragraph ``target`` (Track Changes on)."""
        paragraphs = self.paragraphs()
        paragraph, destination = paragraphs[source], paragraphs[target]
        moved = copy.deepcopy(paragraph)
        range_id = self.rev_id()
        start = self.attrs(etree.Element(q('w:moveFromRangeStart')), **who)
        start.set(q('w:id'), range_id)
        start.set(q('w:name'), name)
        wrapper = self.attrs(etree.Element(q('w:moveFrom')), **who)
        runs = [child for child in paragraph if child.tag in (q('w:r'), q('w:hyperlink'))]
        if runs:
            runs[0].addprevious(start)
            start.addnext(wrapper)
            for run in runs:
                for node in run.iter(q('w:t')):
                    node.tag = q('w:delText')
                run.set(q('w:rsidDel'), self.rsid)
                wrapper.append(run)
            end = etree.Element(q('w:moveFromRangeEnd'))
            end.set(q('w:id'), range_id)
            wrapper.addnext(end)
        self._mark(paragraph, 'moveFrom', **who)
        # The destination copy: Word drops identity bookmarks from the moved copy and marks it moveTo.
        for bookmark in moved.findall(q('w:bookmarkStart')) + moved.findall(q('w:bookmarkEnd')):
            moved.remove(bookmark)
        moved.set(q('w14:paraId'), self._para_id())
        to_range = self.rev_id()
        to_start = self.attrs(etree.Element(q('w:moveToRangeStart')), **who)
        to_start.set(q('w:id'), to_range)
        to_start.set(q('w:name'), name)
        to_wrapper = self.attrs(etree.Element(q('w:moveTo')), **who)
        moved_runs = [child for child in moved if child.tag in (q('w:r'), q('w:hyperlink'))]
        if moved_runs:
            moved_runs[0].addprevious(to_start)
            to_start.addnext(to_wrapper)
            for run in moved_runs:
                to_wrapper.append(run)
            to_end = etree.Element(q('w:moveToRangeEnd'))
            to_end.set(q('w:id'), to_range)
            to_wrapper.addnext(to_end)
        self._mark(moved, 'moveTo', **who)
        destination.addprevious(moved)

    def format(self, index, start, end, prop='b', **who):
        paragraph = self.paragraphs()[index]
        for run in self._covered(paragraph, start, end):
            rpr = run.find(q('w:rPr'))
            old = copy.deepcopy(rpr) if rpr is not None else etree.Element(q('w:rPr'))
            if rpr is None:
                rpr = etree.Element(q('w:rPr'))
                run.insert(0, rpr)
            etree.SubElement(rpr, q('w:' + prop))
            change = self.attrs(etree.SubElement(rpr, q('w:rPrChange')), **who)
            old.tag = q('w:rPr')
            change.append(old)

    def align(self, index, value='center', **who):
        paragraph = self.paragraphs()[index]
        ppr = paragraph.find(q('w:pPr'))
        if ppr is None:
            ppr = etree.Element(q('w:pPr'))
            paragraph.insert(0, ppr)
        old = copy.deepcopy(ppr)
        for child in list(old):
            if child.tag in (q('w:rPr'), q('w:pPrChange')):
                old.remove(child)
        jc = etree.SubElement(ppr, q('w:jc'))
        jc.set(q('w:val'), value)
        change = self.attrs(etree.SubElement(ppr, q('w:pPrChange')), **who)
        change.append(old)

    def untracked(self, index, start, end, text):
        """Type over text with Track Changes off."""
        paragraph = self.paragraphs()[index]
        runs = self._covered(paragraph, start, end) if end > start else []
        if runs:
            runs[0].find(q('w:t')).text = text
            for run in runs[1:]:
                run.getparent().remove(run)
        else:
            self._place(paragraph, start, self._new_run(text))

    # comments

    def _para_id(self):
        self.para_counter += 7
        return f'{self.para_counter:08X}'

    def _part(self, key):
        if key not in self.parts:
            _name, _content_type, _reltype, root = PARTS[key]
            prefix = root.split(':')[0]
            element = etree.Element(q(root), nsmap={prefix: NS[prefix], 'mc': NS['mc'], 'w14': NS['w14']})
            if key != 'comments':
                element.set(q('mc:Ignorable'), prefix)
            self.parts[key] = element
        return self.parts[key]

    def comment(self, index, start, end, text, *, reply_to=None, done=False, **who):
        """Comment on ``[start, end)`` of paragraph ``index``; ``reply_to`` makes it a reply."""
        comments = self._part('comments')
        identifier = str(max([int(c.get(q('w:id'))) for c in comments.findall(q('w:comment'))] + [-1]) + 1)
        paragraph = self.paragraphs()[index]
        range_start = etree.Element(q('w:commentRangeStart'))
        range_start.set(q('w:id'), identifier)
        range_end = etree.Element(q('w:commentRangeEnd'))
        range_end.set(q('w:id'), identifier)
        reference = etree.Element(q('w:r'))
        rpr = etree.SubElement(reference, q('w:rPr'))
        etree.SubElement(rpr, q('w:rStyle')).set(q('w:val'), 'CommentReference')
        etree.SubElement(reference, q('w:commentReference')).set(q('w:id'), identifier)
        self._place(paragraph, start, range_start)
        anchor = self._insert_point(paragraph, end)
        if anchor is None:
            paragraph.append(range_end)
        else:
            anchor.addprevious(range_end)
        range_end.addnext(reference)
        element = etree.SubElement(comments, q('w:comment'))
        element.set(q('w:id'), identifier)
        element.set(q('w:author'), who.get('author', self.author))
        element.set(q('w:date'), who.get('date', self.date))
        element.set(q('w:initials'), who.get('initials', self.initials))
        para_id = self._para_id()
        body = etree.SubElement(element, q('w:p'))
        body.set(q('w14:paraId'), para_id)
        body.set(q('w14:textId'), '77777777')
        ppr = etree.SubElement(body, q('w:pPr'))
        etree.SubElement(ppr, q('w:pStyle')).set(q('w:val'), 'CommentText')
        annotation = etree.SubElement(body, q('w:r'))
        etree.SubElement(etree.SubElement(annotation, q('w:rPr')), q('w:rStyle')).set(
            q('w:val'), 'CommentReference'
        )
        etree.SubElement(annotation, q('w:annotationRef'))
        body.append(self._new_run(text))
        extended = etree.SubElement(self._part('extended'), q('w15:commentEx'))
        extended.set(q('w15:paraId'), para_id)
        if reply_to is not None:
            extended.set(q('w15:paraIdParent'), reply_to['para_id'])
        extended.set(q('w15:done'), '1' if done else '0')
        self.durable_counter += 13
        durable = f'{self.durable_counter:08X}'
        ids = etree.SubElement(self._part('ids'), q('w16cid:commentId'))
        ids.set(q('w16cid:paraId'), para_id)
        ids.set(q('w16cid:durableId'), durable)
        extensible = etree.SubElement(self._part('extensible'), q('w16cex:commentExtensible'))
        extensible.set(q('w16cex:durableId'), durable)
        extensible.set(q('w16cex:dateUtc'), who.get('date', self.date))
        record = {'id': identifier, 'para_id': para_id, 'durable_id': durable}
        self.comment_ids.append(record)
        return record

    def reply(self, parent, text, *, done=False, **who):
        """Reply in the thread of an existing comment (identified by its ``w:id``)."""
        comments = self._part('comments')
        target = comments.xpath(f'w:comment[@w:id="{parent}"]', namespaces=NS)[0]
        para_id = target.findall(q('w:p'))[-1].get(q('w14:paraId'))
        position = self.body.xpath(f'.//w:commentRangeStart[@w:id="{parent}"]', namespaces=NS)[0]
        paragraph = position.getparent()
        index = self.paragraphs().index(paragraph)
        offset = 0
        for node in paragraph:
            if node is position:
                break
            offset += sum(len(t.text or '') for t in node.iter(q('w:t')))
        return self.comment(index, offset, offset, text, reply_to={'para_id': para_id}, done=done, **who)

    def mark_done(self, parent):
        comments = self._part('comments')
        target = comments.xpath(f'w:comment[@w:id="{parent}"]', namespaces=NS)[0]
        para_id = target.findall(q('w:p'))[-1].get(q('w14:paraId'))
        found = self._part('extended').xpath(f'w15:commentEx[@w15:paraId="{para_id}"]', namespaces=NS)
        entry = found[0] if found else etree.SubElement(self._part('extended'), q('w15:commentEx'))
        entry.set(q('w15:paraId'), para_id)
        entry.set(q('w15:done'), '1')

    # constructs the adapter reports

    def add_field(self, index, instruction=' PAGE ', result='3'):
        paragraph = self.paragraphs()[index]
        for kind, payload in (
            ('begin', None),
            ('instr', instruction),
            ('separate', None),
            ('text', result),
            ('end', None),
        ):
            run = etree.SubElement(paragraph, q('w:r'))
            if kind == 'instr':
                node = etree.SubElement(run, q('w:instrText'))
                node.text = payload
                node.set(XML_SPACE, 'preserve')
            elif kind == 'text':
                etree.SubElement(run, q('w:t')).text = payload
            else:
                etree.SubElement(run, q('w:fldChar')).set(q('w:fldCharType'), kind)

    def add_smartart(self, index):
        paragraph = self.paragraphs()[index]
        run = etree.SubElement(paragraph, q('w:r'))
        drawing = etree.SubElement(run, q('w:drawing'))
        inline = etree.SubElement(drawing, q('wp:inline'))
        graphic = etree.SubElement(inline, q('a:graphic'))
        data = etree.SubElement(graphic, q('a:graphicData'))
        data.set('uri', 'http://schemas.openxmlformats.org/drawingml/2006/diagram')

    def add_content_control(self, index):
        paragraph = self.paragraphs()[index]
        sdt = etree.Element(q('w:sdt'))
        properties = etree.SubElement(sdt, q('w:sdtPr'))
        etree.SubElement(properties, q('w:alias')).set(q('w:val'), 'Client name')
        content = etree.SubElement(sdt, q('w:sdtContent'))
        content.append(self._new_run(' [client]'))
        paragraph.append(sdt)

    def add_ink(self, index):
        paragraph = self.paragraphs()[index]
        run = etree.SubElement(paragraph, q('w:r'))
        alternate = etree.SubElement(run, q('mc:AlternateContent'))
        choice = etree.SubElement(alternate, q('mc:Choice'))
        choice.set('Requires', 'w14')
        part = etree.SubElement(choice, q('w14:contentPart'))
        part.set(q('r:id'), 'rId900')

    def add_equation(self, index):
        paragraph = self.paragraphs()[index]
        math = etree.SubElement(paragraph, q('m:oMath'))
        run = etree.SubElement(math, q('m:r'))
        etree.SubElement(run, q('m:t')).text = 'E=mc2'

    def add_cell_insertion(self, table=0):
        tables = self.body.findall(q('w:tbl'))
        cell = tables[table].find(q('w:tr')).find(q('w:tc'))
        tcpr = cell.find(q('w:tcPr'))
        if tcpr is None:
            tcpr = etree.Element(q('w:tcPr'))
            cell.insert(0, tcpr)
        self.attrs(etree.SubElement(tcpr, q('w:cellIns')))

    def edit_cell(self, table, row, column, text, **who):
        cell = self.body.findall(q('w:tbl'))[table].findall(q('w:tr'))[row].findall(q('w:tc'))[column]
        paragraph = cell.find(q('w:p'))
        length = sum(b - a for a, b, _ in self._runs(paragraph))
        if length:
            runs = self._covered(paragraph, 0, length)
            wrapper = self.attrs(etree.Element(q('w:del')), **who)
            runs[0].addprevious(wrapper)
            for run in runs:
                for node in run.findall(q('w:t')):
                    node.tag = q('w:delText')
                wrapper.append(run)
            inserted = self.attrs(etree.Element(q('w:ins')), **who)
            inserted.append(self._new_run(text))
            wrapper.addnext(inserted)

    # saving

    def remove_custom_properties(self):
        self.members.pop('docProps/custom.xml', None)

    def _renumber_comments(self):
        """Word renumbers comment ids 0..n in document order when it saves."""
        if 'comments' not in self.parts:
            return
        order = []
        for node in self.body.iter(q('w:commentRangeStart'), q('w:commentReference')):
            if node.get(q('w:id')) not in order:
                order.append(node.get(q('w:id')))
        for comment in self.parts['comments'].findall(q('w:comment')):
            if comment.get(q('w:id')) not in order:
                order.append(comment.get(q('w:id')))
        mapping = {old: str(new) for new, old in enumerate(order)}
        for node in self.body.iter(q('w:commentRangeStart'), q('w:commentRangeEnd'), q('w:commentReference')):
            node.set(q('w:id'), mapping[node.get(q('w:id'))])
        comments = self.parts['comments']
        for comment in comments.findall(q('w:comment')):
            comment.set(q('w:id'), mapping[comment.get(q('w:id'))])
        comments[:] = sorted(comments, key=lambda c: int(c.get(q('w:id'))))

    def save(self, last_modified_by=None):
        self._renumber_comments()
        members = dict(self.members)
        members['word/document.xml'] = etree.tostring(
            self.document, xml_declaration=True, encoding='UTF-8', standalone=True
        )
        types = etree.fromstring(members['[Content_Types].xml'])
        rels = etree.fromstring(members['word/_rels/document.xml.rels'])
        overrides = {item.get('PartName') for item in types}
        targets = {item.get('Target') for item in rels}
        for key, element in self.parts.items():
            name, content_type, reltype, _root = PARTS[key]
            members[name] = etree.tostring(element, xml_declaration=True, encoding='UTF-8', standalone=True)
            if '/' + name not in overrides:
                override = etree.SubElement(
                    types, '{http://schemas.openxmlformats.org/package/2006/content-types}Override'
                )
                override.set('PartName', '/' + name)
                override.set('ContentType', content_type)
            if name.removeprefix('word/') not in targets:
                rel = etree.SubElement(
                    rels, '{http://schemas.openxmlformats.org/package/2006/relationships}Relationship'
                )
                rel.set('Id', f'rId{800 + len(rels)}')
                rel.set('Type', reltype)
                rel.set('Target', name.removeprefix('word/'))
        members['[Content_Types].xml'] = etree.tostring(
            types, xml_declaration=True, encoding='UTF-8', standalone=True
        )
        members['word/_rels/document.xml.rels'] = etree.tostring(
            rels, xml_declaration=True, encoding='UTF-8', standalone=True
        )
        if 'docProps/core.xml' in members:
            core = etree.fromstring(members['docProps/core.xml'])
            cp = 'http://schemas.openxmlformats.org/package/2006/metadata/core-properties'
            node = core.find(f'{{{cp}}}lastModifiedBy')
            if node is None:
                node = etree.SubElement(core, f'{{{cp}}}lastModifiedBy')
            node.text = last_modified_by or self.author
            members['docProps/core.xml'] = etree.tostring(
                core, xml_declaration=True, encoding='UTF-8', standalone=True
            )
        output = io.BytesIO()
        with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as archive:
            for name, data in members.items():
                info = zipfile.ZipInfo(name, date_time=(2026, 10, 2, 9, 15, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                archive.writestr(info, data)
        return output.getvalue()


# -- The fixture manuscript and the editor's session --------------------------------------------

JANE = {'author': 'Jane Editor', 'date': '2026-10-02T09:15:00Z'}
TOM = {'author': 'Tom Copyeditor', 'date': '2026-10-03T14:00:00Z', 'initials': 'TC'}
PARAGRAPHS = [
    'The keeper climbed the stairs at dusk, counting each step aloud.',
    'Below, the sea worked at the rocks with a patience she envied.',
    'She had kept the light for eleven years without a single failure.',
    'Tonight the lamp would not catch, and the fog was already in.',
    "A ship's horn sounded somewhere to the south.",
    'She wrote the time in the log and went back up.',
    'The end of the watch.',
]


def sample_document():
    """The manuscript sent for review: a heading, seven paragraphs, a table and a writer's thread."""
    from byax import engine
    from byax.model import new_document

    doc = new_document('The Lighthouse')
    first = doc['blocks'][0]['id']
    operations = [
        {'type': 'set_text', 'target': first, 'text': 'Chapter One'},
        {'type': 'set_attributes', 'target': first, 'values': {'level': 1}},
    ]
    previous = first
    for index, text in enumerate(PARAGRAPHS[:5]):
        operations.append(
            {'type': 'insert_paragraph', 'text': text, 'after': previous, 'id': f'p_{index + 1}'}
        )
        previous = f'p_{index + 1}'
    doc, _ = engine.apply(doc, {'operations': operations})
    doc, _ = engine.apply(
        doc, {'operations': [{'type': 'insert_table', 'rows': 2, 'columns': 2, 'after': 'p_5'}]}
    )
    table = next(block for block in doc['blocks'] if block['type'] == 'table')
    cells = [cell['id'] for row in table['rows'] for cell in row['cells']]
    operations = [
        {'type': 'update_cell', 'target': cell, 'text': text}
        for cell, text in zip(cells, ['Date', 'Entry', '12 March', 'Lamp cleaned'], strict=True)
    ]
    operations += [
        {'type': 'insert_paragraph', 'text': PARAGRAPHS[5], 'after': table['id'], 'id': 'p_6'},
        {'type': 'insert_paragraph', 'text': PARAGRAPHS[6], 'after': 'p_6', 'id': 'p_7'},
        {
            'type': 'add_comment',
            'target': 'p_2',
            'start': PARAGRAPHS[1].index('the rocks'),
            'end': PARAGRAPHS[1].index('the rocks') + len('the rocks'),
            'text': 'Too gentle?',
            'author': 'A. Writer',
        },
    ]
    doc, _ = engine.apply(doc, {'operations': operations})
    thread = doc['review']['comments'][0]['id']
    doc, _ = engine.apply(
        doc,
        {
            'operations': [
                {'type': 'reply_comment', 'target': thread, 'text': 'Or keep it?', 'author': 'A. Writer'}
            ]
        },
    )
    return doc


def word_session(review_copy):
    """What the fixture editors do in Word; returns the saved DOCX bytes.

    Paragraph indices in the copy: 0 heading, 1-5 paragraphs, (table), 6-7 paragraphs.
    """
    editor = WordEditor(review_copy, **{k: JANE[k] for k in ('author', 'date')})
    p1, p2 = PARAGRAPHS[0], PARAGRAPHS[1]
    # Offsets address the visible text, so edits within a paragraph run from its end backwards.
    start = p1.index(', counting')
    editor.delete(1, start, start + len(', counting each step aloud'), author=TOM['author'], date=TOM['date'])
    editor.replace(1, p1.index('dusk'), p1.index('dusk') + 4, 'nightfall', **JANE)
    editor.insert(2, p2.index('sea'), 'grey ', **JANE)
    p3 = PARAGRAPHS[2]
    thread = editor.comment(
        3, p3.index('eleven'), p3.index('eleven') + len('eleven years'), 'Check against chapter two.', **JANE
    )
    editor.reply(thread['id'], 'Chapter two says twelve.', **TOM)
    p4 = PARAGRAPHS[3]
    editor.comment(4, p4.index('fog'), p4.index('fog') + 3, 'Lovely.', done=True, **TOM)
    editor.reply('0', 'Agreed, I sharpened it.', **JANE)
    editor.format(4, 0, len('Tonight'), 'b', author=TOM['author'], date=TOM['date'])
    editor.align(0, 'center', **JANE)
    editor.edit_cell(0, 1, 1, 'Lamp cleaned and trimmed', author=TOM['author'], date=TOM['date'])
    editor.delete_paragraph(7, **JANE)
    editor.split(6, PARAGRAPHS[5].index(' and went'), **JANE)
    editor.insert_paragraph_after(3, 'Nobody came to relieve her.', **JANE)
    editor.move_paragraph(6, 5, **JANE)  # "A ship's horn…" to before "Tonight…"
    return editor.save()


def word_unsupported_session(review_copy):
    """Constructs the merge must report as located losses, each in its own paragraph."""
    editor = WordEditor(review_copy, **{k: JANE[k] for k in ('author', 'date')})
    editor.add_field(1, ' PAGE ', '3')
    editor.add_smartart(2)
    editor.add_content_control(3)
    editor.add_ink(4)
    editor.add_equation(5)
    editor.add_cell_insertion(0)
    return editor.save()
