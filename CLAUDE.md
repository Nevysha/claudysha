# claudysha

A personal plugin for Claude Code, made of the plugin (`plugin/`: settings, data folder, archive/load) and modules (`memory/` today). README.md explains both layers. This repo is meant to be publishable: it holds code only, never anyone's data.

- Remote: `Nevysha/claudysha` on GitHub (private for now, meant to go public): push after committing. Commit on `main` with Conventional Commits that have a type and a scope (`feat(memory): ...`, `fix(extract): ...`, `docs(readme): ...`). Don't add a `Claude-Session:` trailer here: the history is meant to be public.
- **No personal data in the repo:** no names, hosts, IPs, paths under a home folder, or infra details, in code, tests, prompts or docs. Who the user is comes from the config (`user_name`, `user_pronouns`, `projects`); refer to "the user" otherwise. Test data uses made-up names.
- **State never goes in the repo:** it goes in the data folder (`claudysha home`), whose path comes from `plugin/claudysha.py`; each module owns its part of it. Settings live in `~/.config/claudysha/claudysha.conf`. Tests set `CLAUDYSHA_HOME` and `CLAUDYSHA_CONFIG` to temp paths.
- The repo is a Claude Code plugin (`.claude-plugin/`, `hooks/hooks.json`, `skills/`) and its own marketplace (`nevysha`). Check changes with `claude plugin validate .`.
- Keep the layers apart: shared infrastructure goes in `plugin/`, features go in a module folder, which registers its `init()` in `MODULES` and has its own stdlib `unittest` tests. Run all tests before committing (`plugin/tests`, `memory/tests`). Mock `run_claude`; never call the real model from tests.
- Python standard library only. Small bash entrypoints (`install.sh`) are fine.
- Extraction prompts are in `memory/store/prompts/` (`{{user}}` and `{{pronouns}}` are filled from the config). Try prompt changes with `mem ingest --dry-run <transcript>` on a real transcript before committing.
- `claudysha load` must keep requiring the user's typed `erase` in a real terminal: never add a flag or path that skips it, and never confirm a real load yourself (for example by faking a terminal). Tests may do so on a temporary data folder only.
