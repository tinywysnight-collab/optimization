"""Enforce the prime directive: this tool must never modify a scanned account.

Documentation alone cannot hold that line — a future edit can add a write call
without anyone noticing. These tests parse the shipped source and fail if any
AWS operation outside the read-only set appears, so the guarantee is checked on
every run rather than trusted.
"""
import ast
import re
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parent.parent / "src" / "assessment" / "resilience"

# AWS operation names are snake_case with a leading verb. Only these verbs read.
READ_VERBS = ("describe", "list", "get")

# Verbs that mutate. Each needs a trailing token so ordinary Python methods
# (set.add, dict.update, dict.copy) are not mistaken for AWS operations.
WRITE_VERBS = (
    "create", "delete", "modify", "put", "update", "terminate", "reboot", "reset",
    "attach", "detach", "associate", "disassociate", "register", "deregister",
    "authorize", "revoke", "enable", "disable", "start", "stop", "restore",
    "copy", "import", "export", "tag", "untag", "add", "remove", "apply",
    "promote", "failover", "purchase", "cancel", "accept", "reject", "set",
    "attach", "replace", "move", "migrate", "upgrade", "downgrade", "rotate",
    "run",
)
_WRITE_SHAPED = re.compile(rf"^(?:{'|'.join(WRITE_VERBS)})_[a-z0-9_]+$")
_OP_SHAPED = re.compile(r"^[a-z]+_[a-z0-9_]+$")

# Operation-shaped names that are Python, not AWS, and are therefore exempt.
_NOT_AWS = frozenset({
    "read_text", "write_text", "get_paginator", "setdefault", "default_factory",
    # sts:AssumeRole vends temporary credentials and changes nothing in the
    # target account: it is how spec §3 reaches each account, and it does not
    # breach the read-only directive. Listed explicitly so the exemption is a
    # deliberate, reviewed decision rather than a gap in the verb list.
    "assume_role",
    "get_caller_identity",  # STS identity check — read-only
})


def _source_files() -> list[Path]:
    return sorted(SRC.rglob("*.py"))


def _names_in(path: Path) -> set[str]:
    """Every attribute name and string constant in a module, as candidate ops."""
    tree = ast.parse(path.read_text())
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute):
            found.add(node.attr)
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            found.add(node.value)
    return found


def _client_operations_in(path: Path) -> set[str]:
    """Operations referenced on objects created by session.client(...)."""
    tree = ast.parse(path.read_text())
    client_names = {"client"}
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        value = node.value
        if not isinstance(value, ast.Call) or not isinstance(value.func, ast.Attribute) \
                or value.func.attr != "client":
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        client_names.update(target.id for target in targets if isinstance(target, ast.Name))

    operations: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Attribute):
            continue
        receiver = node.value
        assigned_client = isinstance(receiver, ast.Name) and receiver.id in client_names
        injected_sts = isinstance(receiver, ast.Attribute) and receiver.attr == "_sts"
        chained_client = isinstance(receiver, ast.Call) \
            and isinstance(receiver.func, ast.Attribute) and receiver.func.attr == "client"
        if assigned_client or injected_sts or chained_client:
            operations.add(node.attr)
    return operations


def _non_read_client_operations(path: Path) -> set[str]:
    return {
        operation for operation in _client_operations_in(path)
        if operation not in _NOT_AWS and not operation.startswith(READ_VERBS)
    }


def test_source_tree_is_not_empty():
    """Guard the guard: a typo in SRC would make every check below vacuous."""
    assert len(_source_files()) >= 15


@pytest.mark.parametrize("path", _source_files(), ids=lambda p: p.name)
def test_no_mutating_aws_operation_appears(path: Path):
    offenders = {
        name for name in _names_in(path)
        if name not in _NOT_AWS and _WRITE_SHAPED.match(name)
    }
    assert not offenders, (
        f"{path.name} references AWS operation(s) that would modify a scanned "
        f"account: {sorted(offenders)}. This tool is strictly read-only."
    )


def test_every_operation_passed_to_the_fetch_helpers_is_read_only():
    """The fetch layer names its operations as string literals; all must read."""
    fetch = SRC / "scanners" / "aws_fetch.py"
    tree = ast.parse(fetch.read_text())
    ops: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) \
                and node.func.id in {"_paginate", "_collect_next_token"}:
            for arg in node.args:
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str) \
                        and _OP_SHAPED.match(arg.value):
                    ops.add(arg.value)
    assert ops, "no operations found — the fetch layer's shape must have changed"
    assert all(op.startswith(READ_VERBS) for op in ops), sorted(ops)


def test_client_operations_across_the_source_tree_are_read_only():
    """Positive allowlist over every recognized boto client, in every module."""
    operations = set().union(*(_client_operations_in(path) for path in _source_files()))
    offenders = set().union(*(_non_read_client_operations(path) for path in _source_files()))
    assert operations, "no client operations found — client discovery must have changed"
    assert not offenders, sorted(offenders)


def test_assume_role_is_the_only_credential_operation_allowed():
    """Pin the exemption: sts:AssumeRole is permitted, its mutating neighbours
    in the STS API are not."""
    assert "assume_role" in _NOT_AWS
    for forbidden in ("create_role", "delete_role", "put_role_policy", "update_assume_role_policy"):
        assert _WRITE_SHAPED.match(forbidden), forbidden
        assert forbidden not in _NOT_AWS


def test_guard_would_catch_a_write_call():
    """Prove the detector works rather than passing because it matches nothing."""
    assert _WRITE_SHAPED.match("run_instances")
    assert _WRITE_SHAPED.match("delete_db_instance")
    assert _WRITE_SHAPED.match("modify_db_cluster")
    assert _WRITE_SHAPED.match("create_replication_group")
    assert _WRITE_SHAPED.match("put_bucket_policy")
    # ordinary Python must not trip it
    assert not _WRITE_SHAPED.match("add")
    assert not _WRITE_SHAPED.match("update")
    assert not _WRITE_SHAPED.match("copy")


def test_positive_allowlist_catches_unusual_write_verbs(tmp_path):
    source = tmp_path / "future_scanner.py"
    source.write_text(
        "def scan(session):\n"
        "    client = session.client('ec2')\n"
        "    client.run_instances()\n"
        "    client.invoke()\n"
        "    client.send_command()\n"
        "    client.publish()\n"
        "    client.batch_write_item()\n"
    )
    assert _non_read_client_operations(source) == {
        "batch_write_item", "invoke", "publish", "run_instances", "send_command",
    }
