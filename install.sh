#!/bin/bash
# Install (or uninstall) the claudysha plugin for the current user.
# Safe to run again: it skips the steps that are already done.
#   ./install.sh            install (settings: ~/.config/claudysha/claudysha.conf, created from claudysha.conf.example)
#   ./install.sh --dev      install, then turn dev mode on (asks you to confirm, see the README)
#   ./install.sh --path     install, and add the commands' folder to your PATH without asking
#   ./install.sh --remove   uninstall (keeps your data, removes the commands and the PATH line)
# The `claudysha` and `mem` commands are linked into the plugin's bin/ folder (~/.claudysha/bin);
# in a terminal, it offers to put that folder on your PATH.
set -euo pipefail
user_path="$PATH"                       # the PATH as the user had it, before the line below
export PATH="$HOME/.local/bin:$PATH"   # claude's native install, also when run from Ansible
dir="$(cd "$(dirname "$0")" && pwd)"
plugin="claudysha@nevysha"
commands=(claudysha mem)
linked_new=no
marker="# added by claudysha install.sh"
case "$(basename "${SHELL:-}")" in
    zsh) rcfile="$HOME/.zshrc"; startup=("$HOME/.zshenv" "$HOME/.zprofile" "$HOME/.zshrc") ;;
    bash) rcfile="$HOME/.bashrc"; startup=("$HOME/.bash_profile" "$HOME/.profile" "$HOME/.bashrc") ;;
    *) rcfile=""; startup=() ;;
esac

set_bindir() {
    bindir="$("$dir/claudysha" home)/bin"
    case "$bindir" in
        "$HOME"/*) bindir_shell="\$HOME/${bindir#"$HOME"/}" ;;   # as written in a startup file
        *) bindir_shell="$bindir" ;;
    esac
    path_export="export PATH=\"$bindir_shell:\$PATH\""
}

# Our link, or nothing: never touch a file we didn't put there.
ours() { [ -L "$1/$2" ] && [ "$(readlink -f "$1/$2")" = "$(readlink -f "$dir/$2")" ]; }

link_commands() {
    mkdir -p "$bindir"
    for c in "${commands[@]}"; do
        ours "$bindir" "$c" || { ln -sfn "$dir/$c" "$bindir/$c"; echo "linked $bindir/$c"; linked_new=yes; }
    done
    # Older versions linked them into ~/.local/bin: remove those links.
    for c in "${commands[@]}"; do
        if ours "$HOME/.local/bin" "$c"; then rm "$HOME/.local/bin/$c"; echo "removed the old link $HOME/.local/bin/$c"; fi
    done
}

# The bin folder on the user's PATH: check their own PATH (not ours), then their shell's
# startup files; edit their startup file only with --path or their OK in a terminal.
ensure_on_path() {
    case ":$user_path:" in *":$bindir:"*) return 0 ;; esac
    if [ -n "$rcfile" ] && grep -qsF -e "$bindir" -e "$bindir_shell" "${startup[@]}"; then
        if [ "$linked_new" = yes ]; then echo "note: open a new terminal to use the claudysha and mem commands"; fi
        return 0
    fi
    local add=no
    if [ -n "$rcfile" ] && [[ " $* " == *" --path "* ]]; then
        add=yes
    elif [ -n "$rcfile" ] && [ -t 0 ]; then
        read -r -p "Put the claudysha and mem commands on your PATH (adds one line to $rcfile)? [Y/n] " answer
        [[ "${answer:-y}" == [Yy]* ]] && add=yes
    fi
    if [ "$add" = yes ]; then
        printf '\n%s  %s\n' "$path_export" "$marker" >> "$rcfile"
        echo "added to $rcfile: open a new terminal to use the claudysha and mem commands"
    else
        echo "note: to use the claudysha and mem commands, add this line to your shell's startup file${rcfile:+ ($rcfile)}:"
        echo "  $path_export"
    fi
}

if [ "${1:-}" = "--remove" ]; then
    claude plugin uninstall "$plugin" --keep-data 2>/dev/null || true
    claude plugin marketplace remove nevysha 2>/dev/null || true
    set_bindir
    for c in "${commands[@]}"; do
        if ours "$bindir" "$c"; then rm "$bindir/$c"; echo "removed $bindir/$c"; fi
    done
    for f in "$HOME/.zshrc" "$HOME/.bashrc"; do
        if grep -qsF "$marker" "$f"; then
            grep -vF "$marker" "$f" > "$f.claudysha-tmp" && cat "$f.claudysha-tmp" > "$f" && rm "$f.claudysha-tmp"
            echo "removed the PATH line from $f"
        fi
    done
    echo "claudysha removed (your data is kept: $("$dir/claudysha" home)/data)"
    exit 0
fi

command -v claude >/dev/null || { echo "claude CLI not found" >&2; exit 1; }
python3 -c 'import sqlite3; sqlite3.connect(":memory:").execute("create virtual table t using fts5(x)")' \
    || { echo "python3 with SQLite FTS5 is required" >&2; exit 1; }

# Writes ~/.config/claudysha/claudysha.conf if missing (asks questions in a terminal, defaults
# otherwise), then creates the plugin's folder (and moves an older layout's data into data/).
"$dir/claudysha" setup

set_bindir
link_commands
ensure_on_path "$@"
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
