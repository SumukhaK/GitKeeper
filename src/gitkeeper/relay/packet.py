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


def _truncate_diff(diff: str, max_chars: int, url: str) -> tuple[str, str | None]:
    if len(diff) <= max_chars:
        return diff, None

    shown = diff[:max_chars]
    line_boundary = shown.rfind("\n") + 1
    if line_boundary:
        shown = shown[:line_boundary].rstrip("\n")
    notice = (
        f"[diff truncated: {len(shown)} of {len(diff)} characters shown — "
        f"review the rest on GitHub: {url}/files]"
    )
    return shown, notice


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
    shown_diff, truncation_notice = _truncate_diff(diff, max_diff_chars, pr.url)
    diff_text = f"## Diff\n```diff\n{shown_diff}\n```"
    if truncation_notice is not None:
        diff_text += f"\n{truncation_notice}"
    packet = "\n\n".join((issue_text, pr_text, checks_text, diff_text))

    placeholder = "<REVIEW PACKET>"
    if placeholder in review_prompt:
        return review_prompt.replace(placeholder, packet)
    separator = "\n" if review_prompt.endswith("\n") else "\n\n"
    return review_prompt + separator + packet
