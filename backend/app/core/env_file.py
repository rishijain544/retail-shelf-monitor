"""
backend/app/core/env_file.py

Minimal, targeted writer for ``backend/.env``.

Only used by the "email setup" flow: an operator pastes a mail-provider API key into
the app, the backend verifies it with the provider, and only then persists it here.
Everything else in the file is preserved byte-for-byte (comments, ordering, CRLF line
endings), so this never rewrites an operator's configuration wholesale.
"""

from __future__ import annotations

import os
import re
import tempfile

from app.core.config import _BACKEND_DIR

ENV_PATH = os.path.join(_BACKEND_DIR, ".env")


def _pattern(name: str) -> re.Pattern[str]:
    # Anchored to the start of a line, tolerant of CRLF and trailing spaces.
    return re.compile(rf"^{re.escape(name)}=[^\r\n]*(?=\r?$)", re.MULTILINE)


def upsert(name: str, value: str, path: str | None = None) -> str:
    """Set ``name=value`` in the env file, returning the path that was written.

    The value must not contain a newline — a pasted key with a stray line break would
    otherwise turn into a second (garbage) variable.
    """
    if "\n" in value or "\r" in value:
        raise ValueError(f"{name} value must be a single line")

    target = path or ENV_PATH
    text = ""
    if os.path.isfile(target):
        with open(target, "r", encoding="utf-8-sig") as fh:
            text = fh.read()

    line = f"{name}={value}"
    pattern = _pattern(name)
    if pattern.search(text):
        text = pattern.sub(lambda _m: line, text, count=1)
    else:
        if text and not text.endswith(("\n", "\r")):
            text += "\n"
        text += line + "\n"

    directory = os.path.dirname(target) or "."
    fd, tmp_path = tempfile.mkstemp(prefix=".env-", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as fh:
            fh.write(text)
        os.replace(tmp_path, target)
    except BaseException:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        raise

    return target
