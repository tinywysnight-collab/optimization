"""Render the self-contained HTML report from the JSON report dict (spec §9)."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, select_autoescape

_TEMPLATE_DIR = Path(__file__).parent


def render_html(report: dict[str, Any]) -> str:
    env = Environment(
        loader=FileSystemLoader(_TEMPLATE_DIR),
        autoescape=select_autoescape(["html"]),
    )
    return env.get_template("template.html.j2").render(report=report)
