from uigen.config import tokens
from uigen.contrast import check_tokens, ratio


def test_known_ratios():
    assert ratio("#FFFFFF", "#000000") == 21.0
    assert ratio("#FFFFFF", "#16A34A") == 3.3  # the old success colour: fails AA


def test_design_tokens_meet_wcag_aa():
    assert check_tokens() == [], "fix config/design_tokens.json colours"


def test_a_bad_token_is_caught():
    colors = dict(tokens()["colors"], warning="#FBBF24")
    failures = check_tokens(colors)
    assert any(f.startswith("warning badge: #FFFFFF on warning") for f in failures)
