from byax.library_parser import dispatch_worker

dispatch_worker()

from byax.mcp import main

if __name__ == '__main__':
    raise SystemExit(main())
