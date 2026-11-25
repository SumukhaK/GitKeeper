"""Behavioural spec for the OpenCode command-interception capability probe.

Task 3 of the host-mode readiness plan must not be satisfied by a prompt
request or a documentation claim. These tests fix the machine-checkable part of
the contract: what the launcher must force, what the resolved CLI configuration
must show, and what live-probe evidence has to exist before the readiness gate
may treat command mediation as proven.
"""

import json

import pytest

from gitkeeper.host_mode.opencode_capability import (
    BROKER_PLUGIN_SPEC,
    SHELL_PERMISSION,
    CapabilityGap,
    ControlledConfigError,
    LiveProbeRecord,
    ProbeRecordError,
    ResolvedCliConfig,
    build_launch_environment,
    evaluate_capability,
    inspect_resolved_config,
    load_probe_record,
    require_inert_worktree,
    save_probe_record,
    validate_launch_arguments,
)

VERSION = "1.19.6"
CONTROLLED_CONFIG = r"C:\Users\sumuk\AppData\Local\GitKeeper\opencode-broker.json"
WORKTREE = r"C:\Users\sumuk\GitKeeper\worktrees\setu-mobile-pr-19"


def resolved(**overrides):
    values = {
        "bash_permission": SHELL_PERMISSION,
        "plugins": (BROKER_PLUGIN_SPEC,),
        "mcp_servers": (),
        "model": "opencode/deepseek-v4.1-flash-free",
    }
    values.update(overrides)
    return ResolvedCliConfig(**values)


def record(**overrides):
    values = {
        "cli_version": VERSION,
        "hook": "plugin.tool.execute.before",
        "approval_paused_the_command": True,
        "approval_change_invalidated_the_request": True,
        "denial_prevented_execution": True,
        "direct_shell_bypass_found": False,
        "probe_directory": r"C:\Users\sumuk\AppData\Local\Temp\gitkeeper-probe",
    }
    values.update(overrides)
    return LiveProbeRecord(**values)


_DEFAULT = object()


def gaps_for(version=_DEFAULT, configuration=_DEFAULT, probe=_DEFAULT):
    return evaluate_capability(
        cli_version=VERSION if version is _DEFAULT else version,
        resolved=resolved() if configuration is _DEFAULT else configuration,
        probe=record() if probe is _DEFAULT else probe,
    ).gaps


def test_a_complete_probe_reports_no_gaps():
    assert gaps_for() == ()


def test_a_missing_cli_is_a_named_gap():
    assert CapabilityGap.CLI_MISSING in gaps_for(version=None, configuration=None)


def test_a_version_mismatch_is_a_named_gap():
    assert CapabilityGap.CLI_VERSION_MISMATCH in gaps_for(version="1.20.0")


def test_a_shell_permission_other_than_ask_is_a_gap():
    for permission in ("allow", "deny", None, "auto"):
        gaps = gaps_for(configuration=resolved(bash_permission=permission))
        assert CapabilityGap.SHELL_PERMISSION_NOT_ASK in gaps, permission


def test_a_resolved_config_without_the_broker_plugin_is_a_gap():
    gaps = gaps_for(configuration=resolved(plugins=("file:///C:/temp/other.js",)))
    assert CapabilityGap.BROKER_PLUGIN_MISSING in gaps


def test_the_resolved_plugin_url_the_cli_actually_reports_is_accepted():
    # `opencode debug config` prints the plugin as a file URL, so matching only
    # the bare name would report a gap on a correctly configured host.
    url = (
        "file:///C:/Users/sumuk/AppData/Local/GitKeeper/plugins/"
        f"{BROKER_PLUGIN_SPEC}.mjs"
    )
    assert gaps_for(configuration=resolved(plugins=(url,))) == ()
    assert gaps_for(
        configuration=resolved(plugins=(url, "file:///C:/temp/other.mjs"))
    ) == (CapabilityGap.UNEXPECTED_PLUGINS,)


def test_configured_mcp_servers_are_a_gap_because_they_can_spawn_processes():
    gaps = gaps_for(configuration=resolved(mcp_servers=("evil",)))
    assert CapabilityGap.MCP_SERVERS_CONFIGURED in gaps


def test_a_second_plugin_beside_the_broker_is_a_gap():
    # The CLI scans the worktree's `.opencode` directory for plugins outside the
    # OPENCODE_DISABLE_PROJECT_CONFIG guard, so a stray plugin could observe or
    # wrap every tool call. Only the broker may be configured.
    gaps = gaps_for(
        configuration=resolved(plugins=(BROKER_PLUGIN_SPEC, "file:///C:/temp/x.js"))
    )
    assert CapabilityGap.UNEXPECTED_PLUGINS in gaps
    assert gaps_for(configuration=resolved(plugins=(BROKER_PLUGIN_SPEC,))) == ()


def test_a_worktree_carrying_project_plugins_is_refused_before_launch(tmp_path):
    worktree = tmp_path / "pr-19"
    (worktree / ".opencode" / "plugin").mkdir(parents=True)
    (worktree / ".opencode" / "plugin" / "extra.js").write_text(
        "export const Extra = async () => ({});\n", encoding="utf-8"
    )

    with pytest.raises(ControlledConfigError):
        require_inert_worktree(worktree)


def test_an_untrusted_project_config_file_alone_is_tolerated(tmp_path):
    # `opencode.json` is disarmed by OPENCODE_DISABLE_PROJECT_CONFIG; only the
    # directory the CLI scans unconditionally makes a worktree unsafe.
    worktree = tmp_path / "pr-19"
    worktree.mkdir()
    (worktree / "opencode.json").write_text("{}\n", encoding="utf-8")

    assert require_inert_worktree(worktree) == worktree.resolve()


def test_a_missing_worktree_is_refused_rather_than_assumed_inert(tmp_path):
    with pytest.raises(ControlledConfigError):
        require_inert_worktree(tmp_path / "does-not-exist")


def test_an_inspected_config_that_is_not_a_mapping_fails_closed():
    with pytest.raises(ProbeRecordError):
        inspect_resolved_config(lambda: "not json")
    with pytest.raises(ProbeRecordError):
        inspect_resolved_config(lambda: None)


def test_an_inspected_config_missing_the_permission_key_is_not_assumed_safe():
    parsed = inspect_resolved_config(lambda: json.dumps({"plugin": [BROKER_PLUGIN_SPEC]}))
    assert parsed.bash_permission is None
    assert CapabilityGap.SHELL_PERMISSION_NOT_ASK in gaps_for(configuration=parsed)


def test_inspection_reads_only_the_fields_the_gate_needs():
    payload = {
        "model": "opencode/deepseek-v4.1-flash-free",
        "permission": {"bash": "ask"},
        "plugin": [BROKER_PLUGIN_SPEC],
        "provider": {"omniroute": {"options": {"apiKey": "secret-value"}}},
    }
    parsed = inspect_resolved_config(lambda: json.dumps(payload))
    assert parsed == resolved()
    assert "secret-value" not in repr(parsed)


def test_inspection_normalises_a_single_plugin_string():
    payload = {"permission": {"bash": "ask"}, "plugin": BROKER_PLUGIN_SPEC}
    assert inspect_resolved_config(lambda: json.dumps(payload)).plugins == (
        BROKER_PLUGIN_SPEC,
    )


def test_a_missing_probe_record_is_a_gap():
    assert CapabilityGap.LIVE_PROBE_MISSING in gaps_for(probe=None)


def test_probe_evidence_from_another_cli_version_does_not_count():
    gaps = gaps_for(probe=record(cli_version="1.19.5"))
    assert CapabilityGap.LIVE_PROBE_VERSION_MISMATCH in gaps


def test_probe_evidence_for_an_unverified_hook_does_not_count():
    gaps = gaps_for(probe=record(hook="prompt-instruction"))
    assert CapabilityGap.LIVE_PROBE_HOOK_UNSUPPORTED in gaps


def test_probe_evidence_that_found_a_bypass_does_not_count():
    gaps = gaps_for(probe=record(direct_shell_bypass_found=True))
    assert CapabilityGap.DIRECT_SHELL_BYPASS_FOUND in gaps


def test_probe_evidence_must_show_approval_pausing_and_denial_blocking():
    gaps = gaps_for(probe=record(approval_paused_the_command=False))
    assert CapabilityGap.LIVE_PROBE_INCOMPLETE in gaps
    gaps = gaps_for(probe=record(denial_prevented_execution=False))
    assert CapabilityGap.LIVE_PROBE_INCOMPLETE in gaps
    gaps = gaps_for(probe=record(approval_change_invalidated_the_request=False))
    assert CapabilityGap.LIVE_PROBE_INCOMPLETE in gaps


def test_gaps_are_reported_in_a_stable_documented_order():
    everything_wrong = evaluate_capability(
        cli_version="1.20.0",
        resolved=resolved(
            bash_permission="allow", plugins=(), mcp_servers=("evil",)
        ),
        probe=record(direct_shell_bypass_found=True),
    )
    assert everything_wrong.gaps == (
        CapabilityGap.CLI_VERSION_MISMATCH,
        CapabilityGap.SHELL_PERMISSION_NOT_ASK,
        CapabilityGap.BROKER_PLUGIN_MISSING,
        CapabilityGap.MCP_SERVERS_CONFIGURED,
        CapabilityGap.LIVE_PROBE_VERSION_MISMATCH,
        CapabilityGap.DIRECT_SHELL_BYPASS_FOUND,
    )


def test_a_verified_capability_becomes_a_ready_cli_capability():
    capability = evaluate_capability(
        cli_version=VERSION, resolved=resolved(), probe=record()
    )
    assert capability.is_verified
    assert capability.to_cli_capability().broker_verified is True
    assert capability.to_cli_capability().cli_version == VERSION


def test_an_unverified_capability_stays_blocking_and_explains_itself():
    capability = evaluate_capability(
        cli_version=VERSION, resolved=resolved(), probe=None
    )
    assert not capability.is_verified
    cli_capability = capability.to_cli_capability()
    assert cli_capability.broker_verified is False
    assert CapabilityGap.LIVE_PROBE_MISSING.value in cli_capability.unsupported_reason


def test_the_launch_environment_disables_project_configuration():
    environment = build_launch_environment(CONTROLLED_CONFIG)
    assert environment["OPENCODE_DISABLE_PROJECT_CONFIG"] == "1"
    assert environment["OPENCODE_CONFIG"] == CONTROLLED_CONFIG
    assert "OPENCODE_PURE" not in environment


def test_the_launch_environment_refuses_a_config_inside_a_repository():
    with pytest.raises(ControlledConfigError):
        build_launch_environment(f"{WORKTREE}\\opencode.json")
    with pytest.raises(ControlledConfigError):
        build_launch_environment("opencode.json")


def test_launch_arguments_reject_auto_approve_and_pure_mode():
    with pytest.raises(ControlledConfigError):
        validate_launch_arguments(["opencode", "run", "--auto", "fix the tests"])
    with pytest.raises(ControlledConfigError):
        validate_launch_arguments(["opencode", "run", "--pure", "fix the tests"])
    validate_launch_arguments(["opencode", "run", "--model", "opencode/x", "hi"])


def test_a_probe_record_round_trips_through_a_local_file(tmp_path):
    path = tmp_path / "broker-probe.json"
    save_probe_record(path, record())
    assert load_probe_record(path) == record()


def test_a_missing_or_malformed_probe_record_loads_as_none(tmp_path):
    assert load_probe_record(tmp_path / "absent.json") is None
    broken = tmp_path / "broken.json"
    broken.write_text("{ nope", encoding="utf-8")
    assert load_probe_record(broken) is None
    partial = tmp_path / "partial.json"
    partial.write_text(json.dumps({"cli_version": VERSION}), encoding="utf-8")
    assert load_probe_record(partial) is None


def test_a_probe_record_cannot_claim_a_bypass_and_still_be_verified():
    with pytest.raises(ProbeRecordError):
        LiveProbeRecord(
            cli_version=VERSION,
            hook="plugin.tool.execute.before",
            approval_paused_the_command=True,
            approval_change_invalidated_the_request=True,
            denial_prevented_execution=True,
            direct_shell_bypass_found=True,
            probe_directory=r"C:\Users\sumuk\AppData\Local\Temp\gitkeeper-probe",
            verified=True,
        )
