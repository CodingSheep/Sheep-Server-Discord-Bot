"""The player wiki's shared login (nginx basic auth).

nginx reads the password file on every request, so a change applies at once,
with no reload. Hashes are SHA-512 crypt: nginx checks the hash on every request
(a page load is dozens of them), and bcrypt would make that slow. The strength
comes from the password itself: generated ones are 16 random characters.
"""
import os
import secrets
import subprocess

from core import config

WIKI_USER = "player"
# No look-alikes (0/o, 1/l/i), so it's easy to read out or type.
ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"
MIN_LEN = 12


def generate() -> str:
    """Like `k7pq-x2mf-9tdh-r4wn` (~79 bits)."""
    return "-".join("".join(secrets.choice(ALPHABET) for _ in range(4)) for _ in range(4))


def set_password(password: str) -> tuple[bool, str]:
    if len(password) < MIN_LEN:
        return False, f"Use at least {MIN_LEN} characters (or leave it empty to generate one)."
    if len(password) > 128 or any(ord(c) < 32 for c in password):
        return False, "That password has unsupported characters or is too long."

    # Password goes in on stdin, so it never shows in a process list.
    r = subprocess.run(["openssl", "passwd", "-6", "-stdin"], input=password + "\n",
                       capture_output=True, text=True, timeout=20)
    hashed = r.stdout.strip()
    if r.returncode != 0 or not hashed.startswith("$6$"):
        return False, f"Hashing the password failed: {(r.stderr or r.stdout).strip()[-200:]}"

    path = config.WIKI_HTPASSWD
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        f.write(f"{WIKI_USER}:{hashed}\n")
    os.chmod(tmp, 0o644)   # nginx runs as another user; the file only holds a hash
    os.replace(tmp, path)
    return True, "ok"
