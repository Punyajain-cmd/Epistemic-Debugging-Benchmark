#!/usr/bin/env python3
"""Launch the EpiDebug researcher prototype."""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from web.app import main

if __name__ == "__main__":
    main()
