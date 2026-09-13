"""敏感信息自查脚本。"""

import pathlib
import re
import sys

PATTERNS = [
    r'sk-[A-Za-z0-9]{20,}',
    r'password\s*=\s*["\'][^"\']{4,}["\']',
    r'DEEPSEEK_API_KEY\s*=\s*sk-',
]

SKIP_DIRS = {".venv", "__pycache__", ".git", "node_modules"}
SKIP_NAMES = {"example", "check_secrets"}

found = []
root = pathlib.Path(".")
for p in root.rglob("*"):
    if p.is_dir():
        continue
    parts = set(p.parts)
    if parts & SKIP_DIRS:
        continue
    if any(kw in p.name.lower() for kw in SKIP_NAMES):
        continue
    if p.suffix not in (".py", ".yaml", ".yml", ".env", ".cfg", ".toml", ".txt"):
        continue
    try:
        text = p.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        continue
    for pat in PATTERNS:
        m = re.search(pat, text)
        if m:
            found.append(f"{p}: {m.group()[:60]}")

if found:
    print("SECRET LEAKS DETECTED:")
    for f in found:
        print("  !!!", f)
    sys.exit(1)
else:
    print("No hardcoded secrets found - OK")
