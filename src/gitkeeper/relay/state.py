"""Pure pull-request relay state machine."""

from dataclasses import dataclass, replace
from enum import Enum
from typing import Literal

MAX_CI_ATTEMPTS = 3
MAX_REVIEW_ROUNDS = 3


class Phase(str, Enum):
    ci_pending = "ci_pending"
    ci_failed = "ci_failed"
    review_needed = "review_needed"
    awaiting_verdict = "awaiting_verdict"
    changes_requested = "changes_requested"
    ready_to_merge = "ready_to_merge"
    blocked = "blocked"
    done = "done"


@dataclass(frozen=True)
class PrRecord:
    repository: str
    number: int
    head_sha: str
    phase: Phase
    review_rounds: int
    ci_attempts: int
    reviewed_sha: str | None
    blocked_reason: str | None


@dataclass(frozen=True)
class PrFacts:
    head_sha: str
    is_open: bool
    is_merged: bool
    is_draft: bool
    checks: Literal["pending", "passed", "failed", "infra"]
    repository: str = ""
    number: int = 0


def _new_record(facts: PrFacts, phase: Phase) -> PrRecord:
    return PrRecord(
        repository=facts.repository,
        number=facts.number,
        head_sha=facts.head_sha,
        phase=phase,
        review_rounds=0,
        ci_attempts=0,
        reviewed_sha=None,
        blocked_reason=None,
    )


def _ci_failure(record: PrRecord) -> PrRecord:
    if record.ci_attempts >= MAX_CI_ATTEMPTS:
        return replace(record, phase=Phase.blocked, blocked_reason="ci_attempts_exhausted")
    return replace(
        record,
        phase=Phase.ci_failed,
        ci_attempts=record.ci_attempts + 1,
        blocked_reason=None,
    )


def _from_checks(record: PrRecord, checks: str) -> PrRecord:
    if checks == "infra":
        return replace(record, phase=Phase.blocked, blocked_reason="ci_infrastructure")
    if checks == "failed":
        return _ci_failure(record)
    phase = Phase.ci_pending if checks == "pending" else Phase.review_needed
    return replace(record, phase=phase, blocked_reason=None)


def advance(record: PrRecord | None, facts: PrFacts) -> PrRecord:
    if facts.is_merged or not facts.is_open:
        if record is None:
            return _new_record(facts, Phase.done)
        return replace(record, phase=Phase.done)
    if record is None:
        return _from_checks(_new_record(facts, Phase.ci_pending), facts.checks)
    if record.phase is Phase.done:
        return record
    if facts.head_sha != record.head_sha:
        current = replace(record, head_sha=facts.head_sha, reviewed_sha=None)
        if current.phase is Phase.blocked and facts.checks != "infra":
            return current
        return _from_checks(current, facts.checks)
    if facts.checks == "infra":
        return replace(record, phase=Phase.blocked, blocked_reason="ci_infrastructure")
    if record.phase is Phase.ci_pending:
        return _from_checks(record, facts.checks)
    return record


def packet_copied(record: PrRecord) -> PrRecord:
    if record.phase not in (Phase.review_needed, Phase.awaiting_verdict):
        raise ValueError(f"packet copy is not allowed from {record.phase.value}")
    return replace(record, phase=Phase.awaiting_verdict)


def verdict_recorded(record: PrRecord, approved: bool) -> PrRecord:
    if record.phase is not Phase.awaiting_verdict:
        raise ValueError(f"verdict is not allowed from {record.phase.value}")
    if approved:
        return replace(record, phase=Phase.ready_to_merge, reviewed_sha=record.head_sha)
    rounds = record.review_rounds + 1
    if rounds >= MAX_REVIEW_ROUNDS:
        return replace(
            record,
            phase=Phase.blocked,
            review_rounds=rounds,
            reviewed_sha=None,
            blocked_reason="review_rounds_exhausted",
        )
    return replace(
        record,
        phase=Phase.changes_requested,
        review_rounds=rounds,
        reviewed_sha=None,
    )


def owner_reset(record: PrRecord) -> PrRecord:
    if record.phase is not Phase.blocked:
        raise ValueError(f"owner reset is not allowed from {record.phase.value}")
    return replace(
        record,
        phase=Phase.ci_pending,
        review_rounds=0,
        ci_attempts=0,
        reviewed_sha=None,
        blocked_reason=None,
    )
