"""`python -m media_forge [options] [files]`: the app from source, the same entry as the exe."""

import sys

from media_forge.shell.main import main

if __name__ == "__main__":
    sys.exit(main())
