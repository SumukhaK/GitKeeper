import pytest


def policy_mapping(**overrides):
    data = {
        "mode": "ACCEPTED_HOST_MODE",
        "repositories": ["SumukhaK/Setu-mobile"],
        "public_exceptions": ["SumukhaK/Setu-mobile"],
        "verified_private_repositories": [],
        "model_ladder": ["opencode/deepseek-v4.1-flash-free"],
        "expected_cli_version": "1.19.6",
    }
    data.update(overrides)
    return data


def owner_policy(**overrides):
    from gitkeeper.host_mode.readiness import HostModePolicy

    return HostModePolicy.from_mapping(policy_mapping(**overrides))


def facts(**overrides):
    from gitkeeper.host_mode.readiness import PullRequestFacts, RepairReason

    data = {
        "repository": "SumukhaK/Setu-mobile",
        "number": 19,
        "head_repository": "SumukhaK/Setu-mobile",
        "author": "SumukhaK",
        "head_sha": "a153271a18241b3cf5b9d5ed7025916c6b11e9f8",
        "check_head_sha": "a153271a18241b3cf5b9d5ed7025916c6b11e9f8",
        "is_open": True,
        "is_draft": False,
        "repair_reason": RepairReason.DETERMINISTIC_CHECK_FAILURE,
        "automatic_invocations_used": 0,
        "retry_authorized": False,
    }
    data.update(overrides)
    return PullRequestFacts(**data)


def capabilities(**overrides):
    from gitkeeper.host_mode.readiness import CliCapability

    data = {
        "cli_version": "1.19.6",
        "broker_verified": True,
        "unsupported_reason": None,
    }
    data.update(overrides)
    return CliCapability(**data)


def enabled_config(**overrides):
    from gitkeeper.host_mode.readiness import WorkerConfiguration

    data = {"worker_enabled": True, "merged": True}
    data.update(overrides)
    return WorkerConfiguration(**data)


def evaluate(*, overrides_facts=None, config=None, policy=None, capability=None):
    from gitkeeper.host_mode.readiness import evaluate_worker_readiness

    return evaluate_worker_readiness(
        facts=facts(**(overrides_facts or {})),
        config=config if config is not None else enabled_config(),
        policy=policy if policy is not None else owner_policy(),
        capability=capability if capability is not None else capabilities(),
    )


def codes(status):
    return {blocker.code.value for blocker in status.blockers}


def test_accepted_host_mode_is_never_reported_as_isolation():
    status = evaluate()

    assert status.ready is True
    assert status.mode == "ACCEPTED_HOST_MODE"
    assert status.isolation_claimed is False
    assert "ACCEPTED_HOST_MODE" in status.summary()


def test_policy_rejects_a_mode_label_that_claims_isolation():
    from gitkeeper.host_mode.readiness import HostModePolicy, HostModePolicyError

    for mode in ("isolated", "sandboxed", "", "accepted-host-mode"):
        with pytest.raises(HostModePolicyError):
            HostModePolicy.from_mapping(policy_mapping(mode=mode))


def test_policy_rejects_an_empty_repository_allowlist():
    from gitkeeper.host_mode.readiness import HostModePolicy, HostModePolicyError

    with pytest.raises(HostModePolicyError):
        HostModePolicy.from_mapping(
            policy_mapping(repositories=[], public_exceptions=[])
        )


def test_policy_rejects_unknown_keys_so_local_policy_stays_owner_controlled():
    from gitkeeper.host_mode.readiness import HostModePolicy, HostModePolicyError

    with pytest.raises(HostModePolicyError):
        HostModePolicy.from_mapping(
            policy_mapping(worker_enabled=True, auto_merge=True)
        )


def test_public_exception_must_be_an_allowlisted_repository():
    from gitkeeper.host_mode.readiness import HostModePolicy, HostModePolicyError

    with pytest.raises(HostModePolicyError):
        HostModePolicy.from_mapping(
            policy_mapping(public_exceptions=["SumukhaK/GitKeeper"])
        )


def test_repository_cannot_be_both_public_exception_and_verified_private():
    from gitkeeper.host_mode.readiness import HostModePolicy, HostModePolicyError

    with pytest.raises(HostModePolicyError):
        HostModePolicy.from_mapping(
            policy_mapping(
                verified_private_repositories=["SumukhaK/Setu-mobile"],
            )
        )


def test_unknown_public_repository_is_denied():
    status = evaluate(
        overrides_facts={
            "repository": "SumukhaK/other-repo",
            "head_repository": "SumukhaK/other-repo",
        }
    )

    assert status.ready is False
    assert "repository-not-allowlisted" in codes(status)


def test_allowlist_match_is_exact_and_case_sensitive():
    status = evaluate(
        overrides_facts={
            "repository": "sumukhak/setu-mobile",
            "head_repository": "sumukhak/setu-mobile",
        }
    )

    assert status.ready is False
    assert "repository-not-allowlisted" in codes(status)


def test_private_repository_needs_verification_before_it_is_eligible():
    status = evaluate(
        policy=owner_policy(
            repositories=["SumukhaK/Setu-mobile", "SumukhaK/GitKeeper"],
            public_exceptions=["SumukhaK/Setu-mobile"],
        ),
        overrides_facts={
            "repository": "SumukhaK/GitKeeper",
            "head_repository": "SumukhaK/GitKeeper",
        },
    )

    assert status.ready is False
    assert "repository-privacy-unverified" in codes(status)


def test_verified_private_repository_is_eligible():
    status = evaluate(
        policy=owner_policy(
            repositories=["SumukhaK/Setu-mobile", "SumukhaK/GitKeeper"],
            public_exceptions=["SumukhaK/Setu-mobile"],
            verified_private_repositories=["SumukhaK/GitKeeper"],
        ),
        overrides_facts={
            "repository": "SumukhaK/GitKeeper",
            "head_repository": "SumukhaK/GitKeeper",
        },
    )

    assert status.ready is True


def test_fork_head_is_denied():
    status = evaluate(overrides_facts={"head_repository": "someone/Setu-mobile"})

    assert status.ready is False
    assert "fork-head" in codes(status)


def test_other_author_is_denied():
    status = evaluate(overrides_facts={"author": "someone"})

    assert status.ready is False
    assert "author-not-owner" in codes(status)


def test_author_match_is_exact_case():
    status = evaluate(overrides_facts={"author": "sumukhak"})

    assert status.ready is False
    assert "author-not-owner" in codes(status)


def test_closed_pull_request_is_denied():
    status = evaluate(overrides_facts={"is_open": False})

    assert status.ready is False
    assert "pr-closed" in codes(status)


def test_draft_pull_request_is_denied():
    status = evaluate(overrides_facts={"is_draft": True})

    assert status.ready is False
    assert "pr-draft" in codes(status)


def test_unmerged_config_never_enables_work():
    status = evaluate(config=enabled_config(merged=False))

    assert status.ready is False
    assert "config-not-merged" in codes(status)


def test_disabled_config_never_enables_work():
    status = evaluate(config=enabled_config(worker_enabled=False))

    assert status.ready is False
    assert "worker-disabled" in codes(status)


def test_remote_config_cannot_define_the_repository_allowlist():
    from gitkeeper.host_mode.readiness import (
        HostModePolicyError,
        WorkerConfiguration,
    )

    with pytest.raises(HostModePolicyError):
        WorkerConfiguration.from_remote_mapping(
            {
                "worker_enabled": True,
                "polling_preferences": {"mode": "adaptive"},
                "motion_preference": None,
                "deterministic_check_ids": ["Android CI"],
                "repositories": ["SumukhaK/Setu-mobile"],
            }
        )
    with pytest.raises(HostModePolicyError):
        WorkerConfiguration.from_remote_mapping(
            {"worker_enabled": True, "model_ladder": ["opencode/glm-5.3"]}
        )


def test_remote_config_accepts_only_the_merged_config_contract():
    from gitkeeper.host_mode.readiness import WorkerConfiguration

    config = WorkerConfiguration.from_remote_mapping(
        {
            "worker_enabled": True,
            "polling_preferences": {"mode": "adaptive"},
            "motion_preference": "reduced",
            "deterministic_check_ids": ["Android CI"],
        }
    )

    assert config.worker_enabled is True
    assert config.merged is True


def test_remote_config_rejects_a_non_boolean_enabled_flag():
    from gitkeeper.host_mode.readiness import (
        HostModePolicyError,
        WorkerConfiguration,
    )

    with pytest.raises(HostModePolicyError):
        WorkerConfiguration.from_remote_mapping({"worker_enabled": "true"})


def test_passing_checks_alone_are_not_a_repair_reason():
    status = evaluate(overrides_facts={"repair_reason": None})

    assert status.ready is False
    assert "no-repair-reason" in codes(status)


def test_unknown_repair_reason_value_is_rejected():
    from gitkeeper.host_mode.readiness import HostModePolicyError, PullRequestFacts

    with pytest.raises(HostModePolicyError):
        PullRequestFacts(
            repository="SumukhaK/Setu-mobile",
            number=19,
            head_repository="SumukhaK/Setu-mobile",
            author="SumukhaK",
            head_sha="a153271a18241b3cf5b9d5ed7025916c6b11e9f8",
            check_head_sha="a153271a18241b3cf5b9d5ed7025916c6b11e9f8",
            is_open=True,
            is_draft=False,
            repair_reason="looks-green",
            automatic_invocations_used=0,
        )


def test_check_results_from_another_sha_are_stale():
    status = evaluate(overrides_facts={"check_head_sha": "0" * 40})

    assert status.ready is False
    assert "stale-check-sha" in codes(status)


def test_missing_check_evidence_is_stale():
    status = evaluate(overrides_facts={"check_head_sha": None})

    assert status.ready is False
    assert "stale-check-sha" in codes(status)


def test_exhausted_automatic_budget_blocks_the_worker():
    status = evaluate(overrides_facts={"automatic_invocations_used": 3})

    assert status.ready is False
    assert "attempt-budget-exhausted" in codes(status)


def test_explicit_owner_retry_overrides_the_budget():
    status = evaluate(
        overrides_facts={"automatic_invocations_used": 5, "retry_authorized": True}
    )

    assert status.ready is True


def test_missing_local_model_ladder_blocks_the_worker():
    status = evaluate(policy=owner_policy(model_ladder=[]))

    assert status.ready is False
    assert "model-not-configured" in codes(status)


def test_policy_rejects_a_non_opencode_model():
    from gitkeeper.host_mode.readiness import HostModePolicy, HostModePolicyError

    with pytest.raises(HostModePolicyError):
        HostModePolicy.from_mapping(
            policy_mapping(model_ladder=["anthropic/claude-sonnet-4"])
        )


def test_policy_rejects_a_ladder_active_model_outside_the_ladder():
    from gitkeeper.host_mode.readiness import HostModePolicy, HostModePolicyError

    with pytest.raises(HostModePolicyError):
        HostModePolicy.from_mapping(
            {
                **policy_mapping(),
                "model_ladder": {
                    "entries": ["opencode/deepseek-v4.1-flash-free"],
                    "active": "opencode/glm-5.3",
                },
            }
        )


def test_wrong_cli_version_blocks_the_worker():
    status = evaluate(capability=capabilities(cli_version="1.19.5"))

    assert status.ready is False
    assert "cli-version-mismatch" in codes(status)


def test_missing_cli_blocks_the_worker():
    status = evaluate(capability=capabilities(cli_version=None))

    assert status.ready is False
    assert "cli-missing" in codes(status)


def test_unverified_command_broker_blocks_the_worker():
    status = evaluate(capability=capabilities(broker_verified=False))

    assert status.ready is False
    assert "broker-unverified" in codes(status)


def test_capability_that_reports_a_known_unsupported_broker_is_rejected():
    from gitkeeper.host_mode.readiness import CliCapability, HostModePolicyError

    with pytest.raises(HostModePolicyError):
        CliCapability(
            cli_version="1.19.6",
            broker_verified=True,
            unsupported_reason="shell tool bypasses the broker",
        )


def test_every_violation_is_reported_together():
    status = evaluate(
        overrides_facts={
            "author": "someone",
            "is_draft": True,
            "repair_reason": None,
            "automatic_invocations_used": 3,
        },
        config=enabled_config(worker_enabled=False),
        capability=capabilities(cli_version=None, broker_verified=False),
    )

    assert status.ready is False
    assert codes(status) >= {
        "author-not-owner",
        "pr-draft",
        "worker-disabled",
        "no-repair-reason",
        "attempt-budget-exhausted",
        "cli-missing",
        "broker-unverified",
    }


def test_require_worker_ready_refuses_and_names_the_blockers():
    from gitkeeper.host_mode.readiness import (
        WorkerLaunchBlocked,
        require_worker_ready,
    )

    status = evaluate(overrides_facts={"is_draft": True})
    with pytest.raises(WorkerLaunchBlocked) as error:
        require_worker_ready(status)

    assert "pr-draft" in str(error.value)
    require_worker_ready(evaluate())


def test_no_isolation_report_or_boolean_can_authorize_a_launch():
    import inspect

    from gitkeeper.host_mode import readiness

    source = inspect.getsource(readiness)
    assert "gitkeeper.isolation" not in source

    status = evaluate()
    for stray in ("isolation_passes", True, "GO", None):
        with pytest.raises(TypeError):
            readiness.evaluate_worker_readiness(
                facts=facts(),
                config=enabled_config(),
                policy=owner_policy(),
                capability=stray,
            )
    assert status.ready is True


def test_facts_reject_a_malformed_repository_and_a_negative_budget():
    from gitkeeper.host_mode.readiness import HostModePolicyError, PullRequestFacts

    for overrides in (
        {"repository": "Setu-mobile"},
        {"number": 0},
        {"head_sha": ""},
        {"automatic_invocations_used": -1},
    ):
        data = {
            "repository": "SumukhaK/Setu-mobile",
            "number": 19,
            "head_repository": "SumukhaK/Setu-mobile",
            "author": "SumukhaK",
            "head_sha": "a153271a18241b3cf5b9d5ed7025916c6b11e9f8",
            "check_head_sha": "a153271a18241b3cf5b9d5ed7025916c6b11e9f8",
            "is_open": True,
            "is_draft": False,
            "repair_reason": None,
            "automatic_invocations_used": 0,
        }
        data.update(overrides)
        with pytest.raises(HostModePolicyError):
            PullRequestFacts(**data)
