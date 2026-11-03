"""domain.shutdown() only asks the guest to power off. stop_server then reported SHUTOFF at once, and if the power-state loop
ran in the seconds before the guest was off it saw a running domain under a SHUTOFF record, set ACTIVE, and one pass later
auto-recovery found the domain off under an ACTIVE record and started it: the server the user had just stopped came back.
The worker now reports SHUTOFF when the domain is off, and destroys a guest that ignores the request."""
import pytest

pytest.importorskip("fastapi")

from services.compute_worker.app import libvirt_driver  # noqa: E402


class FakeDomain:
    def __init__(self, events, active=True, off_after_polls=3):
        self.events, self._active, self._polls_left = events, active, off_after_polls

    def isActive(self):
        if self._active and self._polls_left is not None:
            if self._polls_left == 0:
                self._active = False
            else:
                self._polls_left -= 1
        return self._active

    def shutdown(self):
        self.events.append("shutdown")

    def destroy(self):
        self._active = False
        self.events.append("destroy")


class FakeConn:
    def __init__(self, domain):
        self._domain = domain

    def lookupByName(self, name):
        return self._domain

    def close(self):
        return None


@pytest.fixture
def host(monkeypatch):
    events = []
    clock = {"now": 0.0}

    def use(domain):
        monkeypatch.setattr(libvirt_driver, "connect", lambda: FakeConn(domain))

    def sleep(seconds):
        clock["now"] += seconds
        events.append(("slept", seconds))

    monkeypatch.setattr("time.sleep", sleep)
    monkeypatch.setattr("time.monotonic", lambda: clock["now"])
    return events, use


def test_stop_returns_only_when_the_domain_is_off(host):
    events, use = host
    domain = FakeDomain(events, off_after_polls=3)
    use(domain)

    libvirt_driver.stop("mini-os-vm-1")

    assert domain._active is False and "destroy" not in events
    assert events[0] == "shutdown" and any(e[0] == "slept" for e in events if isinstance(e, tuple))


def test_a_guest_that_ignores_the_shutdown_is_destroyed_after_the_wait(host, monkeypatch):
    events, use = host
    monkeypatch.setattr(libvirt_driver, "STOP_SHUTDOWN_WAIT", 20)
    domain = FakeDomain(events, off_after_polls=None)  # never powers off by itself
    use(domain)

    libvirt_driver.stop("mini-os-vm-1")

    assert events.index("shutdown") < events.index("destroy")
    assert sum(e[1] for e in events if isinstance(e, tuple)) >= 20
    assert domain._active is False


def test_a_domain_that_is_already_off_is_left_alone(host):
    events, use = host
    use(FakeDomain(events, active=False))

    libvirt_driver.stop("mini-os-vm-1")

    assert events == []
