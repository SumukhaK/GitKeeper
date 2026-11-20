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
        "opencode_model_id": None,
        "polling_preferences": {"mode": "adaptive"},
        "motion_preference": None,
    }


def test_invalid_or_secret_settings_never_create_a_config_pr():
    client = FakeConfigPRClient(make_config(), make_pull_request())
    publisher = ConfigPublisher(client)

    errors = validate_settings(
        {
            "worker_enabled": False,
            "opencode_model_id": None,
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
            "opencode_model_id": None,
            "polling_preferences": {"mode": "adaptive"},
            "motion_preference": [],
        }
    )

    assert any(error.field == "motion_preference" for error in errors)


def test_publish_uses_user_selected_settings_and_config_pr_port():
    client = FakeConfigPRClient(make_config(), make_pull_request())
    publisher = ConfigPublisher(client, isolation_verified=True)
    settings = CompanionSettings(
        worker_enabled=False,
        opencode_model_id="provider/user-selected-model",
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
    assert config["opencode_model_id"] == "provider/user-selected-model"
    assert config["motion_preference"] == "reduced"


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
