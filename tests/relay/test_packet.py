from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest
from gitkeeper.relay.packet import PacketIssue, PacketPr, build_review_packet

FIXTURES = Path(__file__).parent / "fixtures"


def _pr() -> PacketPr:
    return PacketPr(
        number=58,
        title="Add review packet builder",
        body="Implements the packet contract.\n\n## Verification\n\nTests pass.",
        head_sha="0123456789abcdef0123456789abcdef01234567",
        url="https://github.com/SumukhaK/GitKeeper/pull/58",
    )


def _kwargs() -> dict[str, object]:
    return {
        "review_prompt": (FIXTURES / "packet_golden.md").read_text(encoding="utf-8"),
        "repository": "SumukhaK/GitKeeper",
        "pr": _pr(),
        "issue": PacketIssue(
            number=58,
            title="R5: Review packet builder",
            body="Build the packet.\n\n- Preserve the diff.",
        ),
        "checks": (("Python checks", "passed"), ("No copy-paste", "passed")),
        "diff": "diff --git a/src/packet.py b/src/packet.py\n+packet = True",
    }


def test_builds_deterministic_golden_review_packet() -> None:
    expected = (FIXTURES / "packet_golden.md").read_text(encoding="utf-8")

    first = build_review_packet(**_kwargs())
    second = build_review_packet(**_kwargs())

    assert first == second == expected
    assert first.index("## Issue") < first.index("## Pull request")
    assert first.index("## Pull request") < first.index("## CI results")
    assert first.index("## CI results") < first.index("## Diff")


def test_renders_missing_issue_placeholder() -> None:
    kwargs = _kwargs()
    kwargs["issue"] = None

    packet = build_review_packet(**kwargs)

    assert "## Issue\n_No linked issue — check the PR body._" in packet


def test_truncates_diff_at_a_complete_line_with_character_counts() -> None:
    diff = "first line\nsecond line\nthird line\nfourth line"
    kwargs = _kwargs()
    kwargs["diff"] = diff
    kwargs["max_diff_chars"] = 22

    packet = build_review_packet(**kwargs)

    assert "```diff\nfirst line\nsecond line\n```" in packet
    assert "third line" not in packet
    assert (
        "[diff truncated: 22 of 46 characters shown — review the rest on GitHub: "
        "https://github.com/SumukhaK/GitKeeper/pull/58/files]"
    ) in packet


def test_does_not_truncate_diff_at_or_below_limit() -> None:
    kwargs = _kwargs()
    kwargs["max_diff_chars"] = 60

    packet = build_review_packet(**kwargs)

    assert "[diff truncated:" not in packet
    assert "```diff\n" + str(kwargs["diff"]) + "\n```" in packet


def test_appends_packet_when_placeholder_is_missing() -> None:
    kwargs = _kwargs()
    kwargs["review_prompt"] = "Review this PR.\n"

    packet = build_review_packet(**kwargs)

    assert packet.startswith("Review this PR.\n")
    assert "## Issue\n## Pull request\n## CI results\n## Diff" in packet.replace(
        "## Issue #58: R5: Review packet builder", "## Issue"
    ).replace("## Pull request #58: Add review packet builder", "## Pull request")
    assert "<REVIEW PACKET>" not in packet


def test_appends_packet_after_existing_text_without_adding_blank_line() -> None:
    kwargs = _kwargs()
    kwargs["review_prompt"] = "Review this PR."

    packet = build_review_packet(**kwargs)

    assert packet.startswith("Review this PR.\n## Issue #58:")


def test_pr_and_issue_records_are_frozen() -> None:
    pr = _pr()
    issue = kwargs_issue = PacketIssue(58, "R5: Review packet builder", "Build the packet.")

    with pytest.raises(FrozenInstanceError):
        pr.title = "Changed"
    with pytest.raises(FrozenInstanceError):
        issue.title = "Changed"
    with pytest.raises(FrozenInstanceError):
        kwargs_issue.number = 99
