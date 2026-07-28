"""AWS high-availability compliance scorer.

    from hascore import main

    report = main(payload)        # dict
    html = main(payload, "html")  # str
"""
from .api import main
from .input_loader import InputError
from .report.html_report import render_html

__all__ = ["InputError", "main", "render_html"]
