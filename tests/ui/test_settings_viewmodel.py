from gitkeeper.ui.services.config_publisher import CompanionSettings, ConfigPullRequest
from gitkeeper.ui.viewmodels.settings import SettingsViewModel
from tests.fakes.fake_config_publisher import FakeConfigPublisher


def test_unset_model_id_is_preserved_and_settings_remain_unmerged_until_pr_merge():
    initial = CompanionSettings(
        worker_enabled=False,
        opencode_model_id=None,
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
    assert publisher.published_settings[0].opencode_model_id is None

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


def test_worker_cannot_be_enabled_before_isolation_gate_passes():
    pull_request = ConfigPullRequest(
        url="https://github.com/SumukhaK/Setu-mobile/pull/12",
        state="open",
        validation_status="valid",
        merge_status="pending",
    )
    publisher = FakeConfigPublisher(pull_request)
    view_model = SettingsViewModel(publisher, isolation_verified=False)

    view_model.set_worker_enabled(True)

    assert view_model.can_enable_worker is False
    assert view_model.publish() is False
    assert publisher.published_settings == []


def test_rejected_config_pr_keeps_merged_settings_unchanged():
    initial = CompanionSettings(
        worker_enabled=False,
        opencode_model_id=None,
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
