#!/usr/bin/env python3
"""Check staged (or all tracked) files without ever printing candidate secrets."""

import re
import subprocess
import sys
from pathlib import Path, PurePosixPath

all_files = "--all" in sys.argv
args = (
    ["git", "ls-files", "-z"]
    if all_files
    else ["git", "diff", "--cached", "--name-only", "--diff-filter=ACMR", "-z"]
)
files = subprocess.check_output(args).decode().split("\0")
patterns = [
    re.compile(rb"\bsk-[A-Za-z0-9_-]{20,}\b"),
    re.compile(rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    re.compile(
        rb"(?i)(?:api[_-]?key|access[_-]?token|authorization)\s*[=:]\s*[\"']?(?:Bearer\s+)?[A-Za-z0-9_-]{24,}"
    ),
]
blocked = []
# Compare the configured value only inside this process. Do not print it or
# include it in argv; this also catches nonstandard credential formats.
known_credentials = []
local_env = Path(".env.local")
if local_env.is_file():
    for line in local_env.read_text(encoding="utf-8").splitlines():
        if line.startswith("CHESSX_API_KEY="):
            value = line.split("=", 1)[1].strip().strip("\"'")
            if value:
                known_credentials.append(value.encode())
for name in filter(None, files):
    path = PurePosixPath(name)
    if (
        (path.name.startswith(".env") and path.name != ".env.example")
        or path.parts[0] in {"data", "exports", "logs", "local-scenarios"}
        or path.suffix in {".pem", ".key", ".db", ".sqlite", ".sqlite3"}
    ):
        blocked.append((name, "private file"))
        continue
    try:
        content = subprocess.check_output(["git", "show", ":" + name], stderr=subprocess.DEVNULL)
    except subprocess.CalledProcessError:
        continue
    if any(p.search(content) for p in patterns) or any(
        credential in content for credential in known_credentials
    ):
        blocked.append((name, "credential pattern"))
if blocked:
    for name, reason in blocked:
        print(f"Blocked staged file: {name} ({reason}); content suppressed", file=sys.stderr)
    sys.exit(1)
print("Secret guard passed; no candidate values were printed.")
