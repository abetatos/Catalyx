"""Test-session isolation (plan v4 §5 D-f).

THE DEFECT THIS EXISTS TO KILL. `CATALYX_PRICES_OFFLINE=1` is a hard kill switch: `prices.refresh`
honours it even when the caller injects its own `fetch_fn`, which is the right contract — a kill
switch an argument can override is not a kill switch. But six tests inject a fake fetcher and
never touch the network, and with that variable exported in the developer's shell they failed:

    CATALYX_PRICES_OFFLINE=1 uv run pytest      → 6 failed
    uv run pytest                               → all green

A suite whose result depends on the shell it was launched from is not measuring the code. The
variable is a RUNTIME switch, not a test input, so it is cleared for every test; the one test that
is actually about offline behaviour sets it itself with `monkeypatch.setenv`, which still works.

The offline switch stays exactly as strict as it was — this changes no production behaviour.
"""
from __future__ import annotations

import pytest

# Runtime switches that must never leak in from the developer's environment. Each is settable
# per-test via monkeypatch; none may be inherited.
_RUNTIME_ENV = ("CATALYX_PRICES_OFFLINE",)


@pytest.fixture(autouse=True)
def _isolate_runtime_env(monkeypatch):
    for name in _RUNTIME_ENV:
        monkeypatch.delenv(name, raising=False)
