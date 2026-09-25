#!/usr/bin/env python3
"""Store one OpenRouter API key outside the repository for local pilot runs."""

import getpass
import os
import stat
import tempfile
from pathlib import Path


KEY_FILE = Path.home() / ".config" / "jobsss" / "openrouter.key"


def read_key(path=KEY_FILE):
    if path.is_symlink():
        raise ValueError(f"Refusing symlinked key file: {path}")
    try:
        info = path.stat()
    except FileNotFoundError:
        return None
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise ValueError(f"Key file must be owned by you and readable only by you: {path}")
    return path.read_text(encoding="utf-8").strip() or None


def save_key(path=KEY_FILE):
    if path.is_symlink():
        raise ValueError(f"Refusing symlinked key file: {path}")
    key = getpass.getpass("OpenRouter API key (hidden; saved locally): ").strip()
    if not key:
        raise ValueError("Empty key was not saved")
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(path.parent, 0o700)
    descriptor, temporary = tempfile.mkstemp(prefix=".openrouter-", dir=path.parent)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(key + "\n")
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    print(f"Saved OpenRouter key to {path} (permissions 0600)")


if __name__ == "__main__":
    save_key()
