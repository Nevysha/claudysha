#!/bin/bash
# Install (or uninstall) the claudysha plugin for the current user.
# Safe to run again: it skips the steps that are already done.
#   ./install.sh            install (settings: ~/.config/claudysha/claudysha.conf, created from claudysha.conf.example)
#   ./install.sh --remove   uninstall (keeps the home folder)
set -euo pipefail
export PATH="$HOME/.local/bin:$PATH"   # claude's native install, also when run from Ansible
dir="$(cd "$(dirname "$0")" && pwd)"
plugin="claudysha@nevysha"

if [ "${1:-}" = "--remove" ]; then
    claude plugin uninstall "$plugin" --keep-data 2>/dev/null || true
    claude plugin marketplace remove nevysha 2>/dev/null || true
    echo "claudysha removed (its home folder is kept: $("$dir/claudysha" home))"
    exit 0
fi

command -v claude >/dev/null || { echo "claude CLI not found" >&2; exit 1; }
python3 -c 'import sqlite3; sqlite3.connect(":memory:").execute("create virtual table t using fts5(x)")' \
    || { echo "python3 with SQLite FTS5 is required" >&2; exit 1; }

# Writes ~/.config/claudysha/claudysha.conf if missing (asks questions in a terminal, defaults
# otherwise), then creates the home folder.
"$dir/claudysha" setup

claude plugin validate "$dir" >/dev/null
changed=no
if ! claude plugin marketplace list 2>/dev/null | grep -q nevysha; then
    claude plugin marketplace add "$dir"
    changed=yes
fi
if ! claude plugin list 2>/dev/null | grep -q "$plugin"; then
    claude plugin install "$plugin" --scope user
    changed=yes
fi
if [ "$changed" = yes ]; then
    echo "claudysha installed: hooks and skills load in new sessions (or after /reload-plugins)"
else
    echo "claudysha already installed"
fi
