import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from blackbox.config import load_config  # noqa: E402
import dflog  # noqa: E402


@pytest.fixture
def cfg():
    return load_config()


@pytest.fixture
def make_log(tmp_path):
    """Write a synthetic flight to a temp .bin and return its path."""
    def _make(name="flight.bin", **faults):
        path = tmp_path / name
        path.write_bytes(dflog.flight(**faults))
        return path
    return _make
