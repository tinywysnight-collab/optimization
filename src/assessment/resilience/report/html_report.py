"""Render the self-contained HTML report from the JSON report dict (spec §9)."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader

_TEMPLATE_DIR = Path(__file__).parent


def render_html(report: dict[str, Any]) -> str:
    # autoescape must be unconditionally True: select_autoescape matches on the
    # filename suffix, and "template.html.j2" ends in ".j2", so it would leave
    # escaping OFF — reasons/tags/resource names are externally influenced and
    # would become a stored-XSS vector in the rendered report.
    env = Environment(
        loader=FileSystemLoader(_TEMPLATE_DIR),
        autoescape=True,
    )
    return env.get_template("template.html.j2").render(report=report)
