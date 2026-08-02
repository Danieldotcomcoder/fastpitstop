"""f1_radio test fixtures. Shared builders live in `radio_data` (a
uniquely-named module) so sibling test modules can import them without a
cross-package `conftest` name clash under importlib mode."""

from __future__ import annotations

import sys
from pathlib import Path

# Put the tests dir on sys.path so `import radio_data` resolves.
_TESTS_DIR = str(Path(__file__).parent)
if _TESTS_DIR not in sys.path:
    sys.path.insert(0, _TESTS_DIR)
