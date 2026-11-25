"""Strict readiness-boundary adapters for queue configuration and reasons."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from gitkeeper.host_mode.readiness import (
    RepairReason as ReadinessRepairReason,
)
from gitkeeper.queue.models import PilotConfig, RepairReason


def to_readiness_reason(reason: RepairReason) -> ReadinessRepairReason:
    """Translate only automatic queue reasons into readiness wire values."""

    if not isinstance(reason, RepairReason):
        raise ValueError("Invalid repair reason.")
    if reason is RepairReason.deterministic_check_failure:
        return ReadinessRepairReason.DETERMINISTIC_CHECK_FAILURE
    if reason is RepairReason.claude_feedback:
        return ReadinessRepairReason.REVIEW_FEEDBACK
    raise ValueError("Manual retry is not an automatic launch reason.")


def _invalid_config() -> ValueError:
    return ValueError("Invalid pilot configuration.")


def _is_sha(value: object) -> bool:
    if not isinstance(value, str):
        return False
    if len(value) == 40:
        return all(character in "0123456789abcdef" for character in value)
    if len(value) == 64:
        return all(character in "0123456789abcdef" for character in value)
    return False


def parse_pilot_config(
    payload: object,
    default_branch: str,
    default_branch_sha: str,
) -> PilotConfig:
    """Parse only supported remote payload keys, with trusted branch defaults.

    The returned model is immutable, including its copied polling mapping. Any
    malformed or unsupported configuration gets one fixed public error message.
    """

    try:
        if not isinstance(payload, Mapping):
            raise _invalid_config()
        payload_mapping: Mapping[str, Any] = payload
        required = {
            "worker_enabled",
            "polling_preferences",
            "motion_preference",
            "deterministic_check_ids",
        }
        if set(payload_mapping) != required:
            raise _invalid_config()
        if not isinstance(payload_mapping["polling_preferences"], Mapping):
            raise _invalid_config()
        polling = dict(payload_mapping["polling_preferences"])
        if polling != {"mode": "adaptive"}:
            raise _invalid_config()
        check_ids = payload_mapping["deterministic_check_ids"]
        if not isinstance(check_ids, (list, tuple)) or not all(
            isinstance(check_id, str) for check_id in check_ids
        ):
            raise _invalid_config()
        return PilotConfig(
            worker_enabled=payload_mapping["worker_enabled"],
            polling_preferences=polling,
            motion_preference=payload_mapping["motion_preference"],
            deterministic_check_ids=tuple(check_ids),
            default_branch=default_branch,
            default_branch_sha=default_branch_sha,
        )
    except (KeyError, TypeError, ValueError, OverflowError):
        raise _invalid_config() from None
