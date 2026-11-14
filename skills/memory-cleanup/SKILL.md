---
name: memory-cleanup
description: Go through one scope's long-term memories with the user to find duplicates, entries that overlap, outdated or contradicting entries and entries that state several things, and propose fixes (merge, split, correct, forget). Only changes that lose nothing are applied without asking. Use it when the user asks to clean up or tidy their memory. When you notice such problems yourself (for example in search results), offer it to the user instead of starting it unasked.
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

## 3. Apply what loses nothing, without asking

Gather every proposal first. Then apply these yourself, right away:

- **Duplicates:** keep the fuller (or, if equal, newer) entry and drop the other.
- **Merges** of overlapping entries about the same subject, when the merged text keeps every piece of information of every entry.
- **Splits** of an entry stating several things, when the pieces together keep everything it said.

Only when all the entries concerned are in the same scope and none is a preference or feedback: those shape how you behave, so they always go to the user. Anything that drops, corrects or moves information is the user's call too: corrections, removals, cuts of a repeated part, scope and type changes. When in doubt, ask.

## 4. Ask about the rest, by kind

Group the remaining proposals by kind (corrections, removals, cuts, merges and splits involving preferences, feedback or `global`, scope or type changes). Show each group as a numbered list: the ids and texts concerned (shortened if long), what's wrong, and the exact fix, with the new text when there is one. Then ask one question per group, with the question tool when you have one: **apply all**, **choose one by one**, or **skip all**. The user can also answer in their own words ("all but 3").

For a group they want to go through one by one, present it in batches of about 5, each headed with where they are ("Batch 2 of 3", plus how many are left), and ask about each proposal (apply, skip, or change it).

## 5. Commands

| Problem | Command |
|---|---|
| Duplicate: keep #A, drop #B | `mem supersede <B> --by <A>` |
| Overlap, same subject: one entry for both | `mem merge <A> <B> [...] --text "<the one entry>"` |
| Overlap, separate fact: cut the repeated part | `mem supersede <B> "<only what B adds>"` |
| Outdated: corrected text | `mem supersede <id> "<corrected text>"` |
| Wrong, and nothing replaces it | `mem reject <id>` |
| Several things in one entry | `mem split <id> "<first>" "<second>" [...]` |
| Wrong scope or type | `mem edit <id> -s <scope>` / `mem edit <id> -t <type>` |

Replaced entries are kept as history (`mem search -a` shows them), so nothing is lost. New texts follow the usual rules: self-contained, one fact with its reason, absolute dates, no secrets. A merge that includes a proposed preference or feedback stays proposed until the user reviews it (`/claudysha:memory-review`).

Undo: `mem restore <id>` on a replaced or rejected entry undoes the change that replaced it, as a whole (a merge brings back every merged entry and removes the merged one; a split brings back the original and removes the pieces).

## 6. Summary

At the end, list what you applied without asking (each change with its ids, and the new id), so the user can check it and undo any of it with `mem restore <old id>`. Then what they approved, and what they chose to keep as it is.
