from __future__ import annotations

import re
from dataclasses import dataclass, field, replace as dataclass_replace
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


@dataclass(frozen=True)
class AflowSection:
    default_workflow: str | None = None
    keep_runs: int = DEFAULT_KEEP_RUNS
    retry_inconsistent_checkpoint_state: int = 0
    banner_files_limit: int = 10


@dataclass(frozen=True)
class WorkflowHarnessConfig:
    profiles: dict[str, HarnessProfileConfig] = field(default_factory=dict)


@dataclass(frozen=True)
class GoTransition:
    to: str
    when: str | None = None


@dataclass(frozen=True)
class WorkflowStepConfig:
    profile: str
    prompts: tuple[str, ...] = ()
    go: tuple[GoTransition, ...] = ()


@dataclass(frozen=True)
class WorkflowConfig:
    steps: dict[str, WorkflowStepConfig] = field(default_factory=dict)
    first_step: str | None = None
    retry_inconsistent_checkpoint_state: int | None = None


@dataclass(frozen=True)
class WorkflowUserConfig:
    aflow: AflowSection = field(default_factory=AflowSection)
    harnesses: dict[str, WorkflowHarnessConfig] = field(default_factory=dict)
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
        "retry_inconsistent_checkpoint_state",
        "banner_files_limit",
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
    return AflowSection(
        default_workflow=_optional_text(
            raw.get("default_workflow"), path=f"{path}.default_workflow"
        ),
        keep_runs=keep_runs,
        retry_inconsistent_checkpoint_state=retry_inconsistent_checkpoint_state,
        banner_files_limit=banner_files_limit,
    )


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
        allowed = {"profile", "prompts", "go"}
        unknown = sorted(set(step_table) - allowed)
        if unknown:
            raise ConfigError(
                f"unsupported keys in {path}.{step_key}: {', '.join(unknown)}"
            )
        if "profile" not in step_table:
            raise ConfigError(f"missing 'profile' in {path}.{step_key}")
        profile_value = _require_text(
            step_table["profile"], path=f"{path}.{step_key}.profile"
        )
        if "." not in profile_value:
            raise ConfigError(
                f"step profile must be fully qualified (harness.profile) "
                f"in {path}.{step_key}.profile, got '{profile_value}'"
            )
        harness_name, _, profile_name = profile_value.partition(".")
        if not harness_name or not profile_name:
            raise ConfigError(
                f"invalid profile selector '{profile_value}' "
                f"in {path}.{step_key}.profile"
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
            profile=profile_value,
            prompts=prompts,
            go=go_transitions,
        )
    return WorkflowConfig(steps=steps, first_step=first_step)


def _parse_workflow_user_config(
    raw: Mapping[str, object], *, path: Path
) -> WorkflowUserConfig:
    allowed_top = {"aflow", "harness", "workflow", "prompts"}
    unknown = sorted(set(raw) - allowed_top)
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
    workflows: dict[str, WorkflowConfig] = {}
    if "workflow" in raw:
        workflows_table = _require_table(raw["workflow"], path=f"{path}.workflow")
        for wf_name, wf_value in workflows_table.items():
            wf_key = _require_text(wf_name, path=f"{path}.workflow key")
            wf_table = _require_table(
                wf_value, path=f"{path}.workflow.{wf_key}"
            )
            wf_allowed = {"steps", "retry_inconsistent_checkpoint_state"}
            wf_unknown = sorted(set(wf_table) - wf_allowed)
            if wf_unknown:
                raise ConfigError(
                    f"unsupported keys in {path}.workflow.{wf_key}: "
                    f"{', '.join(wf_unknown)}"
                )
            if "steps" not in wf_table:
                raise ConfigError(f"missing 'steps' in {path}.workflow.{wf_key}")
            wf_retry: int | None = None
            if "retry_inconsistent_checkpoint_state" in wf_table:
                retry_value = wf_table["retry_inconsistent_checkpoint_state"]
                if not isinstance(retry_value, int) or isinstance(retry_value, bool) or retry_value < 0:
                    raise ConfigError(
                        f"{path}.workflow.{wf_key}.retry_inconsistent_checkpoint_state "
                        f"must be a non-negative integer"
                    )
                wf_retry = retry_value
            steps_table = _require_table(
                wf_table["steps"], path=f"{path}.workflow.{wf_key}.steps"
            )
            wf_config_base = _parse_workflow_steps(
                steps_table, path=f"{path}.workflow.{wf_key}.steps"
            )
            wf_config = dataclass_replace(wf_config_base, retry_inconsistent_checkpoint_state=wf_retry)
            known_steps = set(wf_config.steps)
            for step_name, step_config in wf_config.steps.items():
                for j, transition in enumerate(step_config.go):
                    if (
                        transition.to != "END"
                        and transition.to not in known_steps
                    ):
                        raise ConfigError(
                            f"transition target '{transition.to}' in "
                            f"{path}.workflow.{wf_key}.steps.{step_name}.go[{j}] "
                            f"does not reference a known step"
                        )
            workflows[wf_key] = wf_config
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
    return _parse_workflow_user_config(raw, path=path)


def bootstrap_config(config_path: Path | None = None) -> Path:
    path = config_path or _config_path()
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        config_text = resources.files("aflow").joinpath("aflow.toml").read_text(
            encoding="utf-8"
        )
        path.write_text(config_text, encoding="utf-8")
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
    for wf_name, wf_config in config.workflows.items():
        for step_name, step_config in wf_config.steps.items():
            parts = step_config.profile.split(".", 1)
            if len(parts) != 2:
                continue
            harness_name, profile_name = parts
            if harness_name not in config.harnesses:
                errors.append(
                    f"workflow.{wf_name}.steps.{step_name}.profile references "
                    f"unknown harness '{harness_name}'"
                )
            elif profile_name not in config.harnesses[harness_name].profiles:
                errors.append(
                    f"workflow.{wf_name}.steps.{step_name}.profile references "
                    f"unknown profile '{profile_name}' "
                    f"for harness '{harness_name}'"
                )
        for step_name, step_config in wf_config.steps.items():
            for prompt_index, prompt_key in enumerate(step_config.prompts):
                if prompt_key not in config.prompts:
                    errors.append(
                        f"workflow.{wf_name}.steps.{step_name}.prompts[{prompt_index}] "
                        f"references unknown prompt '{prompt_key}'"
                    )
    return errors
