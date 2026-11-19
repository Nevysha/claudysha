#!/bin/bash
# Uninstall the claudysha plugin for the current user.
#   ./uninstall.sh           remove the plugin, its commands and the PATH line; keeps your data and settings
#   ./uninstall.sh --purge   the same, then delete your data and settings too: it writes an archive of
#                            your data first, and asks you to type `erase` (in a real terminal only)
# The repo itself (this folder) is left alone: delete it yourself afterwards if you want.
set -euo pipefail
export PATH="$HOME/.local/bin:$PATH"   # claude's native install
dir="$(cd "$(dirname "$0")" && pwd)"
plugin="claudysha@nevysha"
commands=(claudysha mem)
marker="# added by claudysha install.sh"

purge=no
for arg in "$@"; do
    case "$arg" in
        --purge) purge=yes ;;
        -h|--help) sed -n '2,6p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) echo "unknown option: $arg (see --help)" >&2; exit 2 ;;
    esac
done

# Read where things are before removing anything.
home="$("$dir/claudysha" home)"
config="$("$dir/claudysha" config | sed -n 's/^config: \([^ ]*\).*/\1/p')"
bindir="$home/bin"

if [ "$purge" = yes ]; then
    # Check everything before the first change, so a refused purge leaves the install as it was.
    case "$home" in
        ""|/|"$HOME"|"$HOME"/) echo "refusing to delete the plugin's folder: it is '$home'" >&2; exit 1 ;;
    esac
    if [ ! -t 0 ]; then
        echo "--purge deletes your data: run it in a real terminal, where you can confirm it" >&2
        exit 1
    fi
    echo "This deletes all of claudysha's data and settings:"
    echo "  $home"
    [ -e "$config" ] && echo "  $config"
    echo "An archive of your data is written to $HOME first."
    read -r -p "Type erase to confirm: " answer
    [ "$answer" = erase ] || { echo "cancelled, nothing removed"; exit 1; }
    saved="$("$dir/claudysha" archive "$HOME")"
    echo "archive written: $saved"
fi

# Our link, or nothing: never touch a file we didn't put there.
ours() { [ -L "$1/$2" ] && [ "$(readlink -f "$1/$2")" = "$(readlink -f "$dir/$2")" ]; }

"$dir/claudysha" dashboard stop >/dev/null 2>&1 || true
if command -v claude >/dev/null; then
    claude plugin uninstall "$plugin" --keep-data >/dev/null 2>&1 && echo "removed the plugin from Claude Code" || true
    claude plugin marketplace remove nevysha >/dev/null 2>&1 && echo "removed the nevysha marketplace" || true
else
    echo "note: claude CLI not found, so the plugin was not removed from Claude Code"
fi
for d in "$bindir" "$HOME/.local/bin"; do   # ~/.local/bin: where older versions linked them
    for c in "${commands[@]}"; do
        if ours "$d" "$c"; then rm "$d/$c"; echo "removed $d/$c"; fi
    done
done
for f in "$HOME/.zshrc" "$HOME/.bashrc"; do
    if grep -qsF "$marker" "$f"; then
        # cat into the file (not mv) keeps it if it is a link, and keeps its permissions.
        grep -vF "$marker" "$f" > "$f.claudysha-tmp" || true
        cat "$f.claudysha-tmp" > "$f" && rm "$f.claudysha-tmp"
        echo "removed the PATH line from $f"
    fi
done

if [ "$purge" = yes ]; then
    rm -rf "$home"
    echo "deleted $home"
    if [ -e "$config" ]; then
        rm -f "$config"
        rmdir "$(dirname "$config")" 2>/dev/null || true
        echo "deleted $config"
    fi
    echo "claudysha removed, with its data (archive: $saved)"
else
    echo "claudysha removed (your data is kept: $home/data; settings: $config)"
    echo "to delete them too: $dir/uninstall.sh --purge"
fi
echo "start a new session (or /reload-plugins) so its hooks and skills stop loading; this repo ($dir) can be deleted"
