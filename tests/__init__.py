"""The tests. A package, so `python -m unittest discover -p 'test_*.py'` run from the repo root still
finds them and the app's modules import from the root. One file: `python -m unittest tests.test_rules`;
a live e2e script: `python -m tests.test_e2e`."""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent   # the repo root, where the app's files are
