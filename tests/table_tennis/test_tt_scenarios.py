import pytest

from table_tennis.sim.scenarios import SCENARIOS

# Engine now checks the rally id before the status, so an award for a closed rally is
# stale_rally in every state. No known mismatches.
KNOWN_MISMATCH: set = set()


@pytest.mark.parametrize("name", [
    pytest.param(n, marks=pytest.mark.xfail(strict=True, reason="scenario expects stale_rally, engine returns invalid_state"))
    if n in KNOWN_MISMATCH else n
    for n in sorted(SCENARIOS)
])
def test_scenario(name, d):
    SCENARIOS[name](d)
