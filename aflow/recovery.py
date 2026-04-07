from __future__ import annotations

import json
from dataclasses import asdict
from dataclasses import dataclass
from typing import TYPE_CHECKING

from .config import HarnessErrorRecoveryConfig, HarnessErrorRecoveryRuleConfig, TeamConfig
from .plan import PlanSnapshot
from .run_state import HarnessRecoveryAction, HarnessRecoveryContext, HarnessRecoverySource

if TYPE_CHECKING:
    from collections.abc import Sequence


TEAM_LEAD_RECOVERY_SKILL_NAME = "aflow-harness-recovery-lead"
TEAM_LEAD_RECOVERY_BUILTIN_INSTRUCTION = (
    f"Use the `{TEAM_LEAD_RECOVERY_SKILL_NAME}` skill to decide the next harness recovery action."
)


@dataclass(frozen=True)
class TeamLeadRecoveryDecision:
    action: HarnessRecoveryAction
    delay_seconds: int | None
    reason: str
    suggested_keywords: tuple[str, ...]
    suggested_action: HarnessRecoveryAction | None


class TeamLeadRecoveryDecisionError(ValueError):
    pass


def build_recovery_evidence(
    stdout: str | None,
    stderr: str | None,
    error: str | None,
) -> str:
    parts = [text.strip() for text in (stdout, stderr, error) if text and text.strip()]
    return "\n".join(parts)


def extract_recovery_terms(
    stdout: str | None,
    stderr: str | None,
    error: str | None,
) -> tuple[str, ...]:
    parts = []
    for text in (stdout, stderr, error):
        if text and text.strip():
            parts.append(text)
    return tuple(parts)


def find_first_matching_rule(
    config: HarnessErrorRecoveryConfig,
    *,
    stdout: str | None,
    stderr: str | None,
    error: str | None,
) -> tuple[HarnessErrorRecoveryRuleConfig | None, tuple[str, ...]]:
    evidence = build_recovery_evidence(stdout, stderr, error)
    if not evidence or not config.rules:
        return None, ()
    lowered = evidence.casefold()
    for rule in config.rules:
        matched_terms = tuple(term for term in rule.match if term.casefold() in lowered)
        if len(matched_terms) == len(rule.match):
            return rule, matched_terms
    return None, ()


def recovery_made_progress(
    snapshot_before: PlanSnapshot,
    snapshot_after: PlanSnapshot | None,
) -> bool:
    if snapshot_after is None:
        return False
    if snapshot_after.is_complete:
        return True
    return snapshot_after != snapshot_before


def resolve_backup_team(
    current_team: str | None,
    teams: dict[str, TeamConfig],
) -> tuple[str | None, str | None]:
    if current_team is None:
        return None, "no workflow team is configured for this run"
    team_config = teams.get(current_team)
    if team_config is None:
        return None, f"team '{current_team}' is not defined"
    backup_team = team_config.backup_team
    if backup_team is None:
        return None, f"team '{current_team}' does not configure a backup_team"
    if backup_team not in teams:
        return None, f"team '{current_team}' backup_team '{backup_team}' is not defined"
    if backup_team == current_team:
        return None, f"team '{current_team}' backup_team points to itself"
    return backup_team, None


def build_team_lead_recovery_prompt(
    *,
    step_path: str,
    current_team: str | None,
    active_selector: str,
    harness_name: str,
    model: str | None,
    returncode: int,
    snapshot_before: PlanSnapshot,
    snapshot_after: PlanSnapshot | None,
    stdout: str | None,
    stderr: str | None,
    recovery_reason: str,
    recovery_cap: int,
    consecutive_count: int,
    matched_rule_action: str | None,
    matched_terms: tuple[str, ...],
    backup_team: str | None,
) -> str:
    evidence = build_recovery_evidence(stdout=stdout, stderr=stderr, error=recovery_reason)
    snapshot_before_text = json.dumps(snapshot_before.to_dict(), indent=2, sort_keys=True)
    snapshot_after_text = (
        json.dumps(snapshot_after.to_dict(), indent=2, sort_keys=True)
        if snapshot_after is not None
        else "null"
    )
    lines = [
        TEAM_LEAD_RECOVERY_BUILTIN_INSTRUCTION,
        f"Step path: {step_path}",
        f"Current team: {current_team or 'none'}",
        f"Resolved selector: {active_selector}",
        f"Harness: {harness_name}",
        f"Model: {model or 'default'}",
        f"Return code: {returncode}",
        f"Consecutive recoveries: {consecutive_count}/{recovery_cap}",
        f"Matched deterministic action: {matched_rule_action or 'none'}",
        f"Matched deterministic terms: {', '.join(matched_terms) if matched_terms else 'none'}",
        f"Configured backup team: {backup_team or 'none'}",
        "Snapshot before:",
        snapshot_before_text,
        "Snapshot after:",
        snapshot_after_text,
        "Failure evidence:",
        evidence or "(none)",
        "Return exactly one JSON object with these keys:",
        '  action, delay_seconds, reason, suggested_keywords, suggested_action',
        "Use only one of these actions: retry_same_team_after_delay, switch_to_backup_team_and_retry, fail_immediately.",
        "Set suggested_action to null or one of the same actions.",
        "Return no prose, markdown, or code fences.",
    ]
    return "\n\n".join(lines)


def parse_team_lead_recovery_decision(text: str) -> TeamLeadRecoveryDecision:
    stripped = text.strip()
    if not stripped:
        raise TeamLeadRecoveryDecisionError("team lead recovery response was empty")
    try:
        raw = json.loads(stripped)
    except json.JSONDecodeError as exc:
        raise TeamLeadRecoveryDecisionError("team lead recovery response was not valid JSON") from exc
    if not isinstance(raw, dict):
        raise TeamLeadRecoveryDecisionError("team lead recovery response must be a JSON object")

    required_keys = {"action", "delay_seconds", "reason", "suggested_keywords", "suggested_action"}
    missing_keys = sorted(required_keys.difference(raw))
    unexpected_keys = sorted(set(raw).difference(required_keys))
    if missing_keys or unexpected_keys:
        parts: list[str] = []
        if missing_keys:
            parts.append(f"missing required keys: {', '.join(missing_keys)}")
        if unexpected_keys:
            parts.append(f"unexpected keys: {', '.join(unexpected_keys)}")
        raise TeamLeadRecoveryDecisionError(
            f"team lead recovery response has {' and '.join(parts)}"
        )

    action_raw = raw["action"]
    if not isinstance(action_raw, str):
        raise TeamLeadRecoveryDecisionError("team lead recovery action must be a string")
    if action_raw not in {"retry_same_team_after_delay", "switch_to_backup_team_and_retry", "fail_immediately"}:
        raise TeamLeadRecoveryDecisionError(f"unsupported team lead recovery action: {action_raw}")

    delay_seconds_raw = raw["delay_seconds"]
    if delay_seconds_raw is not None:
        if isinstance(delay_seconds_raw, bool) or not isinstance(delay_seconds_raw, int):
            raise TeamLeadRecoveryDecisionError("team lead recovery delay_seconds must be an integer or null")
        if delay_seconds_raw < 0:
            raise TeamLeadRecoveryDecisionError("team lead recovery delay_seconds must not be negative")

    reason_raw = raw["reason"]
    if not isinstance(reason_raw, str) or not reason_raw.strip():
        raise TeamLeadRecoveryDecisionError("team lead recovery reason must be a non-empty string")

    suggested_keywords_raw = raw["suggested_keywords"]
    if not isinstance(suggested_keywords_raw, list):
        raise TeamLeadRecoveryDecisionError("team lead recovery suggested_keywords must be a list")
    suggested_keywords: list[str] = []
    for item in suggested_keywords_raw:
        if not isinstance(item, str):
            raise TeamLeadRecoveryDecisionError("team lead recovery suggested_keywords must contain strings only")
        suggested_keywords.append(item)

    suggested_action_raw = raw["suggested_action"]
    if suggested_action_raw is not None:
        if not isinstance(suggested_action_raw, str):
            raise TeamLeadRecoveryDecisionError("team lead recovery suggested_action must be a string or null")
        if suggested_action_raw not in {"retry_same_team_after_delay", "switch_to_backup_team_and_retry", "fail_immediately"}:
            raise TeamLeadRecoveryDecisionError(
                f"unsupported team lead recovery suggested_action: {suggested_action_raw}"
            )

    return TeamLeadRecoveryDecision(
        action=action_raw,
        delay_seconds=delay_seconds_raw,
        reason=reason_raw.strip(),
        suggested_keywords=tuple(suggested_keywords),
        suggested_action=suggested_action_raw,
    )


def build_recovery_context(
    *,
    source: HarnessRecoverySource,
    action: HarnessRecoveryAction,
    reason: str,
    match_terms: tuple[str, ...] = (),
    matched_terms: tuple[str, ...] = (),
    delay_seconds: int | None = 0,
    from_team: str | None = None,
    to_team: str | None = None,
    consecutive_count: int = 0,
    suggested_keywords: tuple[str, ...] = (),
    suggested_action: HarnessRecoveryAction | None = None,
    executed: bool = True,
    rejection_reason: str | None = None,
) -> HarnessRecoveryContext:
    return HarnessRecoveryContext(
        source=source,
        action=action,
        reason=reason,
        match_terms=match_terms,
        matched_terms=matched_terms,
        delay_seconds=delay_seconds,
        from_team=from_team,
        to_team=to_team,
        consecutive_count=consecutive_count,
        suggested_keywords=suggested_keywords,
        suggested_action=suggested_action,
        executed=executed,
        rejection_reason=rejection_reason,
    )


def build_recovery_payload(
    recovery: HarnessRecoveryContext | None,
    history: Sequence[HarnessRecoveryContext],
) -> dict[str, object]:
    return {
        "recovery_summary": asdict(recovery) if recovery is not None else None,
        "recovery_history": [asdict(item) for item in history],
    }
