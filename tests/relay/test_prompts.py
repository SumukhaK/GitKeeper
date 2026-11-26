from pathlib import Path

import pytest

from gitkeeper.relay.prompts import (
    CiFailure,
    changes_prompt,
    ci_fix_prompt,
    task_prompt,
)

PROMPTS = Path(__file__).parents[2] / "prompts"


def test_changes_prompt_fills_real_template_and_preserves_unknown_placeholders() -> None:
    template = (
        (PROMPTS / "changes.md")
        .read_text(encoding="utf-8")
        .replace(
            "PR #<PR_NUMBER> (branch `<BRANCH>`)",
            "PR #<PR_NUMBER> on branch `<BRANCH>` <UNCHANGED>",
        )
    )
    worker_prompt = "1. Fix the parser.\n2. Run the tests."

    result = changes_prompt(
        template,
        pr_number=79,
        branch="feature/r5-review-packet-builder",
        worker_prompt=worker_prompt,
    )

    assert "PR #79 on branch `feature/r5-review-packet-builder` <UNCHANGED>" in result
    assert "1. Check out `feature/r5-review-packet-builder`" in result
    assert f"\n\n{worker_prompt}\n\nDo this:" in result
    assert "<PR_NUMBER>" not in result
    assert "<BRANCH>" not in result
    assert "<WORKER PROMPT from the reviewer's verdict block>" not in result
    assert "<UNCHANGED>" in result


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("template", ""),
        ("pr_number", None),
        ("branch", ""),
        ("worker_prompt", ""),
    ],
)
def test_changes_prompt_rejects_missing_required_inputs(field: str, value: object) -> None:
    arguments = {
        "template": "PR #<PR_NUMBER> branch <BRANCH>\n<WORKER PROMPT from reviewer>",
        "pr_number": 59,
        "branch": "feature/r6",
        "worker_prompt": "Fix it.",
    }
    arguments[field] = value

    with pytest.raises(ValueError, match=field):
        changes_prompt(**arguments)


def test_changes_prompt_leaves_worker_line_untouched_when_template_omits_it() -> None:
    result = changes_prompt(
        "Review PR #<PR_NUMBER> on <BRANCH>.",
        pr_number=59,
        branch="feature/r6",
        worker_prompt="Fix it.",
    )

    assert result == "Review PR #59 on feature/r6."


def test_task_prompt_fills_real_template_and_preserves_unknown_placeholders() -> None:
    template = (PROMPTS / "task.md").read_text(encoding="utf-8")
    issue_body = "Build the prompts.\n\n- Preserve ordering."

    result = task_prompt(
        template,
        issue_number=59,
        issue_title="R6: Worker prompt builders",
        issue_body=issue_body,
    )

    assert "issue #59: R6: Worker prompt builders" in result
    assert f"{issue_body}\n\nDo this, in order:" in result
    assert "<feature|bugfix|upkeep>" in result
    assert "Closes #59" in result
    assert "<ISSUE_NUMBER>" not in result
    assert "<ISSUE_TITLE>" not in result
    assert (
        "<ISSUE_BODY — goal, acceptance criteria, files likely to change, expected proof>"
        not in result
    )


def test_task_prompt_preserves_similarly_prefixed_unknown_placeholder() -> None:
    template = (
        "#<ISSUE_NUMBER>: <ISSUE_TITLE>\n"
        "<ISSUE_BODY — goal, acceptance criteria, files likely to change, expected proof>\n"
        "<ISSUE_BODY — unexpected>"
    )

    result = task_prompt(
        template,
        issue_number=59,
        issue_title="R6",
        issue_body="Build the prompts.",
    )

    assert "<ISSUE_BODY — unexpected>" in result
    assert (
        "<ISSUE_BODY — goal, acceptance criteria, files likely to change, expected proof>"
        not in result
    )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("template", ""),
        ("issue_number", None),
        ("issue_title", ""),
        ("issue_body", ""),
    ],
)
def test_task_prompt_rejects_missing_required_inputs(field: str, value: object) -> None:
    arguments = {
        "template": "Issue #<ISSUE_NUMBER>: <ISSUE_TITLE>\n<ISSUE_BODY — goal>",
        "issue_number": 59,
        "issue_title": "R6",
        "issue_body": "Build prompts.",
    }
    arguments[field] = value

    with pytest.raises(ValueError, match=field):
        task_prompt(**arguments)


def test_ci_fix_prompt_lists_failures_with_annotations_and_summaries() -> None:
    prompt = ci_fix_prompt(
        pr_number=59,
        branch="feature/r6-worker-prompt-builders-changes-ci",
        failures=(
            CiFailure(
                check_name="Python checks",
                annotations=("test_prompts.py:1: expected True", "another failure"),
                summary="The prompt builder is missing.",
            ),
            CiFailure(check_name="Ruff", annotations=(), summary="Lint failed."),
        ),
        attempt=2,
        max_attempts=3,
    )

    assert "PR #59" in prompt
    assert "branch `feature/r6-worker-prompt-builders-changes-ci`" in prompt
    assert "attempt 2 of 3" in prompt
    assert "### Python checks" in prompt
    assert "test_prompts.py:1: expected True" in prompt
    assert "another failure" in prompt
    assert "The prompt builder is missing." in prompt
    assert "### Ruff" in prompt
    assert "Lint failed." in prompt
    assert prompt.index("### Python checks") < prompt.index("### Ruff")
    assert "reproduce" in prompt.lower()
    assert "test" in prompt.lower()
    assert "push to the same branch" in prompt.lower()
    assert "stop" in prompt.lower()
    assert prompt.rstrip().endswith("**Stop.**")


def test_ci_fix_prompt_caps_each_check_at_twenty_annotations() -> None:
    annotations = tuple(f"annotation {number}" for number in range(25))

    prompt = ci_fix_prompt(
        pr_number=59,
        branch="feature/r6",
        failures=(CiFailure("Python checks", annotations, "Fix the tests."),),
        attempt=1,
        max_attempts=3,
    )

    for number in range(20):
        assert f"annotation {number}" in prompt
    for number in range(20, 25):
        assert f"annotation {number}" not in prompt


def test_ci_fix_prompt_caps_annotation_multiline_text_at_twenty_lines() -> None:
    annotations = ("\n".join(f"line {number}" for number in range(25)),)

    prompt = ci_fix_prompt(
        pr_number=59,
        branch="feature/r6",
        failures=(CiFailure("Python checks", annotations, "Fix the tests."),),
        attempt=1,
        max_attempts=3,
    )

    for number in range(20):
        assert f"line {number}" in prompt
    for number in range(20, 25):
        assert f"line {number}" not in prompt


def test_ci_fix_prompt_caps_each_summary_at_four_thousand_characters() -> None:
    summary = "s" * 4_001

    prompt = ci_fix_prompt(
        pr_number=59,
        branch="feature/r6",
        failures=(CiFailure("Python checks", (), summary),),
        attempt=1,
        max_attempts=3,
    )

    assert "s" * 4_000 in prompt
    assert "s" * 4_001 not in prompt


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("pr_number", None),
        ("branch", ""),
        ("failures", ()),
        ("attempt", None),
        ("max_attempts", None),
    ],
)
def test_ci_fix_prompt_rejects_missing_required_inputs(field: str, value: object) -> None:
    arguments = {
        "pr_number": 59,
        "branch": "feature/r6",
        "failures": (CiFailure("Python checks", (), "Fix the tests."),),
        "attempt": 1,
        "max_attempts": 3,
    }
    arguments[field] = value

    with pytest.raises(ValueError, match=field):
        ci_fix_prompt(**arguments)


@pytest.mark.parametrize(
    ("check_name", "annotations", "summary"),
    [
        ("", (), "Failed."),
        ("Python checks", (None,), "Failed."),
        ("Python checks", (), ""),
    ],
)
def test_ci_fix_prompt_rejects_missing_failure_fields(
    check_name: str, annotations: tuple[object, ...], summary: str
) -> None:
    with pytest.raises(ValueError):
        CiFailure(check_name, annotations, summary)
