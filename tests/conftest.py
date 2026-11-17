from __future__ import annotations

from datetime import UTC, datetime

import pytest

from sovereign.core.clock import FixedClock
from sovereign.kernel import Kernel


@pytest.fixture
def clock() -> FixedClock:
    return FixedClock(datetime(2026, 11, 17, 9, 0, tzinfo=UTC))


@pytest.fixture
def kernel(clock: FixedClock) -> Kernel:
    k = Kernel.ephemeral(clock=clock)
    yield k
    k.close()


@pytest.fixture
def funded(kernel: Kernel) -> Kernel:
    """Noyau avec 10 000 € encaissés → enveloppe de 1 000 €."""

    kernel.finance.record_collection("10000", "2026-11-01", counterparty="Client démo", reference="seed-1")
    return kernel
