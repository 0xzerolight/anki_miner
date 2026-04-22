"""Entry point for python -m anki_miner."""

import sys

from anki_miner.gui.app import main

if __name__ == "__main__":
    sys.exit(main())
