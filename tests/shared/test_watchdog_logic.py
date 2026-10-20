from shared.watchdog_logic import decide

D = ["dom1"]


def test_wait_before_timeout():
    assert decide(100, 60, 45, [], True, D) == "wait"


def test_kill_when_a_peer_reaches_ha_api():
    assert decide(106, 60, 45, [False, True], True, D) == "kill"


def test_kill_when_no_peer_answers():
    assert decide(106, 60, 45, [], True, D) == "kill"


def test_hold_when_every_peer_also_lost_ha_api():
    assert decide(106, 60, 45, [False, False], True, D) == "hold"


def test_hold_without_peers_or_domains():
    assert decide(106, 60, 45, [], False, D) == "hold"
    assert decide(106, 60, 45, [True], True, []) == "hold"
