from pathlib import Path

import pytest


HOST_PROBE_NAMES = (
    "host_file_access",
    "process_metadata",
    "environment_access",
    "outside_worktree_write",
    "network_egress",
)


def build_report(
    *,
    prototype_id="windows-prototype-v1",
    failed_probe=None,
    missing_evidence=False,
):
    from gitkeeper.isolation.report import IsolationReport, ProbeResult

    probes = [
        ProbeResult(
            name=name,
            passed=name != failed_probe,
            failure_category=None if name != failed_probe else "unsupported_host",
        )
        for name in HOST_PROBE_NAMES
    ]
    return IsolationReport(
        prototype_id=prototype_id,
        host_probe_results=probes,
        credential_probe_result=ProbeResult(
            name="credential_exposure",
            passed=failed_probe != "credential_exposure",
            failure_category=(
                None if failed_probe != "credential_exposure" else "credential_visible"
            ),
        ),
        opencode_model_configured=True,
        opencode_call_succeeded=True,
        evidence_paths=[] if missing_evidence else [Path("evidence/summary.json")],
    )


def test_complete_report_for_expected_prototype_unlocks_gate():
    from gitkeeper.isolation.report import require_verified

    assert require_verified(build_report(), "windows-prototype-v1") is None


@pytest.mark.parametrize(
    "failed_probe",
    [
        "host_file_access",
        "process_metadata",
        "environment_access",
        "outside_worktree_write",
        "network_egress",
        "credential_exposure",
    ],
)
def test_any_failed_isolation_or_credential_assertion_blocks_gate(failed_probe):
    from gitkeeper.isolation.report import IsolationGateError, require_verified

    with pytest.raises(IsolationGateError):
        require_verified(
            build_report(failed_probe=failed_probe),
            "windows-prototype-v1",
        )


def test_missing_evidence_blocks_gate():
    from gitkeeper.isolation.report import IsolationGateError, require_verified

    with pytest.raises(IsolationGateError):
        require_verified(
            build_report(missing_evidence=True),
            "windows-prototype-v1",
        )


def test_unsupported_host_blocks_gate():
    from gitkeeper.isolation.report import IsolationGateError, require_verified

    with pytest.raises(IsolationGateError):
        require_verified(
            build_report(failed_probe="host_file_access"),
            "windows-prototype-v1",
        )


def test_report_from_wrong_prototype_version_blocks_gate():
    from gitkeeper.isolation.report import IsolationGateError, require_verified

    with pytest.raises(IsolationGateError):
        require_verified(
            build_report(prototype_id="windows-prototype-v0"),
            "windows-prototype-v1",
        )
