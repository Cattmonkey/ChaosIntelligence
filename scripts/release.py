#!/usr/bin/env python3
"""Checkout entry point for the complete release builder."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from chaos.release import main

if __name__ == '__main__':
    raise SystemExit(main())
