# Golden dialog regression pack

This folder holds deterministic chat transcripts plus the regression harness expectations.

## Adding a new golden dialog
1. Append a new entry to `dialogs.json` with a unique `id`, short `description`, and one or more `turns`.
2. For each turn, specify the minimal `expect` fields you want to lock (e.g., `intent`, `stage`, `missing_fields`, `fields`, `estimate`, `reply_contains`, `handoff_required`).
3. Keep numeric estimates to the cents level shown by the estimator; use existing cases as a template for field names.

## Validating the goldens
Run the focused test to exercise every dialog:

```bash
pytest tests/test_golden_dialogs.py
```

The test automatically runs each turn in order, carries forward state, and verifies the asserted fields and estimates.
