"""Ensures the project root is importable as a package root for pytest.

Allows tests to use absolute imports such as ``from services.parser import
...`` and ``from schemas.article import ...`` regardless of the directory
pytest is invoked from.
"""

import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))
