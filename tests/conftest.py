import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import building  # noqa: E402
import climate  # noqa: E402
import diagnostics  # noqa: E402
import optimizer  # noqa: E402
import simulate  # noqa: E402


@pytest.fixture(scope="session")
def records():
    return simulate.week(building.as_found(), climate.measurement_week())


@pytest.fixture(scope="session")
def diagnosis(records):
    return diagnostics.diagnose(records)


@pytest.fixture(scope="session")
def plan(diagnosis):
    return optimizer.plan(diagnosis)
