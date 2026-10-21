#!/usr/bin/env python3
"""Launcher.  python mako.py            -> opens the web UI
              python mako.py check|preview|intake|merge|all ...  -> command line"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

if __name__ == "__main__":
    if len(sys.argv) > 1:
        from mako_pipeline.cli import main
        sys.exit(main())
    try:
        from mako_pipeline.ui import launch
    except ImportError as e:
        print(f"The UI needs Gradio ({e}).\nRun:  pip install -r requirements.txt")
        sys.exit(2)
    launch()
