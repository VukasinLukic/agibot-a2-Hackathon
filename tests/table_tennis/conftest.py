import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from table_tennis.sim.driver import InProcessDriver  # noqa: E402
from table_tennis.sim.fixtures import make_runtime  # noqa: E402


@pytest.fixture
def rt_ids(tmp_path):
    runtime, ids = make_runtime(str(tmp_path))
    try:
        yield runtime, ids
    finally:
        runtime.stop()


@pytest.fixture
def rt(rt_ids):
    return rt_ids[0]


@pytest.fixture
def d(rt_ids):
    runtime, ids = rt_ids
    return InProcessDriver(runtime, ids=ids)
