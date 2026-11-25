from pathlib import Path

import pytest

from gitkeeper.policy.patch_scope import (
    FileChange,
    PatchScopePolicy,
    RemotePrState,
    ScopeReason,
    ScopeRequest,
    path_is_allowed,
)

STARTING_SHA = "a" * 40
NEXT_SHA = "b" * 40


@pytest.fixture
def roots(tmp_path):
    managed = tmp_path / "managed"
    worktree = managed / "worktrees" / "setu-pr-19"
    gitkeeper_checkout = managed / "GitKeeper"
    setu_checkout = managed / "Setu-mobile"
    for path in (worktree, gitkeeper_checkout, setu_checkout):
        path.mkdir(parents=True, exist_ok=True)
    return {
        "managed": managed,
        "worktree": worktree,
        "active": (gitkeeper_checkout, setu_checkout),
    }


def change(path, *, status="modified", added=4, deleted=1, **overrides):
    payload = {
        "path": path,
        "status": status,
        "added_lines": added,
        "deleted_lines": deleted,
    }
    payload.update(overrides)
    return FileChange(**payload)


def request(roots, changes, **overrides):
    payload = {
        "starting_sha": STARTING_SHA,
        "head_sha": STARTING_SHA,
        "worktree_path": roots["worktree"],
        "managed_root": roots["managed"],
        "active_checkouts": roots["active"],
        "changes": tuple(changes),
        "remote": RemotePrState(
            number=19,
            repository="SumukhaK/Setu-mobile",
            author="SumukhaK",
            head_sha=STARTING_SHA,
            is_open=True,
            is_draft=False,
        ),
    }
    payload.update(overrides)
    return ScopeRequest(**payload)


def codes(decision):
    return {violation.code for violation in decision.violations}


def minimal_changes():
    return [
        change("app/src/main/java/com/setu/LoginViewModel.kt"),
        change("app/src/test/java/com/setu/LoginViewModelTest.kt"),
    ]


def test_source_change_with_a_matching_test_is_allowed(roots):
    decision = PatchScopePolicy().evaluate(request(roots, minimal_changes()))

    assert decision.allowed is True
    assert decision.violations == ()
    assert decision.reason_codes == ()


def test_empty_patch_is_rejected(roots):
    decision = PatchScopePolicy().evaluate(request(roots, []))

    assert decision.allowed is False
    assert codes(decision) == {ScopeReason.EMPTY}


def test_every_violation_is_reported_so_the_operator_sees_the_whole_problem(roots):
    changes = [
        change(".github/workflows/android.yml", added=2, deleted=0),
        change("docs/notes.md", added=2, deleted=0),
        change("docs/old.md", status="deleted"),
    ]

    decision = PatchScopePolicy().evaluate(request(roots, changes))

    assert codes(decision) >= {
        ScopeReason.PATH_DENIED,
        ScopeReason.NO_SOURCE_CHANGE,
        ScopeReason.DELETION,
    }


@pytest.mark.parametrize(
    "path",
    [
        "app/src/main/java/com/setu/Foo.kt",
        "core/network/src/main/kotlin/com/setu/Http.kt",
        "feature/home/src/test/kotlin/com/setu/HomeTest.kt",
        "tests/workflow-contract.test.mjs",
        "docs/superpowers/specs/pilot.md",
        "app/src/main/AndroidManifest.xml",
    ],
)
def test_allowlisted_paths_are_accepted(path):
    assert path_is_allowed(path) is True


@pytest.mark.parametrize(
    "path",
    [
        ".github/workflows/android.yml",
        "scripts/release.sh",
        "gradle/libs.versions.toml",
        "gradlew",
        "gradlew.bat",
        "build.gradle.kts",
        "app/build.gradle.kts",
        "gradle.properties",
        "config/pilot.yaml",
        "third_party/lib/README.md",
        "index.html",
        "prototype.css",
        "app/src/main/res/xml/network_security_config.xml",
        "app/proguard-rules.pro",
        "release.keystore",
        "signing.jks",
        "deploy.pem",
        "keystore.p12",
        ".gitmodules",
        ".gitattributes",
    ],
)
def test_denylisted_paths_win_over_the_allowlist(path):
    assert path_is_allowed(path) is False


def test_denylist_is_case_insensitive_because_windows_paths_are(roots):
    decision = PatchScopePolicy().evaluate(
        request(roots, [change(".GITHUB/workflows/android.yml"), *minimal_changes()])
    )

    assert ScopeReason.PATH_DENIED in codes(decision)


@pytest.mark.parametrize(
    "path",
    [
        "app/src/../../.github/workflows/android.yml",
        "/etc/passwd",
        "C:/Windows/system32/drivers/etc/hosts",
        "app\\src\\main\\java\\Foo.kt",
        "~/secrets.txt",
    ],
)
def test_malformed_or_escaping_paths_are_rejected(roots, path):
    decision = PatchScopePolicy().evaluate(
        request(roots, [change(path), *minimal_changes()])
    )

    assert codes(decision) & {ScopeReason.PATH_MALFORMED, ScopeReason.PATH_DENIED}


def test_ten_files_is_the_boundary_and_eleven_is_rejected(roots):
    inside = [change(f"app/src/main/java/com/setu/File{index}.kt") for index in range(9)]
    test_file = change("app/src/test/java/com/setu/LoginTest.kt")

    allowed = PatchScopePolicy().evaluate(request(roots, [*inside, test_file]))
    rejected = PatchScopePolicy().evaluate(
        request(roots, [*inside, test_file, change("app/src/main/java/com/setu/Extra.kt")])
    )

    assert allowed.allowed is True
    assert codes(rejected) == {ScopeReason.TOO_MANY_FILES}


def test_four_hundred_changed_lines_is_the_boundary_and_four_oh_one_is_rejected(roots):
    at_limit = [
        change("app/src/main/java/com/setu/LoginViewModel.kt", added=150, deleted=0),
        change("app/src/test/java/com/setu/LoginViewModelTest.kt", added=150, deleted=0),
        change("docs/pilot-notes.md", added=100, deleted=0),
    ]
    over_limit = [
        change("app/src/main/java/com/setu/LoginViewModel.kt", added=150, deleted=0),
        change("app/src/test/java/com/setu/LoginViewModelTest.kt", added=150, deleted=0),
        change("docs/pilot-notes.md", added=101, deleted=0),
    ]

    assert PatchScopePolicy().evaluate(request(roots, at_limit)).allowed is True
    assert codes(PatchScopePolicy().evaluate(request(roots, over_limit))) == {
        ScopeReason.TOO_LARGE
    }


def test_one_hundred_fifty_lines_in_a_single_file_is_the_boundary(roots):
    at_limit = [
        change("app/src/main/java/com/setu/LoginViewModel.kt", added=150, deleted=0),
        change("app/src/test/java/com/setu/LoginViewModelTest.kt", added=1, deleted=0),
    ]
    over_limit = [
        change("app/src/main/java/com/setu/LoginViewModel.kt", added=151, deleted=0),
        change("app/src/test/java/com/setu/LoginViewModelTest.kt", added=1, deleted=0),
    ]

    assert PatchScopePolicy().evaluate(request(roots, at_limit)).allowed is True
    assert codes(PatchScopePolicy().evaluate(request(roots, over_limit))) == {
        ScopeReason.FILE_TOO_LARGE
    }


def test_deleting_a_file_is_rejected(roots):
    changes = [*minimal_changes(), change("docs/old.md", status="deleted")]

    decision = PatchScopePolicy().evaluate(request(roots, changes))

    assert codes(decision) == {ScopeReason.DELETION}


def test_renaming_or_copying_a_file_is_rejected(roots):
    renamed = [*minimal_changes(), change("docs/new.md", status="renamed")]
    copied = [*minimal_changes(), change("docs/new.md", status="copied")]

    assert codes(PatchScopePolicy().evaluate(request(roots, renamed))) == {
        ScopeReason.RENAME
    }
    assert codes(PatchScopePolicy().evaluate(request(roots, copied))) == {
        ScopeReason.RENAME
    }


def test_changing_the_file_mode_is_rejected(roots):
    changes = [
        change("docs/setup.md", old_mode="100644", new_mode="100755"),
        *minimal_changes(),
    ]

    decision = PatchScopePolicy().evaluate(request(roots, changes))

    assert codes(decision) == {ScopeReason.MODE_CHANGE}


def test_symlinks_are_rejected(roots):
    changes = [*minimal_changes(), change("docs/link.md", new_mode="120000")]

    decision = PatchScopePolicy().evaluate(request(roots, changes))

    assert codes(decision) == {ScopeReason.SYMLINK}


def test_submodules_are_rejected(roots):
    changes = [*minimal_changes(), change("docs/vendor", new_mode="160000")]

    decision = PatchScopePolicy().evaluate(request(roots, changes))

    assert codes(decision) == {ScopeReason.SUBMODULE}


def test_binary_content_is_rejected(roots):
    changes = [*minimal_changes(), change("docs/logo.png", new_content=b"\x89PNG\x00\x01")]

    decision = PatchScopePolicy().evaluate(request(roots, changes))

    assert codes(decision) == {ScopeReason.BINARY}


def test_a_patch_without_a_src_change_is_rejected(roots):
    changes = [change("docs/setup.md"), change("tests/workflow-contract.test.mjs")]

    decision = PatchScopePolicy().evaluate(request(roots, changes))

    assert codes(decision) == {ScopeReason.NO_SOURCE_CHANGE}


def test_a_repair_of_a_ci_failure_must_touch_a_test(roots):
    changes = [change("app/src/main/java/com/setu/LoginViewModel.kt")]

    decision = PatchScopePolicy().evaluate(
        request(roots, changes, requires_test_change=True)
    )

    assert codes(decision) == {ScopeReason.NO_TEST_CHANGE}


def test_a_repair_that_touches_a_test_satisfies_the_test_requirement(roots):
    decision = PatchScopePolicy().evaluate(
        request(roots, minimal_changes(), requires_test_change=True)
    )

    assert decision.allowed is True


def test_added_lines_are_scanned_for_credentials(roots):
    changes = [
        change(
            "app/src/main/java/com/setu/ApiClient.kt",
            added_text='private val key = "AKIAIOSFODNN7EXAMPLE"\n',
        ),
        change("app/src/test/java/com/setu/ApiClientTest.kt"),
    ]

    decision = PatchScopePolicy().evaluate(request(roots, changes))

    assert codes(decision) == {ScopeReason.SECRET_PATTERN}


def test_leftover_unstaged_changes_block_publication(roots):
    decision = PatchScopePolicy().evaluate(request(roots, minimal_changes(), unstaged=True))

    assert codes(decision) == {ScopeReason.UNSTAGED}


def test_head_must_still_match_the_starting_sha(roots):
    decision = PatchScopePolicy().evaluate(
        request(roots, minimal_changes(), head_sha=NEXT_SHA)
    )

    assert codes(decision) == {ScopeReason.HEAD_MISMATCH}


def test_a_moved_remote_head_blocks_publication(roots):
    moved = RemotePrState(
        number=19,
        repository="SumukhaK/Setu-mobile",
        author="SumukhaK",
        head_sha=NEXT_SHA,
        is_open=True,
        is_draft=False,
    )

    decision = PatchScopePolicy().evaluate(
        request(roots, minimal_changes(), remote=moved)
    )

    assert codes(decision) == {ScopeReason.HEAD_MISMATCH}


@pytest.mark.parametrize(
    "overrides",
    [
        {"is_open": False},
        {"is_draft": True},
        {"author": "someone-else"},
        {"repository": "SumukhaK/Other-mobile"},
        {"head_sha": None},
    ],
)
def test_remote_state_must_stay_eligible(roots, overrides):
    payload = {
        "number": 19,
        "repository": "SumukhaK/Setu-mobile",
        "author": "SumukhaK",
        "head_sha": STARTING_SHA,
        "is_open": True,
        "is_draft": False,
    }
    payload.update(overrides)

    decision = PatchScopePolicy().evaluate(
        request(roots, minimal_changes(), remote=RemotePrState(**payload))
    )

    assert ScopeReason.PR_NOT_ELIGIBLE in codes(decision)


def test_missing_remote_state_blocks_publication(roots):
    decision = PatchScopePolicy().evaluate(
        request(roots, minimal_changes(), remote=None)
    )

    assert codes(decision) == {ScopeReason.PR_NOT_ELIGIBLE}


def test_a_worktree_outside_the_managed_root_is_rejected(roots, tmp_path):
    outside = tmp_path / "elsewhere"
    outside.mkdir()

    decision = PatchScopePolicy().evaluate(
        request(roots, minimal_changes(), worktree_path=outside)
    )

    assert codes(decision) == {ScopeReason.WORKTREE_OUTSIDE}


def test_the_active_checkout_of_either_repository_is_never_a_worktree(roots):
    decision = PatchScopePolicy().evaluate(
        request(roots, minimal_changes(), worktree_path=roots["active"][1])
    )

    assert codes(decision) == {ScopeReason.WORKTREE_ACTIVE_CHECKOUT}


def test_decision_carries_stable_reason_codes_for_telemetry(roots):
    decision = PatchScopePolicy().evaluate(
        request(roots, [change("gradlew", status="deleted")], unstaged=True)
    )

    assert set(decision.reason_codes) == {
        ScopeReason.PATH_DENIED.value,
        ScopeReason.DELETION.value,
        ScopeReason.UNSTAGED.value,
        ScopeReason.NO_SOURCE_CHANGE.value,
    }
    assert len(decision.reason_codes) == len(set(decision.reason_codes))


def test_reason_codes_are_reported_once_even_when_many_paths_share_a_cause(roots):
    changes = [
        change(".github/workflows/a.yml"),
        change(".github/workflows/b.yml"),
        *minimal_changes(),
    ]

    decision = PatchScopePolicy().evaluate(request(roots, changes))

    assert [violation.code for violation in decision.violations].count(
        ScopeReason.PATH_DENIED
    ) == 2
    assert decision.reason_codes == (ScopeReason.PATH_DENIED.value,)


def test_policy_thresholds_are_code_constants_not_configurable_inputs():
    policy = PatchScopePolicy()

    assert (policy.MAX_FILES, policy.MAX_TOTAL_LINES, policy.MAX_FILE_LINES) == (10, 400, 150)
    with pytest.raises(TypeError):
        PatchScopePolicy(max_files=100)
