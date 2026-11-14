#!/bin/bash
# Install (or uninstall) the claudysha plugin for the current user.
# Safe to run again: it skips the steps that are already done.
#   ./install.sh            install (settings: ~/.config/claudysha/claudysha.conf, created from claudysha.conf.example)
#   ./install.sh --dev      install, then turn dev mode on (asks you to confirm, see the README)
#   ./install.sh --path     install, and link `claudysha` and `mem` into ~/.local/bin without asking
#   ./install.sh --remove   uninstall (keeps the home folder, removes those links)
# In a terminal, it asks whether to add `claudysha` and `mem` to your PATH (links in ~/.local/bin).
set -euo pipefail
export PATH="$HOME/.local/bin:$PATH"   # claude's native install, also when run from Ansible
dir="$(cd "$(dirname "$0")" && pwd)"
plugin="claudysha@nevysha"
bindir="$HOME/.local/bin"
commands=(claudysha mem)

# Our link in ~/.local/bin, or nothing: never touch a file we didn't put there.
ours() { [ -L "$bindir/$1" ] && [ "$(readlink -f "$bindir/$1")" = "$(readlink -f "$dir/$1")" ]; }

link_commands() {
    mkdir -p "$bindir"
    for c in "${commands[@]}"; do
        if ours "$c"; then continue; fi
        if [ -e "$bindir/$c" ] && [ ! -L "$bindir/$c" ]; then
            echo "skipped $bindir/$c: a file of that name is already there" >&2; continue
        fi
        ln -sfn "$dir/$c" "$bindir/$c"   # a link to another clone is replaced by this one
        echo "linked $bindir/$c"
    done
    case ":$PATH:" in
        *":$bindir:"*) ;;
        *) echo "note: $bindir is not on your PATH yet; add it in your shell's startup file" ;;
    esac
}

if [ "${1:-}" = "--remove" ]; then
    claude plugin uninstall "$plugin" --keep-data 2>/dev/null || true
    claude plugin marketplace remove nevysha 2>/dev/null || true
    for c in "${commands[@]}"; do
        if ours "$c"; then rm "$bindir/$c"; echo "removed $bindir/$c"; fi
    done
    echo "claudysha removed (its home folder is kept: $("$dir/claudysha" home))"
    exit 0
fi

command -v claude >/dev/null || { echo "claude CLI not found" >&2; exit 1; }
python3 -c 'import sqlite3; sqlite3.connect(":memory:").execute("create virtual table t using fts5(x)")' \
    || { echo "python3 with SQLite FTS5 is required" >&2; exit 1; }

# Writes ~/.config/claudysha/claudysha.conf if missing (asks questions in a terminal, defaults
# otherwise), then creates the home folder.
"$dir/claudysha" setup

# Commands on the PATH: --path says yes; otherwise ask, in a terminal only, when a link is missing.
missing=no
for c in "${commands[@]}"; do ours "$c" || missing=yes; done
if [[ " $* " == *" --path "* ]]; then
    link_commands
elif [ "$missing" = yes ] && [ -t 0 ]; then
    read -r -p "Add the claudysha and mem commands to your PATH (links in $bindir)? [Y/n] " answer
    case "${answer:-y}" in [Yy]*) link_commands ;; *) echo "skipped: run them as $dir/claudysha and $dir/mem" ;; esac
fi
if [[ " $* " == *" --dev "* ]]; then
    "$dir/claudysha" dev on || true   # not confirmed: install anyway, dev mode stays off
fi

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
# Claude Code runs a cached copy of the plugin, one per version: refresh it when the repo's
# version is newer than the installed one.
want=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["version"])' "$dir/.claude-plugin/plugin.json")
# (`claude plugin list` shows the repo's version, not the installed copy's: read Claude Code's record.)
have=$(python3 - "$plugin" <<'PY' 2>/dev/null || true
import json, os, sys
path = os.path.expanduser("~/.claude/plugins/installed_plugins.json")
entries = json.load(open(path)).get("plugins", {}).get(sys.argv[1], [])
print(next((e.get("version", "") for e in entries if e.get("scope") == "user"), ""))
PY
)
if [ "$changed" = no ] && [ "$have" != "$want" ]; then
    claude plugin marketplace update nevysha >/dev/null
    claude plugin update "$plugin" --scope user
    echo "claudysha updated from ${have:-?} to $want: new sessions (or /reload-plugins) use it"
    exit 0
fi
if [ "$changed" = yes ]; then
    echo "claudysha installed: hooks and skills load in new sessions (or after /reload-plugins)"
else
    echo "claudysha already installed"
fi
