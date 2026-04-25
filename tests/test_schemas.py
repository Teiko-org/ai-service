import pytest
from pydantic import ValidationError

from app.models.schemas import AskRequest, InsightRequest


def test_ask_request_valid():
    req = AskRequest(question="Qual o produto mais vendido?")
    assert req.question == "Qual o produto mais vendido?"
    assert req.session_id is None


def test_ask_request_too_short():
    with pytest.raises(ValidationError):
        AskRequest(question="")


def test_ask_request_single_char_valid():
    req = AskRequest(question="X")
    assert req.question == "X"


def test_ask_request_with_session():
    req = AskRequest(question="Oi", session_id="abc-123")
    assert req.session_id == "abc-123"


def test_ask_request_too_long():
    with pytest.raises(ValidationError):
        AskRequest(question="a" * 1001)


def test_insight_request_default():
    req = InsightRequest()
    assert req.context == "dashboard_main"


def test_insight_request_custom():
    req = InsightRequest(context="production")
    assert req.context == "production"
