"""SQLite-backed decision audit trail for TradAlgo (renamed from the spec's
`logging/` to avoid shadowing Python's stdlib `logging` module, which Flask
and the `anthropic` SDK import internally)."""
