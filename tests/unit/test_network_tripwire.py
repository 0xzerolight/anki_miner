"""Self-tests for the network tripwire (``tests/_network_tripwire.py``).

Prove the two halves independently, mirroring ``test_home_isolation.py``'s
pure-callable pattern: (1) the session-installed socket wrapper records and
blocks AF_INET connects while leaving AF_UNIX alone (Qt/xdist internals);
(2) the assert logic conftest's ``_network_guard`` runs at setup/teardown
turns recorded entries into a failure message. The wrapper is installed by
the session-scoped conftest fixture, so these tests exercise the REAL guard,
not a copy.
"""

from __future__ import annotations

import socket

import pytest

from tests import _network_tripwire as _net

# ---------------------------------------------------------------------------
# Socket wrapper: record + block
# ---------------------------------------------------------------------------


def test_af_inet_connect_blocked_and_recorded():
    """A TCP connect raises NetworkTripwire and lands in RECORDED."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s, pytest.raises(_net.NetworkTripwire, match="8765"):
        s.connect(("127.0.0.1", 8765))
    assert _net.RECORDED
    origin, target = _net.RECORDED[-1]
    assert target == "127.0.0.1:8765"
    assert "test_af_inet_connect_blocked_and_recorded" in origin
    # Clear our own deliberate entry so the autouse teardown-assert doesn't
    # (correctly!) fail this very test.
    _net.RECORDED.clear()


def test_connect_ex_blocked_and_recorded():
    """connect_ex is wrapped too — requests never uses it, but stdlib code does."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s, pytest.raises(_net.NetworkTripwire):
        s.connect_ex(("127.0.0.1", 8765))
    assert _net.RECORDED
    _net.RECORDED.clear()


def test_af_unix_connect_untouched(tmp_path):
    """AF_UNIX passes through to the real connect (Qt/xdist internals need it)."""
    target = tmp_path / "no-such-server.sock"
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s, pytest.raises(OSError) as excinfo:
        s.connect(str(target))
    # The REAL connect ran (missing socket file -> OSError), not the tripwire.
    assert not isinstance(excinfo.value, _net.NetworkTripwire)
    assert _net.RECORDED == []


@pytest.mark.network
def test_network_marker_suppresses_guard():
    """@pytest.mark.network lets a real connect through, unrecorded.

    Targets the discard port (9) on loopback: nothing listens there, the
    kernel refuses instantly, and no traffic leaves the machine — but the
    syscall itself must run (proving the wrapper stood aside) rather than
    raise NetworkTripwire.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        rc = s.connect_ex(("127.0.0.1", 9))
    assert rc != 0  # refused by the kernel, not intercepted
    assert _net.RECORDED == []


# ---------------------------------------------------------------------------
# Assert logic (what conftest's _network_guard fails tests with)
# ---------------------------------------------------------------------------


def test_summarize_empty_returns_none():
    assert _net.summarize_recorded([]) is None


def test_summarize_names_target_and_origin():
    msg = _net.summarize_recorded([("tests/unit/test_x.py::test_x (call)", "127.0.0.1:8765")])
    assert msg is not None
    assert "127.0.0.1:8765" in msg
    assert "test_x" in msg
    assert "network" in msg  # points at the escape-hatch marker
