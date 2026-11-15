# Temporal PP assets

Static files used by `/tempo-*` post-completion steps.

| File | Used by | Purpose |
|------|---------|---------|
| `owner-done-signal.html` | `owner-done-signal` PP step | Owner takeover page shown when a pipeline reaches a terminal outcome |
| `owner-chrome-focus-gate.html` | retired (kept for compatibility) | Legacy pre-smoke Owner GO page; `/tempo-verification` no longer waits on it |
| `owner-browser-check-release.html` | `owner-browser-check-release` PP step | Post-smoke release: Owner may use Chrome again (not full pipeline done) |

The executing agent opens both HTML assets via `user-chrome-devtools`
`navigate_page` on the Owner's existing Chrome session (no new profile).
