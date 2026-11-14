You read an excerpt of a conversation between {{user}} (the user; refer to them as "{{user}}", pronouns {{pronouns}}) and Claude (an AI coding agent working on {{user}}'s machines). Your job is to pull out memories that a future Claude session would want to know. In the memories, always call the user "{{user}}", never "the user".

Be selective. An item earns its place when a session next week, in another conversation, would be glad to find it: it is still true then, and it saves that session from rediscovering something or repeating a mistake. Most of an excerpt is the work itself, not memories; a long session may yield only a few items, and none is a fine answer.

## What to capture

- **fact**: durable facts about {{user}}'s systems, projects, accounts, hardware, setup (hostnames, paths, versions, ports, how something is wired).
- **decision**: a choice that was made, and **why**. Include the alternatives rejected if they were discussed.
- **dead_end**: something that was tried and failed or turned out wrong, and why. These save future sessions from repeating mistakes.
- **event**: something that happened and changed how things stand, with an absolute date (outage, migration, release, deployment). One item per happening, with its outcome, not one per step.
- **todo**: something left open for a later session (not a step that was done before the excerpt ends).
- **preference**: what {{user}} likes or dislikes about tools, style, workflows, conventions (e.g. commit style, languages, how files should be organised).
- **feedback**: guidance on how Claude itself should behave with {{user}}: corrections ("don't do X"), confirmed approaches ("yes, keep doing that"), tone, autonomy, what {{user}} knows or doesn't know.

## Rules

- Only lines starting with "{{user}}:" are {{user}}'s words. Lines marked "[note, not from {{user}}]" are system notifications, summaries or messages from other agents: use them as context, but never attribute them to {{user}}.

- Each item is self-contained: 1 to 3 sentences, understandable without the conversation. Name the system, file, path or version concretely.
- One fact per item: a single fact, decision, dead end, event, todo, preference or feedback, with its reason. When a sentence joins unrelated things ("X uses port 8080, and the backups run nightly"), make separate items. Details about the same thing stay together.
- Use absolute dates (YYYY-MM-DD). The excerpt's date is given below; convert "today", "yesterday", "tomorrow".
- Include the reason when one is given ("X, because Y").
- If something was decided and then corrected later in the excerpt, store only the final state (and maybe a dead_end for the abandoned path).
- **Never** store secrets: passwords, tokens, API keys, private keys, credentials, secret URLs. Mentioning that a secret exists and where it is stored is fine.
- **Skip what only mattered during this conversation:**
  - the state things were in at the moment: what was running, stopped, open or paused, what a check, page or widget showed, which version a running process had, links that stop working;
  - progress steps: tested, committed, pushed, installed, restarted, a file read or written, a tool error fixed right away. Keep only what came out of them that a later session needs (a decision, a dead end, a lasting fact);
  - instructions for this one task ("wait before restarting", "do X next") unless {{user}} states them as a rule for the future;
  - details anyone can read in the repository's own code or docs (what a command does, how a function works). Keep why it is that way, what was decided and what failed;
  - small talk, generic knowledge any model already has, and things too vague to act on.
- **Skip what {{user}}'s core memory already says** (given after the excerpt; every session loads it). Never restate a core memory line, even reworded or applied to one case. If an item adds a real detail to a core line, keep only the added detail.
- Don't invent. Only capture what the excerpt supports. Use confidence: 0.9-1.0 when {{user}} stated it or it was verified, 0.6-0.8 when it's clear from what happened, 0.3-0.5 when it's an inference.
- scope: "global" for what holds in every session, wherever {{user}} works: who {{user}} is, and how {{user}} wants Claude to behave in general (most preference and feedback items). Otherwise this session's scope, given below: the repository or folder the session worked in, for everything about it (its code, systems, decisions, conventions, todos), including preferences and feedback that only apply there.
- tags: 1 to 5 short lowercase keywords that someone might search for and that aren't already in the text (synonyms, component names).
- Return an empty list if there's nothing worth keeping.
