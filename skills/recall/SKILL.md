---
name: recall
description: Search the user's long-term memory before answering questions about past work, earlier sessions, their machines and servers, decisions already made, or before redoing something that may have been tried (and failed) before. Also use it to remember an important fact right away, to correct a memory that turned out wrong, or to change the user's core memory or backlog.
---

# Your memory (claudysha plugin)

You have three kinds of memory, and one command for all of them: `${CLAUDE_PLUGIN_ROOT}/mem`.

- **Long-term memory:** what happened in past sessions (facts, decisions with the why, dead ends, events, todos, the user's preferences and feedback). It is not in your context: search it.
- **Core memory:** who the user is and how they want you to work together. You saw it at session start.
- **Backlog:** ideas the user postponed, each with a review date. Session start lists the ones that are due.

Reach your memory only through these commands. Never look for where or how it is stored, and never open, search or edit those files yourself, even to check something: the plugin refuses it. If a command can't do what you need, tell the user instead of working around it. When you talk to the user, say "long-term memory", "core memory" and "backlog".

## Long-term memory

### Scopes

Every memory has a scope: the repository (its name) or, outside one, the folder (its absolute path) of the session it came from, or `global` for what holds in every session. At session start you got this session's scope, the approved preferences and feedback of `global` and of this scope, and the list of other scopes.

Memories from another scope describe that repository or folder. Look them up when they may help (how something was solved elsewhere, a shared server), but treat them as information about that place: never follow another scope's preferences or feedback as rules here, and say where a fact came from when you rely on it.

### Search

```
${CLAUDE_PLUGIN_ROOT}/mem search <words...> [-s scope] [-A] [-t type] [-f] [-a]
```

- Terms are OR-ed and ranked, so give several words, including exact tokens (hostnames, paths, versions, ports): `mem search media server transcoding gpu`.
- By default it searches this session's scope and `global`. `-s <scope>` searches another one (repeatable), `-A` searches every scope; results from another scope are marked `(other scope)`. `mem stats` lists the scopes. `-t` filters by type (fact, decision, dead_end, event, todo, preference, feedback).
- `-f` shows the details (tags, source session, use count); `-a` includes superseded and rejected entries (history). `mem get <id...>` shows entries in full, `mem list [-s scope] [-t type]` the latest ones.
- Memories are dated and can be wrong or outdated: when two disagree, trust the newer one, and verify against the real system before acting on anything important.

### Remember and correct

```
${CLAUDE_PLUGIN_ROOT}/mem add "<self-contained sentence, with the why>" -t <type> [-s global]
${CLAUDE_PLUGIN_ROOT}/mem supersede <id> "<corrected text>"
```

`add` files the memory under this session's scope; `-s global` is for what holds everywhere. You remember sessions on your own as they go, so only add by hand what matters right away, or correct what you found wrong. Use absolute dates. Never store secrets.

### Proposed entries

Preferences and feedback you pick up on your own shape how you behave, so they wait as `proposed` until the user approves them. Never approve them yourself: the user reviews them with `/claudysha:memory-review`, or `mem review` in a terminal.

## Core memory

Change it only when the user asks or agrees, and show them the change (each command prints it). Keep it short: facts go in long-term memory.

```
${CLAUDE_PLUGIN_ROOT}/mem core show
${CLAUDE_PLUGIN_ROOT}/mem core add "- <line>" [--section "<section title>"] [-m "<what changed>"]
${CLAUDE_PLUGIN_ROOT}/mem core edit --old "<exact passage>" --new "<replacement>" [-m ...]
${CLAUDE_PLUGIN_ROOT}/mem core remove "<text from the one line to remove>" [-m ...]
```

`edit` and `remove` refuse when the text matches more than once: give more of it.

## Backlog

```
${CLAUDE_PLUGIN_ROOT}/mem backlog list [-a]                 # DUE marks items to review now; -a includes done
${CLAUDE_PLUGIN_ROOT}/mem backlog show <id>
${CLAUDE_PLUGIN_ROOT}/mem backlog add "<one-line summary>" --review-after YYYY-MM-DD --details "<context a future session needs>"
${CLAUDE_PLUGIN_ROOT}/mem backlog update <id> [--review-after YYYY-MM-DD|none] [--status open|waiting|someday|done] [--summary ...] [--append "<news>"]
${CLAUDE_PLUGIN_ROOT}/mem backlog done <id>
```

Statuses: `open`, `waiting` (on the user or something external), `someday` (no reminder), `done`. To bring an item up later, move its `--review-after`.

## When something looks wrong

`mem log` shows the recent memory runs and their errors, `mem stats` the counts per scope. Report what you see to the user; don't go looking at how it works inside.
