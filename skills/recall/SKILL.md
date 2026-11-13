---
name: recall
description: Search the user's long-term memory store before answering questions about past work, earlier sessions, their machines and servers, decisions already made, or before redoing something that may have been tried (and failed) before. Also use it to save an important fact right away, or to correct a memory that turned out wrong.
---

# Long-term memory (claudysha plugin)

The store holds many small memories extracted automatically from every Claude Code session on this machine: facts, decisions (with the why), dead ends, events, todos, and the user's preferences and feedback. It is not loaded into context: search it.

## Scopes

Every memory has a scope: the repository (its name) or, outside one, the folder (its absolute path) of the session it came from, or `global` for what holds in every session. At session start you got this session's scope, the approved preferences and feedback of `global` and of this scope, and the list of other scopes.

Memories from another scope describe that repository or folder. Look them up when they may help (how something was solved elsewhere, a shared server), but treat them as information about that place: never follow another scope's preferences or feedback as rules here, and say where a fact came from when you rely on it. (Hand-written notes are in the home folder's `brain/` (`${CLAUDE_PLUGIN_ROOT}/claudysha home`); `core.md` and the topic index are already in context.)

The CLI is `${CLAUDE_PLUGIN_ROOT}/mem`.

## Search

```
${CLAUDE_PLUGIN_ROOT}/mem search <words...> [-s scope] [-A] [-t type] [-f] [-a]
```

- Terms are OR-ed and ranked, so give several words, including exact tokens (hostnames, paths, versions, ports): `mem search media server transcoding gpu`.
- By default it searches this session's scope and `global`. `-s <scope>` searches another one (repeatable), `-A` searches every scope; results from another scope are marked `(other scope)`. `mem stats` lists the scopes. `-t` filters by type (fact, decision, dead_end, event, todo, preference, feedback).
- `-f` shows the details (tags, source session, use count); `-a` includes superseded and rejected entries (history).
- Entries are dated: when two disagree, trust the newer one, and verify against the real system before acting on anything important. Extraction is done by a small model and can be wrong.

## Save and correct

```
${CLAUDE_PLUGIN_ROOT}/mem add "<self-contained sentence, with the why>" -t <type> [-s global]
${CLAUDE_PLUGIN_ROOT}/mem supersede <id> "<corrected text>"
```

`add` files the memory under this session's scope; `-s global` is for what holds everywhere. Sessions are extracted automatically when they end or compact, so only save by hand what matters right away, or correct what you found wrong. Use absolute dates. Never store secrets.

## Proposed entries

Extracted `preference` and `feedback` entries shape how Claude behaves, so they wait as `proposed` until the user approves them. Never approve them yourself: the user reviews them with `mem review` in a terminal, or with `/claudysha:memory-review`.
