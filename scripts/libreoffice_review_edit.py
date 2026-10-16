"""Edit a ByAuthorX review copy in LibreOffice with change recording on, then save it as DOCX.

Run with the Python that ships LibreOffice's UNO bridge (on Debian/Ubuntu ``/usr/bin/python3`` with the
``python3-uno`` package), not the project environment:

    /usr/bin/python3 scripts/libreoffice_review_edit.py review-copy.docx libreoffice-edited.docx

The edits are a fixed script (a replacement, an insertion, a deletion and a comment by the reviewer
"Lee Reviewer"), so the output is a real third-party DOCX with LibreOffice's own serialization of
tracked changes and comments. Used by ``scripts/docx_review_fixtures.py --libreoffice``.
"""

import os
import subprocess
import sys
import tempfile
import time

import uno
from com.sun.star.beans import PropertyValue


def prop(name, value):
    item = PropertyValue()
    item.Name, item.Value = name, value
    return item


def connect(port, profile):
    process = subprocess.Popen(
        [
            'soffice',
            '--headless',
            '--invisible',
            '--norestore',
            f'-env:UserInstallation={uno.systemPathToFileUrl(profile)}',
            f'--accept=socket,host=127.0.0.1,port={port};urp;',
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    local = uno.getComponentContext()
    resolver = local.ServiceManager.createInstanceWithContext('com.sun.star.bridge.UnoUrlResolver', local)
    for _ in range(150):
        try:
            context = resolver.resolve(
                f'uno:socket,host=127.0.0.1,port={port};urp;StarOffice.ComponentContext'
            )
            return process, context
        except Exception:  # noqa: BLE001 - the office is still starting
            time.sleep(0.2)
    process.kill()
    raise SystemExit('LibreOffice did not start')


def set_author(context, given, family):
    provider = context.ServiceManager.createInstanceWithContext(
        'com.sun.star.configuration.ConfigurationProvider', context
    )
    access = provider.createInstanceWithArguments(
        'com.sun.star.configuration.ConfigurationUpdateAccess',
        (prop('nodepath', '/org.openoffice.UserProfile/Data'),),
    )
    access.setPropertyValue('givenname', given)
    access.setPropertyValue('sn', family)
    access.commitChanges()


def find(document, text):
    search = document.createSearchDescriptor()
    search.SearchString = text
    found = document.findFirst(search)
    if found is None:
        raise SystemExit(f'Text not found in review copy: {text!r}')
    return found


def main(source, target):
    with tempfile.TemporaryDirectory(prefix='byax-lo-') as profile:
        process, context = connect(2083, profile)
        try:
            set_author(context, 'Lee', 'Reviewer')
            desktop = context.ServiceManager.createInstanceWithContext('com.sun.star.frame.Desktop', context)
            document = desktop.loadComponentFromURL(
                uno.systemPathToFileUrl(os.path.abspath(source)), '_blank', 0, (prop('Hidden', True),)
            )
            document.RecordChanges = True
            find(document, 'climbed').setString('hauled herself up')
            sea = find(document, 'sea worked')
            sea.getText().insertString(sea.getStart(), 'restless ', False)
            find(document, ' with a patience she envied').setString('')
            target_range = find(document, 'eleven years')
            note = document.createInstance('com.sun.star.text.textfield.Annotation')
            note.Author = 'Lee Reviewer'
            note.Content = 'Is this the right number?'
            target_range.getText().insertTextContent(target_range, note, True)
            document.storeToURL(
                uno.systemPathToFileUrl(os.path.abspath(target)), (prop('FilterName', 'MS Word 2007 XML'),)
            )
            document.close(True)
        finally:
            try:
                desktop.terminate()
            except Exception:  # noqa: BLE001 - shutting down
                pass
            process.wait(timeout=30)


if __name__ == '__main__':
    main(sys.argv[1], sys.argv[2])
