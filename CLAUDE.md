# claudysha

A personal plugin for Claude Code ("personal" as in personal to each user: it starts generic and adapts to its user's habits, see the README intro), made of the plugin (`plugin/`: settings, data folder, archive/load) and modules (`memory/` today). README.md explains both layers. This repo is meant to be publishable: it holds code only, never anyone's data.

- Remote: `Nevysha/claudysha` on GitHub (public): push after committing. Commit on `main` with Conventional Commits that have a type and a scope (`feat(memory): ...`, `fix(extract): ...`, `docs(readme): ...`). Don't add a `Claude-Session:` trailer here: the history is public.
- **No personal data in the repo:** no names, hosts, IPs, paths under a home folder, or infra details, in code, tests, prompts or docs. Who the user is comes from the config (`user_name`, `user_pronouns`); refer to "the user" otherwise. Test data uses made-up names.
- **State never goes in the repo:** it goes in the data folder (`claudysha home`), whose path comes from `plugin/claudysha.py`; each module owns its part of it. Settings live in `~/.config/claudysha/claudysha.conf`. Tests set `CLAUDYSHA_HOME` and `CLAUDYSHA_CONFIG` to temp paths.
- The repo is a Claude Code plugin (`.claude-plugin/`, `hooks/hooks.json`, `skills/`) and its own marketplace (`nevysha`). Check changes with `claude plugin validate .`. Claude Code runs a cached copy per version, not the repo: bump `version` in `.claude-plugin/plugin.json` (and the marketplace entry if it has one) when a change touches what runs (hooks, scripts, skills, prompts), then run `./install.sh` to refresh the installed copy.
- Keep the layers apart: shared infrastructure goes in `plugin/`, features go in a module folder, which registers its `init()` in `MODULES` and has its own stdlib `unittest` tests. Run all tests before committing (`plugin/tests`, `memory/tests`). Mock `run_claude`; never call the real model from tests.
- Python standard library only. Small bash entrypoints (`install.sh`) are fine.
- **Sessions never see how memory works inside** (see the README's Guard section): what hooks print and what skills say talks about "long-term memory", "core memory" and "backlog", never data-folder paths, file names, databases or internals; sessions act through commands (`mem ...`). When a session needs something new, add a command rather than a path. Keep the guard on, and never weaken it to make a session's job easier. Dev mode (`claudysha dev on`) is the user's alone: never turn it on yourself, never add a way around its terminal confirmation, and when it's on, pause the guard (`claudysha guard off`) only while the work needs it.
- Extraction prompts are in `memory/store/prompts/` (`{{user}}` and `{{pronouns}}` are filled from the config). Try prompt changes with `mem ingest --dry-run <transcript>` on a real transcript before committing.
- `claudysha load` must keep requiring the user's typed `erase` in a real terminal: never add a flag or path that skips it, and never confirm a real load yourself (for example by faking a terminal). Tests may do so on a temporary data folder only.

## Installing claudysha for a user

When a user asks you to install it (the README suggests they just ask Claude):

1. Clone the repo to `~/claudysha` (or where they prefer).
2. If `~/.config/claudysha/claudysha.conf` doesn't exist, ask them in the chat, with the defaults: the data folder (`~/.claudysha`), the name you should use for them, and their pronouns (`they/them`). Write the file from `claudysha.conf.example` with their answers. `install.sh` would ask the same questions, but only in a real terminal, which your shell isn't.
3. Run `~/claudysha/install.sh`. If they want dev mode (to work on claudysha itself), tell them to run `~/claudysha/claudysha dev on` in their own terminal: it needs their typed confirmation. Then tell them to start a new session (or `/reload-plugins`) so the hooks and skills load.
