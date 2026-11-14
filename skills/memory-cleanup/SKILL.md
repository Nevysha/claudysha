---
name: memory-cleanup
description: Go through one scope's long-term memories with the user to find duplicates, entries that overlap, outdated or contradicting entries and entries that state several things, and propose fixes (merge, split, correct, forget) that are applied only with the user's OK. Use it when the user asks to clean up or tidy their memory. When you notice such problems yourself (for example in search results), offer it to the user instead of starting it unasked.
---

# Clean up long-term memory with the user

The goal: **one entry per fact**. Every memory states one thing (a fact, decision, dead end, event, todo, preference or feedback, with its reason), says it once, and is still true.

`mem` below is `${CLAUDE_PLUGIN_ROOT}/mem`.

## 1. Pick the scope

This session's scope, unless the user names another one (`mem stats` lists them). `global` holds what applies everywhere, mostly the user's preferences and feedback: clean it only when the user asks for it.

## 2. Find the problems

- `mem similar [-s <scope>]`: groups of entries that look alike, with the `global` entries they repeat. A group is only a lead: read it and decide.
- `mem list -s <scope> -n 500`: the whole scope, newest first. Read it for what `similar` can't see: a todo that is done, a state that changed later, two entries that contradict each other, an entry that strings several unrelated things together.
- `mem search <words> -s <scope>` around a lead finds the related entries worded differently, and `mem get <id...>` shows entries in full.

Look for:

- **Duplicates:** entries that say the same thing. Keep the best one.
- **Overlaps:** an entry that repeats another one and adds something. Either merge them into one entry (same subject), or cut the repeated part out of the newer one (a separate fact).
- **Outdated or contradicting:** a newer entry or the real system says otherwise. When it matters and you can check cheaply (a file, a command, the code), check before proposing. The fix is a corrected entry, or forgetting it if a newer one already says the right thing.
- **Several things in one entry:** split it into one entry per thing.
- **Wrong scope or type:** a rule for every session filed under a repository, a fact filed as feedback.

Leave alone what is merely related: two entries about the same system that state different facts are fine.

## 3. Propose, in batches

Present the proposals in small batches (about 4 or 5), each with the ids and texts concerned (shortened if long), what's wrong, and the exact fix, including the new text when there is one. Ask the user about each proposal, with the question tool when you have one (apply, skip, or change it), and apply only what they approve.

Preferences and feedback shape how you behave: be extra careful with them, and never turn a fact into one without the user saying so.

## 4. Apply

| Problem | Command |
|---|---|
| Duplicate: keep #A, drop #B | `mem supersede <B> --by <A>` |
| Overlap, same subject: one entry for both | `mem merge <A> <B> [...] --text "<the one entry>"` |
| Overlap, separate fact: cut the repeated part | `mem edit <B> --text "<only what B adds>"` |
| Outdated: corrected text | `mem supersede <id> "<corrected text>"` |
| Wrong, and nothing replaces it | `mem reject <id>` |
| Several things in one entry | `mem split <id> "<first>" "<second>" [...]` |
| Wrong scope or type | `mem edit <id> -s <scope>` / `mem edit <id> -t <type>` |

Replaced entries are kept as history (`mem search -a` shows them), so nothing is lost. New texts follow the usual rules: self-contained, one fact with its reason, absolute dates, no secrets. A merge that includes a proposed preference or feedback stays proposed until the user reviews it (`/claudysha:memory-review`).

At the end, summarise what changed (counts, and the ids replaced) and what the user chose to keep as it is.
