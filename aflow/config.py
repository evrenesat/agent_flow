from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from collections.abc import Mapping
import tomllib

from .harnesses import ADAPTERS


class ConfigError(RuntimeError):
    pass


@dataclass(frozen=True)
class HarnessProfileConfig:
    model: str | None = None
    effort: str | None = None


@dataclass(frozen=True)
class HarnessConfig:
    model: str | None = None
    effort: str | None = None
    profiles: dict[str, HarnessProfileConfig] = field(default_factory=dict)


@dataclass(frozen=True)
class UserConfig:
    default_harness: str | None = None
    harnesses: dict[str, HarnessConfig] = field(default_factory=dict)


@dataclass(frozen=True)
class ResolvedConfig:
    harness: str
    model: str | None
    effort: str | None
    profile: str | None = None


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


def _parse_harness_table(raw: Mapping[str, object], *, path: str) -> HarnessConfig:
    allowed = {"model", "effort", "profiles"}
    unknown = sorted(set(raw) - allowed)
    if unknown:
        raise ConfigError(f"unsupported keys in {path}: {', '.join(unknown)}")

    profiles_value = raw.get("profiles")
    profiles: dict[str, HarnessProfileConfig] = {}
    if profiles_value is not None:
        profiles_table = _require_table(profiles_value, path=f"{path}.profiles")
        for profile_name, profile_value in profiles_table.items():
            profile_key = _require_text(profile_name, path=f"{path}.profiles key")
            profile_table = _require_table(profile_value, path=f"{path}.profiles.{profile_key}")
            profiles[profile_key] = _parse_profile_table(profile_table, path=f"{path}.profiles.{profile_key}")

    return HarnessConfig(
        model=_optional_text(raw.get("model"), path=f"{path}.model"),
        effort=_optional_text(raw.get("effort"), path=f"{path}.effort"),
        profiles=profiles,
    )


def _parse_user_config(raw: Mapping[str, object], *, path: Path) -> UserConfig:
    allowed = {"default_harness", "harness"}
    unknown = sorted(set(raw) - allowed)
    if unknown:
        raise ConfigError(f"unsupported keys in {path}: {', '.join(unknown)}")

    default_harness = _optional_text(raw.get("default_harness"), path=f"{path}.default_harness")
    if default_harness is not None and default_harness not in ADAPTERS:
        supported = ", ".join(sorted(ADAPTERS))
        raise ConfigError(
            f"unsupported default_harness '{default_harness}' in {path}, supported harnesses are: {supported}"
        )

    harnesses: dict[str, HarnessConfig] = {}
    harnesses_value = raw.get("harness")
    if harnesses_value is not None:
        harnesses_table = _require_table(harnesses_value, path=f"{path}.harness")
        for harness_name, harness_value in harnesses_table.items():
            harness_key = _require_text(harness_name, path=f"{path}.harness key")
            if harness_key not in ADAPTERS:
                supported = ", ".join(sorted(ADAPTERS))
                raise ConfigError(
                    f"unsupported harness '{harness_key}' in {path}, supported harnesses are: {supported}"
                )
            harness_table = _require_table(harness_value, path=f"{path}.harness.{harness_key}")
            harnesses[harness_key] = _parse_harness_table(harness_table, path=f"{path}.harness.{harness_key}")

    return UserConfig(default_harness=default_harness, harnesses=harnesses)


def load_user_config() -> UserConfig:
    path = _config_path()
    if not path.exists():
        return UserConfig()
    try:
        with path.open("rb") as handle:
            raw = tomllib.load(handle)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"invalid TOML in {path}: {exc}") from exc
    except OSError as exc:
        raise ConfigError(f"unable to read config file {path}: {exc}") from exc
    return _parse_user_config(raw, path=path)


def resolve_launch_config(
    config: UserConfig,
    *,
    harness: str | None,
    model: str | None,
    effort: str | None,
    profile: str | None,
) -> ResolvedConfig:
    resolved_harness = harness or config.default_harness
    if resolved_harness is None:
        raise ConfigError("--harness is required unless default_harness is configured")
    if resolved_harness not in ADAPTERS:
        supported = ", ".join(sorted(ADAPTERS))
        raise ConfigError(f"unsupported harness '{resolved_harness}', supported harnesses are: {supported}")

    harness_config = config.harnesses.get(resolved_harness)

    if profile is not None:
        if harness_config is None or profile not in harness_config.profiles:
            raise ConfigError(f"unknown profile '{profile}' for harness '{resolved_harness}'")
        profile_config = harness_config.profiles[profile]
    else:
        profile_config = None

    resolved_model = model
    if resolved_model is None and profile_config is not None:
        resolved_model = profile_config.model
    if resolved_model is None and harness_config is not None:
        resolved_model = harness_config.model

    resolved_effort = effort
    if resolved_effort is None and profile_config is not None:
        resolved_effort = profile_config.effort
    if resolved_effort is None and harness_config is not None:
        resolved_effort = harness_config.effort

    return ResolvedConfig(
        harness=resolved_harness,
        model=resolved_model,
        effort=resolved_effort,
        profile=profile,
    )
