"""AWS high-availability compliance scorer.

    from assessment.resilience import score

    report = score(payload)        # dict
    html = score(payload, "html")  # str
"""
from .api import score
from .input_loader import InputError
from .report.html_report import render_html

__all__ = ["InputError", "render_html", "score"]
