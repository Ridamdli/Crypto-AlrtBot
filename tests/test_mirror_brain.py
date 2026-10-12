"""Brain unit tests: sizing matrix. Pure logic, no I/O."""
from mirror.brain import decide_size


def test_full_mimic_when_bank_healthy():
    size, reason = decide_size(bank=100.0, open_count=2)
    assert size == 5.0 and reason == "full-mimic"


def test_shrinks_to_fit_when_bank_tight():
    size, reason = decide_size(bank=10.0, open_count=0)
    assert size == 2.5 and reason.startswith("shrunk-to-fit")


def test_skips_on_max_open():
    size, reason = decide_size(bank=100.0, open_count=10)
    assert size is None and reason.startswith("skip:max-open")


def test_skips_on_empty_bank():
    size, reason = decide_size(bank=0.0, open_count=0)
    assert size is None and "bank-empty" in reason


def test_never_trades_blind_without_bank():
    size, reason = decide_size(bank=None, open_count=0)
    assert size is None and "bank-unknown" in reason


def test_custom_params_respected():
    size, reason = decide_size(bank=100.0, open_count=1, base_size=8.0,
                               max_open=5, max_risk_pct=0.10)
    assert size == 8.0 and reason == "full-mimic"
    size2, _ = decide_size(bank=100.0, open_count=5, base_size=8.0,
                           max_open=5, max_risk_pct=0.10)
    assert size2 is None
