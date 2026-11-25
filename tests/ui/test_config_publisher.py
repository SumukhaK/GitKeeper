import json

import pytest

from gitkeeper.ui.services.config_publisher import (
    CompanionSettings,
    ConfigPullRequest,
    ConfigPublisher,
    InvalidSettingsError,
    validate_settings,
)
from tests.fakes.fake_config_publisher import FakeConfigPRClient


def make_isolation_report(tmp_path):
    from tests.isolation.test_gate import build_report

    evidence = tmp_path / "isolation-evidence.json"
    evidence.write_text("verified isolation evidence", encoding="utf-8")
    return build_report(evidence_paths=[evidence])


def make_pull_request(state="open"):
    return ConfigPullRequest(
        url="https://github.com/SumukhaK/Setu-mobile/pull/12",
        state=state,
        validation_status="valid",
        merge_status="pending" if state == "open" else state,
    )


def make_config():
    return {
        "worker_enabled": False,
        "polling_preferences": {"mode": "adaptive"},
        "motion_preference": None,
    }


def test_invalid_or_secret_settings_never_create_a_config_pr():
    client = FakeConfigPRClient(make_config(), make_pull_request())
    publisher = ConfigPublisher(client)

    errors = validate_settings(
        {
            "worker_enabled": False,
            "polling_preferences": {"mode": "adaptive"},
            "motion_preference": None,
            "github_token": "must-not-be-published",
        }
    )

    assert any(error.field == "github_token" for error in errors)
    with pytest.raises(InvalidSettingsError):
        publisher.publish_settings(
            {
                "worker_enabled": False,
                "opencode_model_id": None,
                "polling_preferences": {"mode": "adaptive"},
                "motion_preference": None,
                "github_token": "must-not-be-published",
            }
        )
    assert not any(call[0] == "create_or_update_config_pr" for call in client.calls)


def test_invalid_nested_preference_type_is_reported_as_a_field_error():
    errors = validate_settings(
        {
            "worker_enabled": False,
            "polling_preferences": {"mode": "adaptive"},
            "motion_preference": [],
        }
    )

    assert any(error.field == "motion_preference" for error in errors)


def test_publish_includes_only_remote_settings_in_config_pr():
    client = FakeConfigPRClient(make_config(), make_pull_request())
    publisher = ConfigPublisher(client)
    settings = CompanionSettings(
        worker_enabled=False,
        polling_preferences={"mode": "adaptive"},
        motion_preference="reduced",
    )

    result = publisher.publish_settings(settings)

    assert result.state == "open"
    call = next(call for call in client.calls if call[0] == "create_or_update_config_pr")
    _, repository, branch, files, _, _ = call
    config = json.loads(files[".github/gitkeeper.json"])
    assert repository == "SumukhaK/Setu-mobile"
    assert branch != "main"
    assert set(config) == {
        "worker_enabled",
        "polling_preferences",
        "motion_preference",
    }
    assert config["motion_preference"] == "reduced"


def test_remote_config_rejects_local_model_ids():
    client = FakeConfigPRClient(make_config(), make_pull_request())
    publisher = ConfigPublisher(client)
    settings = {
        "worker_enabled": False,
        "opencode_model_id": "opencode/subscription-model",
        "polling_preferences": {"mode": "adaptive"},
        "motion_preference": None,
    }

    with pytest.raises(InvalidSettingsError, match="opencode_model_id"):
        publisher.publish_settings(settings)

    assert not any(call[0] == "create_or_update_config_pr" for call in client.calls)


def test_worker_enabled_publication_stays_blocked_until_command_mediation_is_verified():
    client = FakeConfigPRClient(make_config(), make_pull_request())
    publisher = ConfigPublisher(client)
    settings = CompanionSettings(
        worker_enabled=True,
        polling_preferences={"mode": "adaptive"},
        motion_preference=None,
    )

    with pytest.raises(InvalidSettingsError, match="command mediation"):
        publisher.publish_settings(settings)

    assert not any(call[0] == "create_or_update_config_pr" for call in client.calls)


def test_historical_isolation_report_is_not_a_worker_authorization_input(tmp_path):
    client = FakeConfigPRClient(make_config(), make_pull_request())
    report = make_isolation_report(tmp_path)

    with pytest.raises(TypeError):
        ConfigPublisher(client, isolation_report=report)


def test_unknown_or_secret_fields_in_default_config_are_rejected():
    default_config = make_config()
    default_config["github_token"] = "must-not-be-published"
    client = FakeConfigPRClient(default_config, make_pull_request())
    publisher = ConfigPublisher(client)

    with pytest.raises(InvalidSettingsError, match="github_token"):
        publisher.publish_settings(CompanionSettings())

    assert not any(call[0] == "create_or_update_config_pr" for call in client.calls)


def test_config_status_transitions_are_read_from_the_pr():
    client = FakeConfigPRClient(make_config(), make_pull_request())
    publisher = ConfigPublisher(client)
    client.statuses["https://github.com/SumukhaK/Setu-mobile/pull/12"] = (
        make_pull_request("merged")
    )

    status = publisher.get_config_pr_status(
        "https://github.com/SumukhaK/Setu-mobile/pull/12"
    )

    assert status.state == "merged"
    assert status.merge_status == "merged"
