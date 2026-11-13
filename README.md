# claudysha

A personal memory plugin for [Claude Code](https://code.claude.com). It gives Claude a memory that carries across sessions, in two tiers:

- **An automatic long-term store.** When a session ends or compacts, a small model (Haiku) reads the transcript and extracts facts, decisions, dead ends, events, todos, preferences and feedback into a local SQLite database. Claude searches it on demand (`claudysha:recall` skill). It's never loaded wholesale, so capture can be generous without filling every session with loosely related snippets.
- **A hand-written core.** `core.md` (who you are, how you want Claude to work with you) and an index of topic notes are printed into every session. This is the part that shapes Claude's behaviour, so it only changes with your OK. Extracted preferences and feedback wait as *proposed* until you approve them.

Plus a **backlog** of postponed ideas that come back on their own when their review date passes, and **archive / load** of everything the plugin keeps.

Python standard library only (3.12+ with SQLite FTS5), no server, no external database.

## Install

From a clone:

```
git clone <this repo> ~/claudysha
~/claudysha/install.sh            # asks a few questions, then installs claudysha@nevysha for your user
~/claudysha/install.sh --remove   # uninstalls; your data stays
```

`install.sh` writes `~/.config/claudysha/claudysha.conf` if it doesn't exist (in a terminal it asks for the data folder, your name, your pronouns and your projects; otherwise it uses the defaults), creates the data folder, registers the repo as a local marketplace and installs the plugin. A plugin installed from a local folder runs in place: edits apply to new sessions (or after `/reload-plugins`).

From GitHub, inside Claude Code: `/plugin marketplace add <owner>/<repo>`, then `/plugin install claudysha@nevysha`, then run `claudysha setup` from the installed plugin folder (or write the config by hand from `claudysha.conf.example`).

## Settings

`~/.config/claudysha/claudysha.conf` (or `$XDG_CONFIG_HOME/claudysha/claudysha.conf`, or `CLAUDYSHA_CONFIG`). It lives outside the plugin folder on purpose: a plugin installed from GitHub is a cached copy that's replaced on updates.

| Key | Default | What |
|---|---|---|
| `home` | `~/.claudysha` | the data folder (`CLAUDYSHA_HOME` overrides it) |
| `user_name` | `the user` | how Claude and the extraction prompts refer to you |
| `user_pronouns` | `they/them` | your pronouns, for the same |
| `projects` | (none) | comma-separated projects the extractor should prefer when filing memories |

`./claudysha config` shows the file in use and the settings in effect.

## Data folder

Everything the plugin keeps lives in one folder, never in the plugin itself:

```
~/.claudysha/
  brain/      tier 2, written by hand, its own git repo:
                core.md          who you are, how to work with you
                memories/        one note per topic, indexed in memories/MEMORY.md
                maybe-later/     postponed ideas (backlog)
  memory/     tier 1: memory.db (SQLite), extract.log
  backups/    anything you want archived with it
```

## Archive and load

```
./claudysha archive [file-or-folder]   # .tar.gz of the whole data folder
./claudysha load <archive>             # replace the whole data folder with an archive
```

- The archive holds a consistent copy of the database even while extraction is writing, plus a `claudysha-archive.json` manifest.
- `load` erases everything that isn't in the archive. It lists what would be erased and only proceeds when you **type `erase` in a terminal**. It refuses piped input, which stops accidental or scripted confirmations; a program could still fake a terminal, so the rest is a rule: Claude must never confirm a load for you. Before replacing anything it saves the current folder next to it (`~/.claudysha-before-load-<date>.tar.gz`). It rejects archives with absolute or `..` paths, links, or no manifest.
- Extraction takes a shared lock on the data folder and `load` an exclusive one, so they never overlap.

## What the plugin adds to a session

| Piece | What |
|---|---|
| SessionStart hook (`claudysha session-start`) | prints `core.md` and the topic index (again after a compaction) |
| SessionStart hook (`mem session-start`) | a line saying the store exists, and how many proposed memories wait for review |
| SessionStart hook (`backlog session-start`) | lists the backlog notes that are due |
| SessionEnd and PreCompact hooks (`mem hook`) | extraction, in the background |
| `claudysha:recall` skill | how to search, save and correct memories; Claude loads it when the topic fits |
| `/claudysha:memory-review` skill | go through proposed memories with you in the chat (only you can start it) |

## Memory store (tier 1)

### Entries

Each memory has `text`, `type`, `project`, `tags`, `confidence` (0 to 1), `status`, `superseded_by`, `source` (`manual` or `extract`), `session_id`, `created_at` (when the conversation happened), `last_used_at`, `use_count` and a JSON `meta`.

- Types: `fact`, `decision`, `dead_end`, `event`, `todo`, `preference`, `feedback`.
- Statuses: `active`, `proposed` (extracted `preference`/`feedback` entries, which shape how Claude behaves, wait for your OK), `superseded`, `rejected`.

### Why SQLite

One file, no server. Tables cover metadata and filtering, JSON columns cover free-form data, and FTS5 covers keyword search. It handles exact tokens like `127.0.0.1:8080` or `pnpm@10.15.1` well (each search term is quoted as a phrase), which embeddings are weak at. Search by meaning can be added later with the `sqlite-vec` extension in the same file.

### Extraction

`mem hook` starts `mem ingest --hook` in the background, so the session never waits. It:

1. reads the transcript from where the last run stopped,
2. skips sessions you never typed in (`claude -p` runs from scripts have no message with `origin.kind == "human"`),
3. condenses it to the dialogue plus short tool hints (no tool output, no thinking); user-role messages that aren't yours (task notifications, summaries) are labelled as notes,
4. asks Haiku (`claude -p --model haiku`, structured output, `memory/prompts/extract.md`) for memories,
5. sends each candidate with its closest stored memories to a second call (`memory/prompts/resolve.md`) that marks duplicates and memories it makes obsolete,
6. stores the rest and supersedes the obsolete ones.

Child `claude -p` runs set `MEM_NO_HOOK=1` and load no settings, so they never trigger extraction themselves. `MEM_EXTRACT_MODEL` overrides the model. Extraction costs well under a cent per session with Haiku.

### Commands

```
mem search <words...> [-p project] [-t type] [-a] [-f]   # OR-ed terms, ranked by bm25, confidence, use
mem add "<text>" [-t type] [-p project] [--tags ...] [-c 0.9]
mem get <id...>
mem list [-p project] [-t type] [-n 20]
mem supersede <id> "<new text>"  |  mem supersede <id> --by <id>
mem edit <id> [--text ...] [-t ...] [-p ...] [--tags ...] [-c ...]
mem review [-l]                   # one by one: a approve, r reject, e edit, t type, s skip, u undo (-l: list)
mem approve <id...>  |  mem reject <id...>
mem ingest <transcript.jsonl> [--dry-run]
mem backfill [--project-dir <folder under ~/.claude/projects>]
mem stats  |  mem export          # JSONL dump
```

## Backlog

`backlog/backlog.py` reads the frontmatter of the notes in `<home>/brain/maybe-later/`:

```yaml
---
status: open          # open | waiting | someday | done
created: 2026-11-13
review-after: 2026-11-27   # empty = no date
summary: one line
---
```

At session start it lists the open or waiting notes whose `review-after` date has passed. `backlog/backlog.py list` shows them all.

## Layout

| Path | What |
|---|---|
| `.claude-plugin/` | plugin manifest (`claudysha`) and the marketplace that lists it (`nevysha`) |
| `hooks/hooks.json`, `skills/` | what the plugin adds to sessions |
| `home/` | settings, data folder, archive/load, core-memory hook (`claudysha` is a symlink to `home/claudysha.py`) |
| `memory/` | tier-1 store: CLI, extraction prompts, interactive review (`mem` is a symlink to `memory/mem.py`) |
| `backlog/` | due backlog notes |
| `install.sh`, `claudysha.conf.example` | installation and settings reference |

## Tests

```
python3 -m unittest discover -s home/tests
python3 -m unittest discover -s memory/tests
python3 -m unittest discover -s backlog/tests
```

Standard library only; the model calls are mocked.

## Not done yet

- A consolidation pass that proposes promotions to tier 2, merges, and expiry of stale todos.
- Scheduled archives and an off-machine copy.
- Meaning search with `sqlite-vec`, if keyword search starts missing things.

## License

MIT, see `LICENSE`.
