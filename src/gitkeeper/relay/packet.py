"""Build a deterministic review packet from already-fetched GitHub data."""

from dataclasses import dataclass


@dataclass(frozen=True)
class PacketPr:
    number: int
    title: str
    body: str
    head_sha: str
    url: str


@dataclass(frozen=True)
class PacketIssue:
    number: int
    title: str
    body: str


def _issue_section(issue: PacketIssue | None) -> str:
    if issue is None:
        return "## Issue\n_No linked issue — check the PR body._"
    return f"## Issue #{issue.number}: {issue.title}\n{issue.body}"


def _truncate_diff(diff: str, max_chars: int, url: str) -> str:
    if len(diff) <= max_chars:
        return diff

    shown = diff[:max_chars]
    line_boundary = shown.rfind("\n") + 1
    if line_boundary:
        shown = shown[:line_boundary].rstrip("\n")
    notice = (
        f"[diff truncated: {len(shown)} of {len(diff)} characters shown — "
        f"review the rest on GitHub: {url}/files]"
    )
    return f"{shown}\n{notice}"


def build_review_packet(
    *,
    review_prompt: str,
    repository: str,
    pr: PacketPr,
    issue: PacketIssue | None,
    checks: tuple[tuple[str, str], ...],
    diff: str,
    max_diff_chars: int = 60_000,
) -> str:
    """Insert or append the review packet without fetching or altering source data."""
    issue_text = _issue_section(issue)
    pr_text = f"## Pull request #{pr.number}: {pr.title}\n{pr.url} · head {pr.head_sha}\n{pr.body}"
    checks_text = "## CI results\n" + "\n".join(
        f"- {name}: {conclusion}" for name, conclusion in checks
    )
    diff_text = f"## Diff\n```diff\n{_truncate_diff(diff, max_diff_chars, pr.url)}\n```"
    packet = "\n\n".join((issue_text, pr_text, checks_text, diff_text))

    placeholder = "<REVIEW PACKET>"
    if placeholder in review_prompt:
        return review_prompt.replace(placeholder, packet)
    separator = "\n" if review_prompt.endswith("\n") else "\n\n"
    return review_prompt + separator + packet
