# @ Mention Selector Design

## Goal

When the user types `@` in the session composer, show a WeChat-style participant picker for the current session and insert the selected participant alias into the draft.

## Design

The feature is frontend-only because `session.participants` already contains the available aliases, model refs, and role descriptions. `TabSessionDetail` owns the composer state, so it will also own mention state: the active query range, filtered participants, highlighted item, and insertion behavior.

The picker opens when the caret is after an `@` token that is not separated by whitespace. It filters by `custom_id`, `model_ref`, and `role_desc`, supports mouse selection, ArrowUp/ArrowDown navigation, Enter/Tab insertion, and Escape close. Selecting an item replaces the active `@query` range with `@custom_id ` and restores focus to the textarea.

## Scope

- Current-session participants only.
- No backend API changes.
- No multi-select chips; the composer remains plain text so existing backend routing continues to work.
- Works in all session modes, but is most useful in `code_workspace`.

## Testing

Add an app-level test that loads a workspace session with `claude` and `codex`, types `@co`, sees the filtered picker, presses Enter, and verifies the composer contains `@codex `.
