#!/usr/bin/env python3
"""Compatibility wrapper: the runner lives in ``reddit_signal.migrate`` (run it from the repo root)."""
import sys

from reddit_signal.migrate import main

if __name__ == "__main__":
    sys.exit(main())
