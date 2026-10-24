#!/usr/bin/env python3
"""Run the existing Chaos CLI directly from a source checkout."""

if __name__ == "__main__":
    import sys
    from pathlib import Path

    # Resolve this checkout even in isolated mode; keep the caller's working directory.
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from chaos.cli import main

    raise SystemExit(main())
