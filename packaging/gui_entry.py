import sys

from byax.library_parser import dispatch_worker

dispatch_worker()

# The macOS app is one executable: Contents/MacOS/byax and byax-mcp launch it with these flags so the
# command-line tools reuse the bundled interpreter. Nothing else passes them.
if len(sys.argv) > 1 and sys.argv[1] in ('--byax-cli', '--byax-mcp'):
    mode = sys.argv.pop(1)
    if mode == '--byax-cli':
        from byax.cli import main
    else:
        from byax.mcp import main
    raise SystemExit(main())

from byax.gui import main

if __name__ == '__main__':
    raise SystemExit(main())
