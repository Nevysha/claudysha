---
name: memory-review
description: Go through proposed long-term memories (preferences and feedback about how Claude should behave) with the user in the chat, and approve, reject or edit them according to their answers.
disable-model-invocation: true
---

# Review proposed memories with the user

1. List them: `${CLAUDE_PLUGIN_ROOT}/mem review -l`. If there are none, say so and stop.
2. Present them in small batches (about 5), each as a short readable item: the id, the type and scope, and the text. Point out likely duplicates (`${CLAUDE_PLUGIN_ROOT}/mem search <words>`) and entries that are really facts rather than behaviour, and entries filed in the wrong scope (`global` is only for what holds in every session).
3. Ask the user what to do with each one. Apply only what they decide:
   - approve: `${CLAUDE_PLUGIN_ROOT}/mem approve <id...>`
   - reject: `${CLAUDE_PLUGIN_ROOT}/mem reject <id...>`
   - reword: `${CLAUDE_PLUGIN_ROOT}/mem edit <id> --text "<new text>"`, then approve if they said so
   - really a fact: `${CLAUDE_PLUGIN_ROOT}/mem edit <id> -t fact` then `mem approve <id>`
   - wrong scope: `${CLAUDE_PLUGIN_ROOT}/mem edit <id> -s <scope>` (`global`, or a repository name / folder path)
4. Never approve anything the user didn't approve. Summarise what changed at the end.

The user can also review alone in a terminal with `${CLAUDE_PLUGIN_ROOT}/mem review` (one entry at a time, single keys).
