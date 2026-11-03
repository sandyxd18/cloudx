"""On a compute node without load-balancer-api (a cloud that does not run load balancing) the worker polled every 5 s and
logged a warning every time, 720 a day and hours of the same line. It says it once, repeats at a long interval while the
API stays away, and says when it is back."""
import logging

import pytest

from services.lb_worker.app import main as lbw


class Clock:
    now = 1000.0

    def __call__(self):
        return self.now


@pytest.fixture
def loop(monkeypatch, caplog):
    clock = Clock()
    outcome = {"fail": True}

    def tick():
        if outcome["fail"]:
            raise ConnectionError("Connection refused")

    monkeypatch.setattr(lbw, "tick", tick)
    caplog.set_level(logging.DEBUG, logger="lb-worker")
    return lbw.TickLoop(clock=clock), clock, outcome, caplog


def _warnings(caplog):
    return [r for r in caplog.records if r.levelno == logging.WARNING]


def test_the_first_failure_is_logged(loop):
    ticker, clock, outcome, caplog = loop

    ticker.step()

    assert len(_warnings(caplog)) == 1 and "Connection refused" in _warnings(caplog)[0].getMessage()


def test_the_same_failure_is_not_logged_again_every_tick(loop):
    ticker, clock, outcome, caplog = loop

    for _ in range(100):
        ticker.step()
        clock.now += 5

    assert len(_warnings(caplog)) == 2, "once at the start, once more after the repeat interval (500 s here)"


def test_a_failure_that_goes_on_is_repeated_after_the_interval(loop):
    ticker, clock, outcome, caplog = loop
    ticker.step()
    clock.now += lbw.WARN_REPEAT_SECONDS - 1
    ticker.step()
    assert len(_warnings(caplog)) == 1

    clock.now += 1
    ticker.step()

    assert len(_warnings(caplog)) == 2 and "still failing" in _warnings(caplog)[1].getMessage()


def test_recovery_is_logged_once_and_the_next_failure_is_warned_again(loop):
    ticker, clock, outcome, caplog = loop
    ticker.step()
    outcome["fail"] = False
    clock.now += 10

    ticker.step()
    ticker.step()

    assert [r.getMessage() for r in caplog.records if r.levelno == logging.INFO and "works again" in r.getMessage()]
    assert len([r for r in caplog.records if "works again" in r.getMessage()]) == 1

    outcome["fail"] = True
    ticker.step()
    assert len(_warnings(caplog)) == 2


def test_every_failure_is_still_visible_at_debug(loop):
    ticker, clock, outcome, caplog = loop

    for _ in range(5):
        ticker.step()

    assert len([r for r in caplog.records if r.levelno == logging.DEBUG and "Connection refused" in r.getMessage()]) >= 4
