import pytest

from gitkeeper.policy.models import DEFAULT_MODEL_ID, OWNER_LADDER, ModelLadder
from gitkeeper.ui.services.config_publisher import CompanionSettings, ConfigPullRequest
from gitkeeper.ui.viewmodels.settings import SettingsViewModel
from tests.fakes.fake_config_publisher import FakeConfigPublisher


def owner_ladder(active=DEFAULT_MODEL_ID):
    return ModelLadder.from_ids(OWNER_LADDER, active=active)


def make_isolation_report(tmp_path):
    from tests.isolation.test_gate import build_report

    evidence = tmp_path / "isolation-evidence.json"
    evidence.write_text("verified isolation evidence", encoding="utf-8")
    return build_report(evidence_paths=[evidence])


def test_unset_model_id_is_preserved_and_settings_remain_unmerged_until_pr_merge():
    initial = CompanionSettings(
        worker_enabled=False,
        polling_preferences={"mode": "adaptive"},
        motion_preference=None,
    )
    pull_request = ConfigPullRequest(
        url="https://github.com/SumukhaK/Setu-mobile/pull/12",
        state="open",
        validation_status="valid",
        merge_status="pending",
    )
    publisher = FakeConfigPublisher(pull_request)
    view_model = SettingsViewModel(publisher, initial_settings=initial)

    assert view_model.opencode_model_id == ""
    view_model.set_motion_preference("reduced")
    view_model.publish()

    assert view_model.config_pr_state == "open"
    assert view_model.merged_settings == initial
    assert not hasattr(publisher.published_settings[0], "opencode_model_id")

    merged = ConfigPullRequest(
        url=pull_request.url,
        state="merged",
        validation_status="valid",
        merge_status="merged",
    )
    publisher.statuses[pull_request.url] = merged
    view_model.refresh_config_pr_status()

    assert view_model.config_pr_state == "merged"
    assert view_model.merged_settings.motion_preference == "reduced"


def test_worker_cannot_be_enabled_until_command_mediation_is_verified():
    pull_request = ConfigPullRequest(
        url="https://github.com/SumukhaK/Setu-mobile/pull/12",
        state="open",
        validation_status="valid",
        merge_status="pending",
    )
    publisher = FakeConfigPublisher(pull_request)
    view_model = SettingsViewModel(publisher)

    view_model.set_worker_enabled(True)

    assert view_model.can_enable_worker is False
    assert view_model.publish() is False
    assert "command mediation" in view_model.validation_message
    assert publisher.published_settings == []


def test_historical_isolation_report_is_not_a_worker_readiness_input(tmp_path):
    pull_request = ConfigPullRequest(
        url="https://github.com/SumukhaK/Setu-mobile/pull/12",
        state="open",
        validation_status="valid",
        merge_status="pending",
    )
    publisher = FakeConfigPublisher(pull_request)

    with pytest.raises(TypeError):
        SettingsViewModel(
            publisher,
            isolation_report=make_isolation_report(tmp_path),
        )


def test_boolean_cannot_be_used_as_worker_readiness():
    pull_request = ConfigPullRequest(
        url="https://github.com/SumukhaK/Setu-mobile/pull/12",
        state="open",
        validation_status="valid",
        merge_status="pending",
    )
    publisher = FakeConfigPublisher(pull_request)

    with pytest.raises(TypeError):
        SettingsViewModel(publisher, isolation_report=True)


def test_model_selection_is_local_and_never_part_of_config_pr():
    settings = CompanionSettings(
        worker_enabled=False,
        polling_preferences={"mode": "adaptive"},
        motion_preference=None,
    )
    pull_request = ConfigPullRequest(
        url="https://github.com/SumukhaK/Setu-mobile/pull/12",
        state="open",
        validation_status="valid",
        merge_status="pending",
    )
    publisher = FakeConfigPublisher(pull_request)
    view_model = SettingsViewModel(
        publisher,
        initial_settings=settings,
        model_ladder=owner_ladder(),
    )

    assert view_model.opencode_model_id == DEFAULT_MODEL_ID
    assert view_model.publish() is True
    assert publisher.published_settings[0] == settings
    assert not hasattr(publisher.published_settings[0], "opencode_model_id")


def test_model_ladder_is_exposed_in_order_for_the_desktop_picker():
    view_model = SettingsViewModel(FakeConfigPublisher(None), model_ladder=owner_ladder())

    assert view_model.available_models == list(OWNER_LADDER)
    assert view_model.next_model_suggestion == "opencode/glm-5.3"


def test_switching_the_model_updates_only_the_local_selection():
    view_model = SettingsViewModel(FakeConfigPublisher(None), model_ladder=owner_ladder())

    view_model.use_model("opencode/hy4-flash-free")

    assert view_model.opencode_model_id == "opencode/hy4-flash-free"
    assert view_model.next_model_suggestion == "opencode/deepseek-v4-pro"


def test_switching_to_a_model_outside_the_ladder_is_rejected():
    view_model = SettingsViewModel(FakeConfigPublisher(None), model_ladder=owner_ladder())

    view_model.use_model("opencode/not-in-ladder")

    assert view_model.opencode_model_id == DEFAULT_MODEL_ID
    assert "ladder" in view_model.validation_message


def test_switching_to_another_provider_is_rejected():
    view_model = SettingsViewModel(FakeConfigPublisher(None), model_ladder=owner_ladder())

    view_model.use_model("freellmapi/auto")

    assert view_model.opencode_model_id == DEFAULT_MODEL_ID
    assert "ladder" in view_model.validation_message


def test_without_a_local_ladder_no_model_is_selected():
    view_model = SettingsViewModel(FakeConfigPublisher(None))

    assert view_model.available_models == []
    assert view_model.opencode_model_id == ""
    assert view_model.next_model_suggestion == ""


def test_selecting_a_model_never_unlocks_the_worker():
    pull_request = ConfigPullRequest(
        url="https://github.com/SumukhaK/Setu-mobile/pull/12",
        state="open",
        validation_status="valid",
        merge_status="pending",
    )
    publisher = FakeConfigPublisher(pull_request)
    view_model = SettingsViewModel(publisher, model_ladder=owner_ladder())

    view_model.use_model("opencode/glm-5.3")
    view_model.set_worker_enabled(True)

    assert view_model.can_enable_worker is False
    assert view_model.publish() is False


def test_model_selection_rejects_non_opencode_provider_ids():
    publisher = FakeConfigPublisher(None)
    view_model = SettingsViewModel(publisher)

    view_model.set_opencode_model_id("external/provider-model")

    assert view_model.opencode_model_id == ""
    assert "ladder" in view_model.validation_message


def test_rejected_config_pr_keeps_merged_settings_unchanged():
    initial = CompanionSettings(
        worker_enabled=False,
        polling_preferences={"mode": "adaptive"},
        motion_preference=None,
    )
    pull_request = ConfigPullRequest(
        url="https://github.com/SumukhaK/Setu-mobile/pull/12",
        state="open",
        validation_status="valid",
        merge_status="pending",
    )
    publisher = FakeConfigPublisher(pull_request)
    view_model = SettingsViewModel(publisher, initial_settings=initial)
    view_model.set_motion_preference("disabled")
    view_model.publish()
    publisher.statuses[pull_request.url] = ConfigPullRequest(
        url=pull_request.url,
        state="rejected",
        validation_status="valid",
        merge_status="rejected",
    )

    view_model.refresh_config_pr_status()

    assert view_model.config_pr_state == "rejected"
    assert view_model.merged_settings == initial


def test_invalid_merged_config_pr_does_not_update_merged_settings():
    initial = CompanionSettings(worker_enabled=False, motion_preference=None)
    pull_request = ConfigPullRequest(
        url="https://github.com/SumukhaK/Setu-mobile/pull/12",
        state="open",
        validation_status="valid",
        merge_status="pending",
    )
    publisher = FakeConfigPublisher(pull_request)
    view_model = SettingsViewModel(publisher, initial_settings=initial)
    view_model.set_motion_preference("reduced")
    view_model.publish()
    publisher.statuses[pull_request.url] = ConfigPullRequest(
        url=pull_request.url,
        state="merged",
        validation_status="invalid",
        merge_status="merged",
    )

    view_model.refresh_config_pr_status()

    assert view_model.merged_settings == initial
