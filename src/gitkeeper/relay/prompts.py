"""Build ready-to-copy worker prompts from already-fetched repository data."""

from dataclasses import dataclass


@dataclass(frozen=True)
class CiFailure:
    check_name: str
    annotations: tuple[str, ...]
    summary: str

    def __post_init__(self) -> None:
        if not self.check_name.strip():
            raise ValueError("check_name is required")
        if any(
            not isinstance(annotation, str) or not annotation.strip()
            for annotation in self.annotations
        ):
            raise ValueError("annotations must contain non-empty strings")
        if not self.summary.strip():
            raise ValueError("summary is required")


def _require(name: str, value: object) -> None:
    if value is None or (isinstance(value, str) and not value.strip()) or value == ():
        raise ValueError(f"{name} is required")


def changes_prompt(
    template: str,
    *,
    pr_number: int,
    branch: str,
    worker_prompt: str,
) -> str:
    _require("template", template)
    _require("pr_number", pr_number)
    _require("branch", branch)
    _require("worker_prompt", worker_prompt)
    result = template.replace("<PR_NUMBER>", str(pr_number)).replace("<BRANCH>", branch)
    return result.replace("<WORKER PROMPT from the reviewer's verdict block>", worker_prompt)


def _failure_section(failure: CiFailure) -> str:
    annotation_lines = [
        line for annotation in failure.annotations for line in annotation.splitlines()
    ]
    if len(annotation_lines) > 20:
        annotation_lines = annotation_lines[:20]
    annotations = "\n".join(f"- {line}" for line in annotation_lines)
    summary = failure.summary[:4_000]
    return f"### {failure.check_name}\n{annotations}\n\nSummary:\n{summary}"


def ci_fix_prompt(
    *,
    pr_number: int,
    branch: str,
    failures: tuple[CiFailure, ...],
    attempt: int,
    max_attempts: int,
) -> str:
    _require("pr_number", pr_number)
    _require("branch", branch)
    _require("failures", failures)
    _require("attempt", attempt)
    _require("max_attempts", max_attempts)
    failure_text = "\n\n".join(_failure_section(failure) for failure in failures)
    return (
        f"You are a worker agent on PR #{pr_number}, branch `{branch}`. "
        f"This is CI-fix attempt {attempt} of {max_attempts}.\n\n"
        f"Fix the failing checks below:\n\n{failure_text}\n\n"
        "First reproduce the failure with a test, then make the least fix that passes it. "
        "Run the full test suite and linters. Update the PR's Verification section with "
        "fresh proof, push to the same branch, and reply on the PR point by point. "
        "Do not create a new branch or PR. **Stop.**"
    )


def task_prompt(
    template: str,
    *,
    issue_number: int,
    issue_title: str,
    issue_body: str,
) -> str:
    _require("template", template)
    _require("issue_number", issue_number)
    _require("issue_title", issue_title)
    _require("issue_body", issue_body)
    result = (
        template.replace("<ISSUE_NUMBER>", str(issue_number))
        .replace("<ISSUE_TITLE>", issue_title)
        .replace("<ISSUE_BODY>", issue_body)
    )
    return result.replace(
        "<ISSUE_BODY — goal, acceptance criteria, files likely to change, expected proof>",
        issue_body,
    )
