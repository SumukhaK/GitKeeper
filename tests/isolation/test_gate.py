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
    evidence_paths,
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
        evidence_paths=evidence_paths,
    )


def test_complete_report_for_expected_prototype_unlocks_gate(tmp_path):
    from gitkeeper.isolation.report import require_verified

    evidence = tmp_path / "summary.json"
    evidence.write_text("verified probe evidence", encoding="utf-8")
    report = build_report(evidence_paths=[evidence])

    assert require_verified(report, "windows-prototype-v1") is None


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
def test_any_failed_isolation_or_credential_assertion_blocks_gate(
    failed_probe, tmp_path
):
    from gitkeeper.isolation.report import IsolationGateError, require_verified

    evidence = tmp_path / "summary.json"
    evidence.write_text("probe failure evidence", encoding="utf-8")
    with pytest.raises(IsolationGateError):
        require_verified(
            build_report(
                failed_probe=failed_probe,
                evidence_paths=[evidence],
            ),
            "windows-prototype-v1",
        )


def test_missing_evidence_blocks_gate(tmp_path):
    from gitkeeper.isolation.report import IsolationGateError, require_verified

    with pytest.raises(IsolationGateError):
        require_verified(
            build_report(evidence_paths=[]),
            "windows-prototype-v1",
        )


def test_nonexistent_evidence_path_blocks_gate(tmp_path):
    from gitkeeper.isolation.report import IsolationGateError, require_verified

    with pytest.raises(IsolationGateError):
        require_verified(
            build_report(evidence_paths=[tmp_path / "missing.json"]),
            "windows-prototype-v1",
        )


def test_directory_is_not_accepted_as_evidence(tmp_path):
    from gitkeeper.isolation.report import IsolationGateError, require_verified

    evidence_directory = tmp_path / "evidence"
    evidence_directory.mkdir()
    with pytest.raises(IsolationGateError):
        require_verified(
            build_report(evidence_paths=[evidence_directory]),
            "windows-prototype-v1",
        )


def test_unsupported_host_blocks_gate(tmp_path):
    from gitkeeper.isolation.report import IsolationGateError, require_verified

    evidence = tmp_path / "summary.json"
    evidence.write_text("unsupported host evidence", encoding="utf-8")
    with pytest.raises(IsolationGateError):
        require_verified(
            build_report(
                failed_probe="host_file_access",
                evidence_paths=[evidence],
            ),
            "windows-prototype-v1",
        )


def test_report_from_wrong_prototype_version_blocks_gate(tmp_path):
    from gitkeeper.isolation.report import IsolationGateError, require_verified

    evidence = tmp_path / "summary.json"
    evidence.write_text("prototype evidence", encoding="utf-8")
    with pytest.raises(IsolationGateError):
        require_verified(
            build_report(
                prototype_id="windows-prototype-v0",
                evidence_paths=[evidence],
            ),
            "windows-prototype-v1",
        )


def test_boolean_is_not_accepted_as_an_isolation_report():
    from gitkeeper.isolation.report import IsolationGateError, require_verified

    with pytest.raises(IsolationGateError):
        require_verified(True, "windows-prototype-v1")
