from dataclasses import FrozenInstanceError

import pytest

from gitkeeper.relay.state import (
    MAX_CI_ATTEMPTS,
    MAX_REVIEW_ROUNDS,
    Phase,
    PrFacts,
    PrRecord,
    advance,
    owner_reset,
    packet_copied,
    verdict_recorded,
)


def record(
    phase: Phase = Phase.ci_pending,
    *,
    head_sha: str = "old",
    review_rounds: int = 0,
    ci_attempts: int = 0,
    reviewed_sha: str | None = None,
    blocked_reason: str | None = None,
) -> PrRecord:
    return PrRecord(
        repository="SumukhaK/GitKeeper",
        number=56,
        head_sha=head_sha,
        phase=phase,
        review_rounds=review_rounds,
        ci_attempts=ci_attempts,
        reviewed_sha=reviewed_sha,
        blocked_reason=blocked_reason,
    )


def facts(
    checks: str = "pending",
    *,
    head_sha: str = "old",
    is_open: bool = True,
    is_merged: bool = False,
    is_draft: bool = False,
) -> PrFacts:
    return PrFacts(
        repository="SumukhaK/GitKeeper",
        number=56,
        head_sha=head_sha,
        is_open=is_open,
        is_merged=is_merged,
        is_draft=is_draft,
        checks=checks,
    )


def test_records_are_frozen() -> None:
    with pytest.raises(FrozenInstanceError):
        record().phase = Phase.done
    with pytest.raises(FrozenInstanceError):
        facts().checks = "passed"


def test_first_seen_accepts_issue_fact_shape() -> None:
    minimal = PrFacts("old", True, False, False, "passed")
    assert advance(None, minimal).phase is Phase.review_needed


@pytest.mark.parametrize("current", [None, record(Phase.ready_to_merge)])
@pytest.mark.parametrize("closed", [{"is_open": False}, {"is_merged": True}])
def test_merged_or_closed_pr_is_done(current: PrRecord | None, closed: dict[str, bool]) -> None:
    assert advance(current, facts("passed", **closed)).phase is Phase.done


@pytest.mark.parametrize(
    ("checks", "phase", "attempts"),
    [("pending", Phase.ci_pending, 0), ("passed", Phase.review_needed, 0)],
)
def test_first_seen_pr_without_failure(checks: str, phase: Phase, attempts: int) -> None:
    result = advance(None, facts(checks))
    assert (result.phase, result.ci_attempts) == (phase, attempts)
    assert (result.repository, result.number) == ("SumukhaK/GitKeeper", 56)


def test_first_seen_ci_failure_counts_first_attempt() -> None:
    result = advance(None, facts("failed"))
    assert (result.phase, result.ci_attempts) == (Phase.ci_failed, 1)


def test_first_seen_infra_failure_blocks() -> None:
    result = advance(None, facts("infra"))
    assert result.phase is Phase.blocked
    assert result.blocked_reason == "ci_infrastructure"


def test_ci_pending_pass_moves_to_review() -> None:
    assert advance(record(), facts("passed")).phase is Phase.review_needed


def test_ci_pending_failure_counts_attempt() -> None:
    result = advance(record(ci_attempts=1), facts("failed"))
    assert (result.phase, result.ci_attempts) == (Phase.ci_failed, 2)


def test_ci_pending_stays_pending_while_checks_run() -> None:
    assert advance(record(), facts("pending")) == record()


def test_fourth_ci_failure_blocks_without_exceeding_cap() -> None:
    result = advance(record(ci_attempts=MAX_CI_ATTEMPTS), facts("failed"))
    assert result.phase is Phase.blocked
    assert result.ci_attempts == MAX_CI_ATTEMPTS
    assert result.blocked_reason == "ci_attempts_exhausted"


@pytest.mark.parametrize("phase", [phase for phase in Phase if phase is not Phase.done])
def test_infra_blocks_every_active_phase(phase: Phase) -> None:
    result = advance(record(phase), facts("infra"))
    assert result.phase is Phase.blocked
    assert result.blocked_reason == "ci_infrastructure"


@pytest.mark.parametrize("phase", [Phase.review_needed, Phase.awaiting_verdict])
def test_packet_copy_enters_verdict_wait(phase: Phase) -> None:
    assert packet_copied(record(phase)).phase is Phase.awaiting_verdict


def test_approval_pins_reviewed_head() -> None:
    result = verdict_recorded(record(Phase.awaiting_verdict), approved=True)
    assert result.phase is Phase.ready_to_merge
    assert result.reviewed_sha == "old"


@pytest.mark.parametrize("rounds", [0, 1])
def test_changes_requested_counts_review_round(rounds: int) -> None:
    result = verdict_recorded(record(Phase.awaiting_verdict, review_rounds=rounds), False)
    assert result.phase is Phase.changes_requested
    assert result.review_rounds == rounds + 1


def test_third_changes_requested_blocks() -> None:
    result = verdict_recorded(
        record(Phase.awaiting_verdict, review_rounds=MAX_REVIEW_ROUNDS - 1), False
    )
    assert result.phase is Phase.blocked
    assert result.review_rounds == MAX_REVIEW_ROUNDS
    assert result.blocked_reason == "review_rounds_exhausted"


@pytest.mark.parametrize(
    "phase",
    [
        Phase.changes_requested,
        Phase.ci_failed,
        Phase.ci_pending,
        Phase.ready_to_merge,
        Phase.review_needed,
        Phase.awaiting_verdict,
    ],
)
@pytest.mark.parametrize(
    ("checks", "expected"),
    [("pending", Phase.ci_pending), ("passed", Phase.review_needed), ("failed", Phase.ci_failed)],
)
def test_new_head_recomputes_active_phase(phase: Phase, checks: str, expected: Phase) -> None:
    result = advance(
        record(phase, ci_attempts=1, reviewed_sha="old"), facts(checks, head_sha="new")
    )
    assert result.head_sha == "new"
    assert result.phase is expected
    assert result.reviewed_sha is None
    assert result.ci_attempts == (2 if checks == "failed" else 1)


def test_new_head_failure_applies_ci_cap() -> None:
    result = advance(
        record(Phase.ci_failed, ci_attempts=MAX_CI_ATTEMPTS),
        facts("failed", head_sha="new"),
    )
    assert result.phase is Phase.blocked
    assert result.blocked_reason == "ci_attempts_exhausted"
    assert result.ci_attempts == MAX_CI_ATTEMPTS


def test_new_head_keeps_blocked_pr_blocked() -> None:
    result = advance(
        record(Phase.blocked, blocked_reason="ci_infrastructure", reviewed_sha="old"),
        facts("passed", head_sha="new"),
    )
    assert result.phase is Phase.blocked
    assert result.head_sha == "new"
    assert result.reviewed_sha is None
    assert result.blocked_reason == "ci_infrastructure"


def test_owner_reset_restarts_blocked_pr() -> None:
    result = owner_reset(
        record(
            Phase.blocked,
            review_rounds=2,
            ci_attempts=3,
            reviewed_sha="old",
            blocked_reason="ci_attempts_exhausted",
        )
    )
    assert result.phase is Phase.ci_pending
    assert (result.review_rounds, result.ci_attempts) == (0, 0)
    assert result.reviewed_sha is None
    assert result.blocked_reason is None


def test_repeated_failed_poll_does_not_double_count_attempt() -> None:
    current = record(Phase.ci_failed, ci_attempts=1)
    assert advance(current, facts("failed")) is current


@pytest.mark.parametrize(
    "phase",
    [phase for phase in Phase if phase not in (Phase.review_needed, Phase.awaiting_verdict)],
)
def test_packet_copy_rejects_other_phases(phase: Phase) -> None:
    with pytest.raises(ValueError):
        packet_copied(record(phase))


@pytest.mark.parametrize("phase", [phase for phase in Phase if phase is not Phase.awaiting_verdict])
def test_verdict_rejects_other_phases(phase: Phase) -> None:
    with pytest.raises(ValueError):
        verdict_recorded(record(phase), approved=True)


@pytest.mark.parametrize("phase", [phase for phase in Phase if phase is not Phase.blocked])
def test_owner_reset_rejects_other_phases(phase: Phase) -> None:
    with pytest.raises(ValueError):
        owner_reset(record(phase))


def test_done_is_terminal_until_a_new_record_is_created() -> None:
    current = record(Phase.done)
    assert advance(current, facts("passed", head_sha="new")) is current


def test_new_head_invalidates_approval_without_merge_action() -> None:
    current = record(Phase.ready_to_merge, reviewed_sha="old")
    result = advance(current, facts("passed", head_sha="new"))
    assert result.phase is Phase.review_needed
    assert result.reviewed_sha is None
