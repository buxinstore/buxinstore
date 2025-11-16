import os
import time
from typing import Any, Callable, Iterable, Optional

from flask import current_app, render_template


def _get_int_env(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except Exception:
        return default


def _get_float_env(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except Exception:
        return default


"""
Legacy SMTP email worker.

All production email sending now uses the Resend-based queue in app.utils.email_queue.
This module is kept only for historical reference and is not used by current routes.
"""
