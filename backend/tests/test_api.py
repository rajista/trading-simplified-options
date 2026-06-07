from app import main
from test_strategies import chain


def test_group_aliases_do_not_return_unknown_strategy(monkeypatch):
    monkeypatch.setattr(main.provider, "get_chain", lambda expiry: chain(expiry))

    others_one = main.strategies(
        strategy="others-1",
        expiry="09-Jun-2026",
        far_expiry=None,
        limit=2,
    )
    others_two = main.strategies(
        strategy="others-2",
        expiry="09-Jun-2026",
        far_expiry=None,
        limit=2,
    )

    assert others_one.strategy == "jade-lizard"
    assert others_one.candidates
    assert others_two.strategy == "strip"
    assert others_two.candidates
