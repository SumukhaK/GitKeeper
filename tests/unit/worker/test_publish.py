import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

from gitkeeper.policy.patch_scope import RemotePrState
from gitkeeper.queue.models import (
    AttemptOutcome,
    ClaimResult,
    InvocationAttempt,
    InvocationKind,
    Lease,
    QueueKey,
    RepairReason,
)
from gitkeeper.worker.git import GitPushOutcomeUnknown, GitPushRejected
from gitkeeper.worker.models import (
    PublishTarget,
    WorktreeIdentity,
    WorktreeRecord,
)
from gitkeeper.worker.publish import PatchPublisher

_AT = datetime(2026, 11, 26, 12, 0, tzinfo=timezone.utc)
_SHA = "a" * 40
_LOCAL = "b" * 40
_REPOSITORY = "SumukhaK/Setu-mobile"


class FakeGit:
    def __init__(
        self,
        *,
        changes=(SimpleNamespace(path="app/src/main/java/Foo.kt"),),
        commit_sha: str = _LOCAL,
        push_error: Exception | None = None,
        remote: str | None = None,
        unstaged_after: bool = False,
    ) -> None:
        self.changes = changes
        self.commit_sha = commit_sha
        self.push_error = push_error
        self.remote = remote
        self.unstaged_after = unstaged_after
        self.staged = None
        self.commits = []
        self.pushes = []

    def inspect_changes(self, path, starting_sha, *, include_uncommitted):
        if include_uncommitted and self.staged is not None:
            return self.changes, self.unstaged_after
        return self.changes, False

    def stage(self, path, paths):
        self.staged = paths

    def commit(self, path, message):
        self.commits.append(message)
        return self.commit_sha

    def head(self, path):
        return self.commit_sha

    def is_ancestor(self, path, older, newer):
        return True

    def commit_count(self, path, older, newer):
        return 1

    def push(self, path, branch, commit_sha):
        self.pushes.append((branch, commit_sha))
        if self.push_error is not None:
            raise self.push_error

    def remote_head(self, repository, branch):
        return self.remote or self.commit_sha


class FakeTargets:
    def __init__(self, targets) -> None:
        self.targets = list(targets)
        self.calls = 0

    def get_publish_target(self, repository, pr_number):
        self.calls += 1
        if len(self.targets) == 1:
            return self.targets[0]
        return self.targets.pop(0)


class FakePolicy:
    def __init__(self, allowed: bool = True, reason_codes=()) -> None:
        self.allowed = allowed
        self.reason_codes = tuple(reason_codes)
        self.requests = []

    def evaluate(self, request):
        self.requests.append(request)
        return SimpleNamespace(
            allowed=self.allowed, reason_codes=self.reason_codes
        )


class FakeWorktrees:
    def __init__(self) -> None:
        self.calls = 0

    def assert_active_checkouts_unchanged(self):
        self.calls += 1


class FakeIntents:
    def __init__(self) -> None:
        self.records = []
        self.uncertain = []

    def record(self, claim, worktree_id, starting_sha, local_commit_sha, now):
        self.records.append((claim, worktree_id, starting_sha, local_commit_sha))
        return object()

    def note_uncertain_publication(self, claim, worktree_id, local_commit_sha):
        self.uncertain.append((claim, worktree_id, local_commit_sha))
        return object()


def _claim() -> ClaimResult:
    cycle = str(uuid4())
    attempt_id = str(uuid4())
    lease_id = str(uuid4())
    key = QueueKey(_REPOSITORY, 42, _SHA)
    return ClaimResult(
        lease=Lease(
            lease_id=lease_id,
            generation=1,
            attempt_id=attempt_id,
            owner="gitkeeper-automatic",
            key=key,
            cycle_id=cycle,
            retry_authorization=None,
            claimed_at=_AT,
            expires_at=_AT + timedelta(minutes=2),
            cancelled_at=None,
        ),
        attempt=InvocationAttempt(
            attempt_id=attempt_id,
            lease_id=lease_id,
            key=key,
            cycle_id=cycle,
            kind=InvocationKind.automatic,
            outcome=AttemptOutcome.reserved,
            reserved_at=_AT,
            started_at=None,
            finished_at=None,
            model_id=None,
            error=None,
        ),
        authorization=None,
    )


def _worktree(claim: ClaimResult, tmp_path: Path) -> WorktreeRecord:
    path = tmp_path / "worktree"
    path.mkdir(exist_ok=True)
    return WorktreeRecord(
        worktree_id=str(uuid4()),
        identity=WorktreeIdentity(
            repository=claim.lease.key.repository,
            pr_number=claim.lease.key.pr_number,
            head_sha=claim.lease.key.head_sha,
            cycle_id=claim.attempt.cycle_id,
        ),
        path=path,
        branch=None,
        created_at=_AT,
        preserved_reason=None,
    )


def _target(head_sha: str = _SHA) -> PublishTarget:
    return PublishTarget(
        repository=_REPOSITORY,
        pr_number=42,
        branch="repair/42",
        head_repository=_REPOSITORY,
        is_default_branch=False,
        remote=RemotePrState(
            number=42,
            repository=_REPOSITORY,
            author="SumukhaK",
            head_sha=head_sha,
            is_open=True,
            is_draft=False,
            head_repository=_REPOSITORY,
        ),
    )


def _publisher(
    tmp_path: Path,
    git: FakeGit,
    policy: FakePolicy,
    targets: FakeTargets,
    intents: FakeIntents,
) -> PatchPublisher:
    return PatchPublisher(
        git=git,
        targets=targets,
        policy=policy,
        worktrees=FakeWorktrees(),
        active_checkouts=lambda: (),
        managed_root=tmp_path / "managed",
        lifecycle_guard=threading.RLock(),
        intents=intents,
        clock=lambda: _AT,
    )


def _publish(publisher, claim, worktree, target, *, current=True):
    return publisher.validate_and_publish(
        claim,
        worktree,
        target,
        RepairReason.deterministic_check_failure,
        threading.Event(),
        is_current=lambda: current,
    )


def test_successful_fast_forward_publication(tmp_path) -> None:
    claim = _claim()
    worktree = _worktree(claim, tmp_path)
    git = FakeGit()
    intents = FakeIntents()
    targets = FakeTargets([_target()])
    publisher = _publisher(tmp_path, git, FakePolicy(), targets, intents)

    result = _publish(publisher, claim, worktree, _target())

    assert result.status == "published"
    assert result.commit_sha == _LOCAL
    assert git.staged == ("app/src/main/java/Foo.kt",)
    assert git.commits == ["GitKeeper worker repair"]
    assert git.pushes == [("repair/42", _LOCAL)]
    assert len(intents.records) == 1
    assert targets.calls == 2


def test_scope_refusal_does_not_stage_or_commit(tmp_path) -> None:
    claim = _claim()
    worktree = _worktree(claim, tmp_path)
    git = FakeGit()
    intents = FakeIntents()
    targets = FakeTargets([_target()])
    publisher = _publisher(
        tmp_path, git, FakePolicy(False, ("path_denied",)), targets, intents
    )

    result = _publish(publisher, claim, worktree, _target())

    assert result.status == "blocked"
    assert result.reason_codes == ("path_denied",)
    assert git.staged is None
    assert git.commits == []
    assert git.pushes == []
    assert targets.calls == 0


def test_stale_remote_before_commit_blocks(tmp_path) -> None:
    claim = _claim()
    worktree = _worktree(claim, tmp_path)
    git = FakeGit()
    intents = FakeIntents()
    targets = FakeTargets([_target("c" * 40)])
    publisher = _publisher(tmp_path, git, FakePolicy(), targets, intents)

    result = _publish(publisher, claim, worktree, _target())

    assert result.status == "blocked"
    assert result.reason_codes == ("remote_stale",)
    assert git.commits == []
    assert targets.calls == 1


def test_index_mutation_after_staging_blocks(tmp_path) -> None:
    claim = _claim()
    worktree = _worktree(claim, tmp_path)
    git = FakeGit(unstaged_after=True)
    publisher = _publisher(tmp_path, git, FakePolicy(), FakeTargets([_target()]), FakeIntents())

    result = _publish(publisher, claim, worktree, _target())

    assert result.status == "blocked"
    assert result.reason_codes == ("unstaged",)
    assert git.commits == []


def test_unknown_push_records_the_association(tmp_path) -> None:
    claim = _claim()
    worktree = _worktree(claim, tmp_path)
    git = FakeGit(push_error=GitPushOutcomeUnknown("unknown"))
    intents = FakeIntents()
    publisher = _publisher(tmp_path, git, FakePolicy(), FakeTargets([_target()]), intents)

    result = _publish(publisher, claim, worktree, _target())

    assert result.status == "unknown"
    assert result.commit_sha == _LOCAL
    assert len(intents.uncertain) == 1


def test_rejected_push_is_blocked_without_an_association(tmp_path) -> None:
    claim = _claim()
    worktree = _worktree(claim, tmp_path)
    git = FakeGit(push_error=GitPushRejected("rejected"))
    intents = FakeIntents()
    publisher = _publisher(tmp_path, git, FakePolicy(), FakeTargets([_target()]), intents)

    result = _publish(publisher, claim, worktree, _target())

    assert result.status == "blocked"
    assert result.reason_codes == ("push_rejected",)
    assert intents.uncertain == []


def test_remote_mismatch_after_push_is_unknown(tmp_path) -> None:
    claim = _claim()
    worktree = _worktree(claim, tmp_path)
    git = FakeGit(remote="c" * 40)
    intents = FakeIntents()
    publisher = _publisher(tmp_path, git, FakePolicy(), FakeTargets([_target()]), intents)

    result = _publish(publisher, claim, worktree, _target())

    assert result.status == "unknown"
    assert result.reason_codes == ("remote_mismatch",)
    assert len(intents.uncertain) == 1


def test_stale_callback_blocks_before_any_target_read(tmp_path) -> None:
    claim = _claim()
    worktree = _worktree(claim, tmp_path)
    targets = FakeTargets([_target()])
    publisher = _publisher(tmp_path, FakeGit(), FakePolicy(), targets, FakeIntents())

    result = _publish(publisher, claim, worktree, _target(), current=False)

    assert result.status == "blocked"
    assert result.reason_codes == ("stale",)
    assert targets.calls == 0


def test_reconcile_unknown_equality_and_divergence(tmp_path) -> None:
    claim = _claim()
    worktree = _worktree(claim, tmp_path)
    git = FakeGit(commit_sha=_LOCAL, remote=_LOCAL)
    intents = FakeIntents()
    publisher = _publisher(tmp_path, git, FakePolicy(), FakeTargets([_target()]), intents)
    expected = dict(
        worktree=worktree,
        target=_target(),
        reason=RepairReason.deterministic_check_failure,
        expected_starting_sha=_SHA,
        expected_local_commit_sha=_LOCAL,
        cancellation=threading.Event(),
        is_current=lambda: True,
    )

    equal = publisher.reconcile_unknown(**expected)
    assert equal.status == "published"
    assert equal.reason_codes == ("remote_equal",)

    git.remote = "c" * 40
    diverged = publisher.reconcile_unknown(**expected)
    assert diverged.status == "blocked"
    assert diverged.reason_codes == ("remote_diverged",)


def test_reconcile_unknown_rejects_a_wrong_worktree_head(tmp_path) -> None:
    claim = _claim()
    worktree = _worktree(claim, tmp_path)
    git = FakeGit(commit_sha="c" * 40)
    publisher = _publisher(tmp_path, git, FakePolicy(), FakeTargets([_target()]), FakeIntents())

    result = publisher.reconcile_unknown(
        worktree=worktree,
        target=_target(),
        reason=RepairReason.deterministic_check_failure,
        expected_starting_sha=_SHA,
        expected_local_commit_sha=_LOCAL,
        cancellation=threading.Event(),
        is_current=lambda: True,
    )

    assert result.status == "blocked"
    assert result.reason_codes == ("validation_failed",)
