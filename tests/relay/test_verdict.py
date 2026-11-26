from dataclasses import FrozenInstanceError

import pytest
from gitkeeper.relay.verdict import Verdict, VerdictError, parse_verdict


def test_verdict_is_frozen() -> None:
    verdict = Verdict(approved=True, worker_prompt=None)
    with pytest.raises(FrozenInstanceError):
        verdict.approved = False


@pytest.mark.parametrize(
    "text",
    [
        "VERDICT: APPROVE",
        " \n\tVERDICT:   APPROVE  \n\t",
        "```\nVERDICT: APPROVE\n```",
        "```text\nVERDICT: APPROVE\n```\n",
        "Review complete.\r\n\r\nVERDICT: APPROVE\r\n",
    ],
)
def test_accepts_approve_with_optional_fence_and_whitespace(text: str) -> None:
    assert parse_verdict(text) == Verdict(approved=True, worker_prompt=None)


def test_real_review_can_mention_approve_in_prose() -> None:
    text = """## Findings

The validation looks correct. I would APPROVE this after the documentation typo is fixed.

VERDICT: APPROVE"""
    assert parse_verdict(text) == Verdict(approved=True, worker_prompt=None)


@pytest.mark.parametrize(
    ("text", "expected_prompt"),
    [
        (
            "VERDICT: CHANGES_REQUESTED\nWORKER PROMPT:\n1. Fix the parser.\n2. Run tests.",
            "1. Fix the parser.\n2. Run tests.",
        ),
        (
            "```\nVERDICT: CHANGES_REQUESTED\nWORKER PROMPT:\n1. Fix it.\n\n```",
            "1. Fix it.",
        ),
        (
            " \nVERDICT: CHANGES_REQUESTED\r\n\r\n  WORKER PROMPT:  \r\n"
            "1. Preserve indentation.\r\n    nested = True\r\n2. Verify.\r\n",
            "1. Preserve indentation.\n    nested = True\n2. Verify.",
        ),
    ],
)
def test_accepts_multiline_changes_requested_prompt(text: str, expected_prompt: str) -> None:
    assert parse_verdict(text) == Verdict(approved=False, worker_prompt=expected_prompt)


def test_unfenced_worker_prompt_can_contain_a_code_fence() -> None:
    text = """VERDICT: CHANGES_REQUESTED
WORKER PROMPT:
1. Add this example:
```python
print("hello")
```
2. Run the tests."""
    assert parse_verdict(text) == Verdict(
        approved=False,
        worker_prompt='1. Add this example:\n```python\nprint("hello")\n```\n2. Run the tests.',
    )


def test_uses_last_identical_changes_requested_verdict() -> None:
    text = """VERDICT: CHANGES_REQUESTED
WORKER PROMPT:
Old instructions.
VERDICT: CHANGES_REQUESTED
WORKER PROMPT:
New instructions."""
    assert parse_verdict(text) == Verdict(approved=False, worker_prompt="New instructions.")


@pytest.mark.parametrize("text", ["", "The reviewer may APPROVE this.", "verdict: APPROVE"])
def test_rejects_replies_without_a_verdict_line(text: str) -> None:
    with pytest.raises(VerdictError, match="^No verdict found\\.$"):
        parse_verdict(text)


@pytest.mark.parametrize(
    "text",
    [
        "VERDICT:",
        "VERDICT: approve",
        "VERDICT: UNKNOWN",
        "VERDICT: APPROVE extra",
        "VERDICT: CHANGES_REQUESTED extra",
    ],
)
def test_rejects_unknown_verdict_values(text: str) -> None:
    with pytest.raises(VerdictError, match="^Verdict value is unknown\\.$"):
        parse_verdict(text)


@pytest.mark.parametrize(
    "text",
    [
        "VERDICT: APPROVE\nVERDICT: CHANGES_REQUESTED\nWORKER PROMPT:\nFix it.",
        "VERDICT: CHANGES_REQUESTED\nWORKER PROMPT:\nFix it.\nVERDICT: APPROVE",
    ],
)
def test_rejects_conflicting_verdict_values(text: str) -> None:
    with pytest.raises(VerdictError, match="^Verdict values conflict\\.$"):
        parse_verdict(text)


@pytest.mark.parametrize(
    "text",
    [
        "VERDICT: CHANGES_REQUESTED",
        "VERDICT: CHANGES_REQUESTED\nWORKER PROMPT:",
        "VERDICT: CHANGES_REQUESTED\nWORKER PROMPT:\n \n\t",
        "```\nVERDICT: CHANGES_REQUESTED\nWORKER PROMPT:\n```",
        "VERDICT: CHANGES_REQUESTED\nworker prompt:\nFix it.",
        "VERDICT: CHANGES_REQUESTED\nWORKER PROMPT: Fix it inline.",
        "VERDICT: CHANGES_REQUESTED\nNot a prompt header.\nWORKER PROMPT:\nFix it.",
    ],
)
def test_rejects_missing_or_empty_worker_prompt(text: str) -> None:
    with pytest.raises(VerdictError, match="^Changes requested requires a worker prompt\\.$"):
        parse_verdict(text)


@pytest.mark.parametrize(
    "text",
    [
        "VERDICT: APPROVE\nMore text.",
        "```\nVERDICT: APPROVE\nMore text.\n```",
        "```\nVERDICT: APPROVE\n```\nMore text.",
    ],
)
def test_rejects_text_after_approve(text: str) -> None:
    with pytest.raises(VerdictError, match="^Approval must be the final text\\.$"):
        parse_verdict(text)


def test_approval_uses_the_last_identical_verdict_line() -> None:
    assert parse_verdict("VERDICT: APPROVE\nVERDICT: APPROVE") == Verdict(True, None)


@pytest.mark.parametrize(
    "text",
    [
        "VERDICT: private-review-value",
        "VERDICT: APPROVE\nprivate-review-text",
        "VERDICT: CHANGES_REQUESTED\nWORKER PROMPT:\nprivate-review-prompt",
        "VERDICT: APPROVE\nVERDICT: private-review-value",
    ],
)
def test_errors_never_echo_review_text(text: str) -> None:
    with pytest.raises(VerdictError) as caught:
        parse_verdict(text)
    assert "private-review" not in str(caught.value)
