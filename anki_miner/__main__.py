"""Entry point for python -m anki_miner."""

import sys

from anki_miner.cli.main import main

if __name__ == "__main__":
    sys.exit(main())
