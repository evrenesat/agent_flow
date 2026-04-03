from __future__ import annotations

import re
from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path
from collections.abc import Mapping
import tomllib

from .harnesses import ADAPTERS

VALID_CONDITION_SYMBOLS = frozenset({"DONE", "NEW_PLAN_EXISTS", "MAX_TURNS_REACHED"})


class ConfigError(RuntimeError):
    pass


@dataclass(frozen=True)
class HarnessProfileConfig:
    model: str | None = None
    effort: str | None = None


def _config_path() -> Path:
    return Path.home() / ".config" / "aflow" / "aflow.toml"


def _require_table(value: object, *, path: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ConfigError(f"expected {path} to be a table")
    return value


def _require_text(value: object, *, path: str) -> str:
    if not isinstance(value, str):
        raise ConfigError(f"expected {path} to be a string")
    text = value.strip()
    if not text:
        raise ConfigError(f"{path} must not be empty")
    return text


def _optional_text(value: object | None, *, path: str) -> str | None:
    if value is None:
        return None
    return _require_text(value, path=path)


def _parse_profile_table(raw: Mapping[str, object], *, path: str) -> HarnessProfileConfig:
    allowed = {"model", "effort"}
    unknown = sorted(set(raw) - allowed)
    if unknown:
        raise ConfigError(f"unsupported keys in {path}: {', '.join(unknown)}")
    return HarnessProfileConfig(
        model=_optional_text(raw.get("model"), path=f"{path}.model"),
        effort=_optional_text(raw.get("effort"), path=f"{path}.effort"),
    )


DEFAULT_KEEP_RUNS = 20


DEFAULT_MAX_TURNS = 15


DEFAULT_MAX_SAME_STEP_TURNS = 5


@dataclass(frozen=True)
class AflowSection:
    default_workflow: str | None = None
    keep_runs: int = DEFAULT_KEEP_RUNS
    max_turns: int = DEFAULT_MAX_TURNS
    retry_inconsistent_checkpoint_state: int = 0
    banner_files_limit: int = 10
    max_same_step_turns: int = DEFAULT_MAX_SAME_STEP_TURNS


@dataclass(frozen=True)
class WorkflowHarnessConfig:
    profiles: dict[str, HarnessProfileConfig] = field(default_factory=dict)


@dataclass(frozen=True)
class GoTransition:
    to: str
    when: str | None = None


@dataclass(frozen=True)
class WorkflowStepConfig:
    role: str
    prompts: tuple[str, ...] = ()
    go: tuple[GoTransition, ...] = ()


@dataclass(frozen=True)
class WorkflowConfig:
    steps: dict[str, WorkflowStepConfig] = field(default_factory=dict)
    first_step: str | None = None
    retry_inconsistent_checkpoint_state: int | None = None
    team: str | None = None
    extends: str | None = None


@dataclass(frozen=True)
class WorkflowUserConfig:
    aflow: AflowSection = field(default_factory=AflowSection)
    harnesses: dict[str, WorkflowHarnessConfig] = field(default_factory=dict)
    roles: dict[str, str] = field(default_factory=dict)
    teams: dict[str, dict[str, str]] = field(default_factory=dict)
    workflows: dict[str, WorkflowConfig] = field(default_factory=dict)
    prompts: dict[str, str] = field(default_factory=dict)


def _validate_condition_symbols(expression: str, *, path: str) -> None:
    tokens = re.findall(
        r"[A-Za-z_][A-Za-z0-9_]*"
        r"|&&"
        r"|\|\|"
        r"|!==?"
        r"|<>|<=|>=|==|[=<>+\-*/%]"
        r"|!"
        r"|[()]"
        r"|\S",
        expression,
    )
    allowed = VALID_CONDITION_SYMBOLS | {"&&", "||", "!", "(", ")"}
    invalid_tokens = sorted(set(t for t in tokens if t not in allowed))
    if invalid_tokens:
        raise ConfigError(
            f"unsupported tokens in {path}: {', '.join(invalid_tokens)}"
        )


def _parse_aflow_section(raw: Mapping[str, object], *, path: str) -> AflowSection:
    allowed = {
        "default_workflow",
        "keep_runs",
        "max_turns",
        "retry_inconsistent_checkpoint_state",
        "banner_files_limit",
        "max_same_step_turns",
    }
    unknown = sorted(set(raw) - allowed)
    if unknown:
        raise ConfigError(f"unsupported keys in {path}: {', '.join(unknown)}")
    keep_runs = DEFAULT_KEEP_RUNS
    if "keep_runs" in raw:
        value = raw["keep_runs"]
        if not isinstance(value, int) or isinstance(value, bool) or value < 1:
            raise ConfigError(f"{path}.keep_runs must be a positive integer")
        keep_runs = value
    max_turns = DEFAULT_MAX_TURNS
    if "max_turns" in raw:
        value = raw["max_turns"]
        if not isinstance(value, int) or isinstance(value, bool) or value < 1:
            raise ConfigError(f"{path}.max_turns must be a positive integer")
        max_turns = value
    retry_inconsistent_checkpoint_state = 0
    if "retry_inconsistent_checkpoint_state" in raw:
        value = raw["retry_inconsistent_checkpoint_state"]
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise ConfigError(
                f"{path}.retry_inconsistent_checkpoint_state must be a non-negative integer"
            )
        retry_inconsistent_checkpoint_state = value
    banner_files_limit = 10
    if "banner_files_limit" in raw:
        value = raw["banner_files_limit"]
        if not isinstance(value, int) or isinstance(value, bool) or value < 1:
            raise ConfigError(
                f"{path}.banner_files_limit must be a positive integer"
            )
        banner_files_limit = value
    max_same_step_turns = DEFAULT_MAX_SAME_STEP_TURNS
    if "max_same_step_turns" in raw:
        value = raw["max_same_step_turns"]
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise ConfigError(
                f"{path}.max_same_step_turns must be a non-negative integer"
            )
        max_same_step_turns = value
    return AflowSection(
        default_workflow=_optional_text(
            raw.get("default_workflow"), path=f"{path}.default_workflow"
        ),
        keep_runs=keep_runs,
        max_turns=max_turns,
        retry_inconsistent_checkpoint_state=retry_inconsistent_checkpoint_state,
        banner_files_limit=banner_files_limit,
        max_same_step_turns=max_same_step_turns,
    )


def _parse_selector_value(
    value: object,
    *,
    path: str,
) -> str:
    selector = _require_text(value, path=path)
    if "." not in selector:
        raise ConfigError(
            f"expected {path} to be a fully qualified harness.profile selector"
        )
    harness_name, _, profile_name = selector.partition(".")
    if not harness_name or not profile_name:
        raise ConfigError(f"invalid selector '{selector}' in {path}")
    return selector


def _parse_workflow_harness(
    raw: Mapping[str, object], *, path: str
) -> WorkflowHarnessConfig:
    allowed = {"profiles"}
    unknown = sorted(set(raw) - allowed)
    if unknown:
        raise ConfigError(f"unsupported keys in {path}: {', '.join(unknown)}")
    profiles: dict[str, HarnessProfileConfig] = {}
    profiles_value = raw.get("profiles")
    if profiles_value is not None:
        profiles_table = _require_table(profiles_value, path=f"{path}.profiles")
        for profile_name, profile_value in profiles_table.items():
            profile_key = _require_text(
                profile_name, path=f"{path}.profiles key"
            )
            profile_table = _require_table(
                profile_value, path=f"{path}.profiles.{profile_key}"
            )
            profiles[profile_key] = _parse_profile_table(
                profile_table, path=f"{path}.profiles.{profile_key}"
            )
    return WorkflowHarnessConfig(profiles=profiles)


def _parse_role_map(
    raw: Mapping[str, object], *, path: str
) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for key, value in raw.items():
        role_name = _require_text(key, path=f"{path} key")
        mapping[role_name] = _parse_selector_value(
            value, path=f"{path}.{role_name}"
        )
    return mapping


def _parse_go_transitions(
    raw_list: object, *, path: str
) -> tuple[GoTransition, ...]:
    if not isinstance(raw_list, list):
        raise ConfigError(f"expected {path}.go to be an array")
    transitions: list[GoTransition] = []
    for i, entry in enumerate(raw_list):
        entry_path = f"{path}.go[{i}]"
        if not isinstance(entry, Mapping):
            raise ConfigError(f"expected {entry_path} to be an inline table")
        if "to" not in entry:
            raise ConfigError(f"missing 'to' in {entry_path}")
        to_value = _require_text(entry["to"], path=f"{entry_path}.to")
        if to_value != "END" and not re.match(
            r"^[a-zA-Z_][a-zA-Z0-9_]*$", to_value
        ):
            raise ConfigError(
                f"invalid transition target '{to_value}' in {entry_path}.to"
            )
        when_value = entry.get("when")
        when_str: str | None = None
        if when_value is not None:
            when_str = _require_text(when_value, path=f"{entry_path}.when")
            _validate_condition_symbols(when_str, path=entry_path)
        transitions.append(GoTransition(to=to_value, when=when_str))
    return tuple(transitions)


def _parse_workflow_steps(
    raw: Mapping[str, object], *, path: str
) -> WorkflowConfig:
    steps: dict[str, WorkflowStepConfig] = {}
    first_step: str | None = None
    for step_name, step_value in raw.items():
        step_key = _require_text(step_name, path=f"{path} key")
        if first_step is None:
            first_step = step_key
        step_table = _require_table(step_value, path=f"{path}.{step_key}")
        allowed = {"role", "prompts", "go"}
        unknown = sorted(set(step_table) - allowed)
        if unknown:
            raise ConfigError(
                f"unsupported keys in {path}.{step_key}: {', '.join(unknown)}"
            )
        if "role" not in step_table:
            raise ConfigError(f"missing 'role' in {path}.{step_key}")
        role_value = _require_text(
            step_table["role"], path=f"{path}.{step_key}.role"
        )
        if "prompts" not in step_table:
            raise ConfigError(f"missing 'prompts' in {path}.{step_key}")
        prompts_value = step_table["prompts"]
        if not isinstance(prompts_value, list):
            raise ConfigError(f"expected {path}.{step_key}.prompts to be an array")
        if not prompts_value:
            raise ConfigError(f"{path}.{step_key}.prompts must not be empty")
        prompts = tuple(
            _require_text(p, path=f"{path}.{step_key}.prompts[{i}]")
            for i, p in enumerate(prompts_value)
        )
        if "go" not in step_table:
            raise ConfigError(f"missing 'go' in {path}.{step_key}")
        go_transitions = _parse_go_transitions(
            step_table["go"], path=f"{path}.{step_key}"
        )
        if not go_transitions:
            raise ConfigError(f"{path}.{step_key}.go must not be empty")
        steps[step_key] = WorkflowStepConfig(
            role=role_value,
            prompts=prompts,
            go=go_transitions,
        )
    return WorkflowConfig(steps=steps, first_step=first_step)


def _parse_workflow_definition(
    raw: Mapping[str, object], *, path: str
) -> WorkflowConfig:
    allowed = {"steps", "retry_inconsistent_checkpoint_state", "extends", "team"}
    unknown = sorted(set(raw) - allowed)
    if unknown:
        raise ConfigError(f"unsupported keys in {path}: {', '.join(unknown)}")
    wf_team = _optional_text(raw.get("team"), path=f"{path}.team")
    wf_extends = _optional_text(raw.get("extends"), path=f"{path}.extends")
    wf_retry: int | None = None
    if "retry_inconsistent_checkpoint_state" in raw:
        if wf_extends is not None:
            raise ConfigError(
                f"{path}.retry_inconsistent_checkpoint_state is not allowed on alias workflows"
            )
        retry_value = raw["retry_inconsistent_checkpoint_state"]
        if not isinstance(retry_value, int) or isinstance(retry_value, bool) or retry_value < 0:
            raise ConfigError(
                f"{path}.retry_inconsistent_checkpoint_state must be a non-negative integer"
            )
        wf_retry = retry_value
    steps: dict[str, WorkflowStepConfig] = {}
    first_step: str | None = None
    if wf_extends is None:
        if "steps" not in raw:
            raise ConfigError(f"missing 'steps' in {path}")
        steps_table = _require_table(raw["steps"], path=f"{path}.steps")
        wf_config = _parse_workflow_steps(steps_table, path=f"{path}.steps")
        _validate_workflow_transitions(wf_config, path=path)
        steps = wf_config.steps
        first_step = wf_config.first_step
    else:
        if "steps" in raw:
            raise ConfigError(f"{path} may not redefine 'steps' when using 'extends'")
    return WorkflowConfig(
        steps=steps,
        first_step=first_step,
        retry_inconsistent_checkpoint_state=wf_retry,
        team=wf_team,
        extends=wf_extends,
    )


def _parse_workflow_tables(
    raw: Mapping[str, object], *, path: str
) -> dict[str, WorkflowConfig]:
    workflows: dict[str, WorkflowConfig] = {}
    for wf_name, wf_value in raw.items():
        wf_key = _require_text(wf_name, path=f"{path} key")
        wf_table = _require_table(wf_value, path=f"{path}.{wf_key}")
        workflows[wf_key] = _parse_workflow_definition(
            wf_table, path=f"{path}.{wf_key}"
        )
    return workflows


def _validate_workflow_transitions(
    wf_config: WorkflowConfig,
    *,
    path: str,
) -> None:
    known_steps = set(wf_config.steps)
    for step_name, step_config in wf_config.steps.items():
        for index, transition in enumerate(step_config.go):
            if transition.to != "END" and transition.to not in known_steps:
                raise ConfigError(
                    f"transition target '{transition.to}' in "
                    f"{path}.steps.{step_name}.go[{index}] does not reference a known step"
                )


def _materialize_workflows(
    raw_workflows: dict[str, WorkflowConfig],
    *,
    path: str,
) -> dict[str, WorkflowConfig]:
    resolved: dict[str, WorkflowConfig] = {}
    resolving: set[str] = set()

    def resolve(name: str) -> WorkflowConfig:
        if name in resolved:
            return resolved[name]
        if name in resolving:
            raise ConfigError(f"workflow alias cycle detected at {path}.{name}")
        if name not in raw_workflows:
            raise ConfigError(f"workflow alias references unknown workflow '{name}'")
        resolving.add(name)
        raw_wf = raw_workflows[name]
        if raw_wf.extends is None:
            concrete = WorkflowConfig(
                steps=dict(raw_wf.steps),
                first_step=raw_wf.first_step,
                retry_inconsistent_checkpoint_state=raw_wf.retry_inconsistent_checkpoint_state,
                team=raw_wf.team,
                extends=None,
            )
            _validate_workflow_transitions(concrete, path=f"{path}.{name}")
        else:
            base_name = raw_wf.extends
            if base_name not in raw_workflows:
                raise ConfigError(
                    f"workflow '{name}' extends unknown workflow '{base_name}'"
                )
            base_raw = raw_workflows[base_name]
            if base_raw.extends is not None:
                raise ConfigError(
                    f"workflow '{name}' cannot extend alias workflow '{base_name}'"
                )
            base = resolve(base_name)
            concrete = WorkflowConfig(
                steps=dict(base.steps),
                first_step=base.first_step,
                retry_inconsistent_checkpoint_state=base.retry_inconsistent_checkpoint_state,
                team=raw_wf.team if raw_wf.team is not None else base.team,
                extends=None,
            )
            _validate_workflow_transitions(concrete, path=f"{path}.{name}")
        resolving.remove(name)
        resolved[name] = concrete
        return concrete

    for workflow_name in raw_workflows:
        resolve(workflow_name)
    return resolved


def _parse_workflow_user_config(
    raw: Mapping[str, object], *, path: Path, allowed_top_level_keys: set[str]
) -> WorkflowUserConfig:
    unknown = sorted(set(raw) - allowed_top_level_keys)
    if unknown:
        raise ConfigError(
            f"unsupported top-level keys in {path}: {', '.join(unknown)}"
        )
    aflow = AflowSection()
    if "aflow" in raw:
        aflow_table = _require_table(raw["aflow"], path=f"{path}.aflow")
        aflow = _parse_aflow_section(aflow_table, path=f"{path}.aflow")
    harnesses: dict[str, WorkflowHarnessConfig] = {}
    if "harness" in raw:
        harnesses_table = _require_table(raw["harness"], path=f"{path}.harness")
        for harness_name, harness_value in harnesses_table.items():
            harness_key = _require_text(harness_name, path=f"{path}.harness key")
            if harness_key not in ADAPTERS:
                supported = ", ".join(sorted(ADAPTERS))
                raise ConfigError(
                    f"unsupported harness '{harness_key}' in {path}, "
                    f"supported harnesses are: {supported}"
                )
            harness_table = _require_table(
                harness_value, path=f"{path}.harness.{harness_key}"
            )
            harnesses[harness_key] = _parse_workflow_harness(
                harness_table, path=f"{path}.harness.{harness_key}"
            )
    roles: dict[str, str] = {}
    if "roles" in raw:
        roles_table = _require_table(raw["roles"], path=f"{path}.roles")
        roles = _parse_role_map(roles_table, path=f"{path}.roles")
    teams: dict[str, dict[str, str]] = {}
    if "teams" in raw:
        teams_table = _require_table(raw["teams"], path=f"{path}.teams")
        for team_name, team_value in teams_table.items():
            team_key = _require_text(team_name, path=f"{path}.teams key")
            team_table = _require_table(team_value, path=f"{path}.teams.{team_key}")
            teams[team_key] = _parse_role_map(
                team_table, path=f"{path}.teams.{team_key}"
            )
    workflows: dict[str, WorkflowConfig] = {}
    if "workflow" in raw:
        workflows_table = _require_table(raw["workflow"], path=f"{path}.workflow")
        raw_workflows = _parse_workflow_tables(
            workflows_table, path=f"{path}.workflow"
        )
        workflows = _materialize_workflows(raw_workflows, path=f"{path}.workflow")
    prompts: dict[str, str] = {}
    if "prompts" in raw:
        prompts_table = _require_table(raw["prompts"], path=f"{path}.prompts")
        for prompt_name, prompt_value in prompts_table.items():
            prompt_key = _require_text(
                prompt_name, path=f"{path}.prompts key"
            )
            prompts[prompt_key] = _require_text(
                prompt_value, path=f"{path}.prompts.{prompt_key}"
            )
    return WorkflowUserConfig(
        aflow=aflow,
        harnesses=harnesses,
        roles=roles,
        teams=teams,
        workflows=workflows,
        prompts=prompts,
    )


def load_workflow_config(
    config_path: Path | None = None,
) -> WorkflowUserConfig:
    path = config_path or _config_path()
    if not path.exists():
        return WorkflowUserConfig()
    try:
        with path.open("rb") as handle:
            raw = tomllib.load(handle)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"invalid TOML in {path}: {exc}") from exc
    except OSError as exc:
        raise ConfigError(f"unable to read config file {path}: {exc}") from exc
    sibling_path = path.with_name("workflows.toml")
    aflow_allowed_top_level_keys = {"aflow", "harness", "roles", "teams", "prompts"}
    if sibling_path.exists():
        config = _parse_workflow_user_config(
            raw, path=path, allowed_top_level_keys=aflow_allowed_top_level_keys
        )
        try:
            with sibling_path.open("rb") as handle:
                sibling_raw = tomllib.load(handle)
        except tomllib.TOMLDecodeError as exc:
            raise ConfigError(
                f"invalid TOML in {sibling_path}: {exc}"
            ) from exc
        except OSError as exc:
            raise ConfigError(
                f"unable to read config file {sibling_path}: {exc}"
            ) from exc
        sibling_config = _parse_workflow_user_config(
            sibling_raw, path=sibling_path, allowed_top_level_keys={"workflow"}
        )
        merged = WorkflowUserConfig(
            aflow=config.aflow,
            harnesses=config.harnesses,
            roles=config.roles,
            teams=config.teams,
            workflows={**config.workflows, **sibling_config.workflows},
            prompts={**config.prompts, **sibling_config.prompts},
        )
    else:
        merged = _parse_workflow_user_config(
            raw, path=path, allowed_top_level_keys=aflow_allowed_top_level_keys
        )
    errors = validate_workflow_config(merged)
    if errors:
        raise ConfigError("; ".join(errors))
    return merged


def bootstrap_config(config_path: Path | None = None) -> Path:
    path = config_path or _config_path()
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        config_text = resources.files("aflow").joinpath("aflow.toml").read_text(
            encoding="utf-8"
        )
        workflow_text = resources.files("aflow").joinpath(
            "workflows.toml"
        ).read_text(encoding="utf-8")
        path.write_text(config_text, encoding="utf-8")
        path.with_name("workflows.toml").write_text(
            workflow_text, encoding="utf-8"
        )
    return path


def find_placeholders(config: WorkflowUserConfig) -> list[str]:
    placeholders: list[str] = []
    for harness_name, harness_config in config.harnesses.items():
        for profile_name, profile_config in harness_config.profiles.items():
            if profile_config.model == "FILL_IN_MODEL":
                placeholders.append(
                    f"harness.{harness_name}.profiles.{profile_name}.model"
                )
    return sorted(placeholders)


def validate_workflow_config(
    config: WorkflowUserConfig,
) -> list[str]:
    errors: list[str] = []
    if config.aflow.default_workflow is not None:
        if config.aflow.default_workflow not in config.workflows:
            errors.append(
                f"aflow.default_workflow references unknown workflow "
                f"'{config.aflow.default_workflow}'"
            )
    for role_name, selector in config.roles.items():
        if "." not in selector:
            errors.append(
                f"roles.{role_name} must be a fully qualified harness.profile selector"
            )
            continue
        harness_name, _, profile_name = selector.partition(".")
        if harness_name not in config.harnesses:
            errors.append(
                f"roles.{role_name} references unknown harness '{harness_name}'"
            )
        elif profile_name not in config.harnesses[harness_name].profiles:
            errors.append(
                f"roles.{role_name} references unknown profile '{profile_name}' "
                f"for harness '{harness_name}'"
            )
    for team_name, role_map in config.teams.items():
        for role_key, selector in role_map.items():
            if role_key not in config.roles:
                errors.append(
                    f"teams.{team_name}.{role_key} references unknown role '{role_key}'"
                )
            if "." not in selector:
                errors.append(
                    f"teams.{team_name}.{role_key} must be a fully qualified harness.profile selector"
                )
                continue
            harness_name, _, profile_name = selector.partition(".")
            if harness_name not in config.harnesses:
                errors.append(
                    f"teams.{team_name}.{role_key} references unknown harness '{harness_name}'"
                )
            elif profile_name not in config.harnesses[harness_name].profiles:
                errors.append(
                    f"teams.{team_name}.{role_key} references unknown profile '{profile_name}' "
                    f"for harness '{harness_name}'"
                )
    for wf_name, wf_config in config.workflows.items():
        if wf_config.extends is not None:
            errors.append(
                f"workflow.{wf_name}.extends should not be present after materialization"
            )
        known_roles = set(config.roles)
        for step_name, step_config in wf_config.steps.items():
            role_name = step_config.role
            if role_name not in known_roles:
                errors.append(
                    f"workflow.{wf_name}.steps.{step_name}.role references unknown role "
                    f"'{role_name}'"
                )
                continue
        for step_name, step_config in wf_config.steps.items():
            for prompt_index, prompt_key in enumerate(step_config.prompts):
                if prompt_key not in config.prompts:
                    errors.append(
                        f"workflow.{wf_name}.steps.{step_name}.prompts[{prompt_index}] "
                        f"references unknown prompt '{prompt_key}'"
                    )
    return errors
