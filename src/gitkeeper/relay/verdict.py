"""Parse the strict verdict block at the end of a reviewer reply."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Verdict:
    approved: bool
    worker_prompt: str | None


class VerdictError(Exception):
    """A reviewer reply does not contain a valid verdict block."""


_VERDICT_VALUES = {"APPROVE", "CHANGES_REQUESTED"}


def _is_fence(line: str) -> bool:
    return line.strip().startswith("```")


def _approval_is_final(lines: list[str], verdict_index: int, in_fence: bool) -> None:
    fence_closed = not in_fence
    for line in lines[verdict_index + 1 :]:
        stripped = line.strip()
        if not stripped:
            continue
        if not fence_closed and stripped == "```":
            fence_closed = True
            continue
        raise VerdictError("Approval must be the final text.")


def _worker_prompt(lines: list[str], verdict_index: int, in_fence: bool) -> str:
    header_index = verdict_index + 1
    while header_index < len(lines) and not lines[header_index].strip():
        header_index += 1
    if header_index >= len(lines) or lines[header_index].strip() != "WORKER PROMPT:":
        raise VerdictError("Changes requested requires a worker prompt.")

    prompt_lines: list[str] = []
    for line in lines[header_index + 1 :]:
        if in_fence and line.strip() == "```":
            break
        prompt_lines.append(line)

    prompt = "\n".join(prompt_lines).strip()
    if not prompt:
        raise VerdictError("Changes requested requires a worker prompt.")
    return prompt


def parse_verdict(text: str) -> Verdict:
    """Return the last verdict, rejecting ambiguous or malformed replies."""
    lines = text.splitlines()
    verdicts: list[tuple[int, str, bool]] = []
    in_fence = False

    for index, line in enumerate(lines):
        stripped = line.strip()
        if _is_fence(line):
            in_fence = not in_fence
            continue
        if not stripped.startswith("VERDICT:"):
            continue
        value = stripped.removeprefix("VERDICT:").strip()
        if value not in _VERDICT_VALUES:
            raise VerdictError("Verdict value is unknown.")
        verdicts.append((index, value, in_fence))

    if not verdicts:
        raise VerdictError("No verdict found.")
    if len({value for _, value, _ in verdicts}) > 1:
        raise VerdictError("Verdict values conflict.")

    verdict_index, value, verdict_in_fence = verdicts[-1]
    if value == "APPROVE":
        _approval_is_final(lines, verdict_index, verdict_in_fence)
        return Verdict(approved=True, worker_prompt=None)
    return Verdict(
        approved=False, worker_prompt=_worker_prompt(lines, verdict_index, verdict_in_fence)
    )
