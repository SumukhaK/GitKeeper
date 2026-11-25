import json
from pathlib import Path

import pytest

from gitkeeper.policy.models import (
    DEFAULT_MODEL_ID,
    OWNER_LADDER,
    LocalModelPolicy,
    ModelLadder,
    ModelPolicyError,
    classify_model_failure,
    default_ladder,
    is_opencode_model_id,
    load_ladder,
    normalize_model_id,
)


def write_policy(path: Path, payload: dict) -> Path:
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_default_ladder_keeps_the_owner_selected_model_first():
    ladder = ModelLadder.from_ids(OWNER_LADDER)

    assert ladder.active == DEFAULT_MODEL_ID
    assert ladder.entries == (
        "opencode/deepseek-v4.1-flash-free",
        "opencode/glm-5.3",
        "opencode/glm-5.3-flash",
        "opencode/hy4-flash-free",
        "opencode/deepseek-v4-pro",
    )


def test_bare_model_ids_are_normalized_to_the_opencode_provider():
    assert normalize_model_id("glm-5.3") == "opencode/glm-5.3"
    assert normalize_model_id("  opencode/glm-5.3  ") == "opencode/glm-5.3"


def test_other_providers_are_never_normalized_into_the_opencode_namespace():
    assert not is_opencode_model_id("freellmapi/auto")
    assert not is_opencode_model_id("omniroute/auto")
    with pytest.raises(ModelPolicyError):
        normalize_model_id("freellmapi/auto")
    with pytest.raises(ModelPolicyError):
        normalize_model_id("opencode/")
    with pytest.raises(ModelPolicyError):
        normalize_model_id("opencode/model with spaces")
    with pytest.raises(ModelPolicyError):
        normalize_model_id("")


def test_duplicate_ladder_entries_are_rejected():
    with pytest.raises(ModelPolicyError):
        ModelLadder.from_ids(["opencode/glm-5.3", "opencode/glm-5.3"])


def test_empty_ladder_is_rejected_because_no_launch_is_safe():
    with pytest.raises(ModelPolicyError):
        ModelLadder.from_ids([])


def test_active_model_must_be_a_ladder_entry():
    with pytest.raises(ModelPolicyError):
        ModelLadder.from_ids(["opencode/glm-5.3"], active="opencode/other-model")


def test_next_after_walks_the_ladder_and_stops_at_the_end():
    ladder = ModelLadder.from_ids(OWNER_LADDER)

    assert ladder.next_after(DEFAULT_MODEL_ID) == "opencode/glm-5.3"
    assert ladder.next_after("opencode/glm-5.3") == "opencode/glm-5.3-flash"
    assert ladder.next_after("opencode/deepseek-v4-pro") is None
    assert ladder.next_after("opencode/not-in-ladder") is None


def test_switching_the_active_model_keeps_the_ladder_order():
    ladder = ModelLadder.from_ids(OWNER_LADDER)

    switched = ladder.with_active("opencode/hy4-flash-free")

    assert switched.active == "opencode/hy4-flash-free"
    assert switched.entries == ladder.entries
    assert ladder.active == DEFAULT_MODEL_ID


def test_switching_to_an_unlisted_model_is_rejected():
    ladder = ModelLadder.from_ids(OWNER_LADDER)

    with pytest.raises(ModelPolicyError):
        ladder.with_active("opencode/not-in-ladder")


def test_policy_loads_the_local_file_and_round_trips_the_active_model(tmp_path):
    path = write_policy(
        tmp_path / "models.json",
        {
            "version": 1,
            "active": "opencode/glm-5.3",
            "ladder": list(OWNER_LADDER),
        },
    )

    policy = LocalModelPolicy.load(path)

    assert policy.ladder.active == "opencode/glm-5.3"
    assert policy.require_launchable() == "opencode/glm-5.3"

    policy.use("opencode/deepseek-v4.1-flash-free").save()

    reloaded = LocalModelPolicy.load(path)
    assert reloaded.ladder.active == DEFAULT_MODEL_ID
    assert reloaded.ladder.entries == policy.ladder.entries


def test_missing_local_policy_file_blocks_the_worker(tmp_path):
    with pytest.raises(ModelPolicyError) as error:
        LocalModelPolicy.load(tmp_path / "missing.json")

    assert "models.json" in str(error.value)


def test_unknown_policy_keys_are_rejected_so_typos_fail_closed(tmp_path):
    path = write_policy(
        tmp_path / "models.json",
        {
            "version": 1,
            "active": DEFAULT_MODEL_ID,
            "ladder": list(OWNER_LADDER),
            "fallback": ["opencode/glm-5.3"],
        },
    )

    with pytest.raises(ModelPolicyError) as error:
        LocalModelPolicy.load(path)

    assert "fallback" in str(error.value)


def test_unsupported_policy_version_is_rejected(tmp_path):
    path = write_policy(
        tmp_path / "models.json",
        {"version": 2, "active": DEFAULT_MODEL_ID, "ladder": list(OWNER_LADDER)},
    )

    with pytest.raises(ModelPolicyError):
        LocalModelPolicy.load(path)


def test_policy_file_that_is_not_json_blocks_the_worker(tmp_path):
    path = tmp_path / "models.json"
    path.write_text("opencode/glm-5.3", encoding="utf-8")

    with pytest.raises(ModelPolicyError):
        LocalModelPolicy.load(path)


def test_default_policy_path_lives_outside_both_repositories(monkeypatch, tmp_path):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))

    path = LocalModelPolicy.default_path()

    assert path == tmp_path / "GitKeeper" / "models.json"


def test_quota_exhaustion_is_model_unavailable_and_never_consumes_the_repair_budget():
    ladder = ModelLadder.from_ids(OWNER_LADDER)

    failure = classify_model_failure(
        ladder=ladder,
        model_id=DEFAULT_MODEL_ID,
        stderr="Error: insufficient quota for this model (429)",
        exit_code=1,
    )

    assert failure.category == "model-unavailable"
    assert failure.consumes_repair_budget is False
    assert failure.suggestion == "opencode/glm-5.3"
    assert failure.requires_owner_action is True


def test_quota_exhaustion_on_the_last_ladder_entry_offers_no_suggestion():
    ladder = ModelLadder.from_ids(["opencode/deepseek-v4-pro"], active="opencode/deepseek-v4-pro")

    failure = classify_model_failure(
        ladder=ladder,
        model_id="opencode/deepseek-v4-pro",
        stderr="rate limit reached",
        exit_code=1,
    )

    assert failure.category == "model-unavailable"
    assert failure.suggestion is None


def test_quota_text_is_matched_case_insensitively():
    ladder = ModelLadder.from_ids(OWNER_LADDER)

    failure = classify_model_failure(
        ladder=ladder,
        model_id=DEFAULT_MODEL_ID,
        stderr="Weekly quota exhausted",
        exit_code=None,
    )

    assert failure.category == "model-unavailable"


def test_a_crashed_run_is_a_worker_failure_that_consumes_the_repair_budget():
    ladder = ModelLadder.from_ids(OWNER_LADDER)

    failure = classify_model_failure(
        ladder=ladder,
        model_id=DEFAULT_MODEL_ID,
        stderr="TypeError: cannot read properties of undefined",
        exit_code=2,
    )

    assert failure.category == "worker-failure"
    assert failure.consumes_repair_budget is True
    assert failure.suggestion is None


def test_default_ladder_is_the_owner_approved_order():
    ladder = default_ladder()

    assert ladder.active == DEFAULT_MODEL_ID
    assert ladder.entries == tuple(
        "opencode/" + name
        for name in (
            "deepseek-v4.1-flash-free",
            "glm-5.3",
            "glm-5.3-flash",
            "hy4-flash-free",
            "deepseek-v4-pro",
        )
    )


def test_load_ladder_prefers_the_saved_local_policy(tmp_path: Path):
    path = write_policy(
        tmp_path / "models.json",
        {"version": 1, "active": "opencode/glm-5.3", "ladder": ["opencode/glm-5.3"]},
    )

    assert load_ladder(path).active == "opencode/glm-5.3"


def test_load_ladder_falls_back_to_the_default_when_no_policy_exists(tmp_path: Path):
    assert load_ladder(tmp_path / "missing.json").entries == OWNER_LADDER


def test_load_ladder_falls_back_to_the_default_when_the_policy_is_unreadable(
    tmp_path: Path,
):
    path = tmp_path / "models.json"
    path.write_text("not json", encoding="utf-8")

    ladder = load_ladder(path)

    assert ladder.active == DEFAULT_MODEL_ID
    assert ladder.entries == OWNER_LADDER
