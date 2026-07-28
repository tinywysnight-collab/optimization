"""Match input accounts to AWS CLI profiles via ~/.aws/config (spec §3)."""
from __future__ import annotations

import configparser
from pathlib import Path

from .models import AccountSpec


class ProfileResolutionError(Exception):
    pass


def load_profiles(config_path: str | Path | None = None) -> dict[str, list[str]]:
    """Return {account_id: [profile names]} from sso_account_id entries."""
    path = Path(config_path) if config_path else Path.home() / ".aws" / "config"
    parser = configparser.ConfigParser()
    parser.read(path)
    mapping: dict[str, list[str]] = {}
    for section in parser.sections():
        if section == "default":
            name = "default"
        elif section.startswith("profile "):
            name = section[len("profile "):]
        else:
            continue
        account = parser[section].get("sso_account_id")
        if account:
            mapping.setdefault(account, []).append(name)
    return mapping


def resolve_profile(spec: AccountSpec, mapping: dict[str, list[str]]) -> str:
    if spec.profile:
        return spec.profile
    matches = mapping.get(spec.account_id, [])
    if len(matches) == 1:
        return matches[0]
    if not matches:
        raise ProfileResolutionError(
            f"no profile with sso_account_id={spec.account_id} in AWS config; "
            "set an explicit 'profile' for this account"
        )
    raise ProfileResolutionError(
        f"account {spec.account_id} matches multiple profiles {sorted(matches)}; "
        "set an explicit 'profile' to disambiguate"
    )
