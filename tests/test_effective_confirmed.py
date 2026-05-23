from app.tools.writes._helpers import effective_confirmed


def test_effective_confirmed_false_without_token():
    assert effective_confirmed({"confirmed": True}) is False


def test_effective_confirmed_true_with_token():
    assert effective_confirmed({"confirmed": True, "confirm_token": "abc"}) is True
