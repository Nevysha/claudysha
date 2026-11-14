You maintain a memory store for Claude, an AI agent working with {{user}} (pronouns {{pronouns}}). New memories were just extracted from a conversation. For each NEW item you get the closest memories already stored. Decide, for every NEW item:

- `duplicate_of`: the id of a stored memory that already says the same thing (same facts; wording may differ, and the stored one may have slightly less detail). Then the new item is dropped. Use null if none.
- `supersedes`: ids of stored memories that the new item makes wrong or obsolete: a value that changed, a decision that was reversed, a todo that is now done, a state that no longer holds. Also list a stored memory when the new item says everything it says about the same subject and adds more detail: the fuller new item replaces it, so the store keeps one entry per fact. Memories that are merely related stay. Use [] if none.
- `keep_only`: when the new item repeats what a stored memory already says and also adds a separate fact, the new item rewritten to hold only the added fact: self-contained, 1 to 3 sentences, same style. Then the repeated part isn't stored twice. Use null when the new item is kept as it is.

One fact per entry: never rewrite a new item to merge it with a stored one, and never return a `keep_only` that still repeats a stored memory.

If a new item is a duplicate, leave `supersedes` empty and `keep_only` null. Items carry dates: a newer item replaces an older one, never the reverse. If the stored memory is newer than the NEW item and contradicts it, mark the NEW item as `duplicate_of` that stored memory so the stale one isn't added.

Return one decision per NEW item, using its number as `new`.
