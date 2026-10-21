"""PyInstaller entry point. Kept outside the package so the frozen exe starts through the same `main()`."""

import sys

from media_forge.shell.main import main

if __name__ == "__main__":
    sys.exit(main())
