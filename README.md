# easylabeldemo

Development repository for an open-source numismatic collection manager. The existing 2×2 holder
label generator (kept in [`legacy/`](legacy/)) becomes one module of this suite; its behaviour is
preserved, not replaced.

The released label tool lives in a separate repository. This one is for building the wider suite.

> Naming note: the product name is undecided. The Python package is currently `numis`, a placeholder.
> Renaming later touches import paths only, never the database schema.

## Layout

| Path | Contents |
|---|---|
| [`docs/design/`](docs/design/) | Design specifications. Start with [`OVERVIEW.md`](docs/design/OVERVIEW.md) for plain English, or [`README.md`](docs/design/README.md) for the document index |
| [`docs/design/schema/base-v1.sql`](docs/design/schema/base-v1.sql) | The normative base schema |
| `src/numis/` | The core library — no GUI code, so it is testable and reusable from a CLI |
| `tests/` | Test suite |
| [`legacy/`](legacy/) | The original single-file label generator and its config, unchanged |

## Status

Documents 01 (core data model) and 02 (fields and special systems) are **implemented and tested**:
210 tests, schema verified equivalent to the normative SQL. Nothing is released, there is no GUI
yet, and the schema is not yet stable — test libraries are disposable.

Not yet built: search and filtering beyond full text (document 03), import/export and Numista
(04), wishlists (05), labels driven from the database (06), virtual albums and photographs (07).
Database migrations are also deferred: while the schema is unstable and no real data exists,
Alembic would only add ceremony. It arrives before the first release.

### What works today

- A library is an empty folder you create; it ships with **no** catalogues, grading scales or
  fields, and you build what you use
- User-defined columns from 13 field types, with units (grams, millimetres, fineness, angles) and
  exact integer money
- Coin dates that accept `1943`, `1736-1795`, `c. 350 BC`, `AH 1256`, `1930s`, `18th century` or
  something unreadable — sorting numerically either way, and saying when it guessed
- Subcollections with per-subcollection labels for a shared column, merging into one master view
- Catalogue numbers that sort like a catalogue reads, with ranges
- Grades from any number of user-defined standards on one comparable axis, with `Details` sorting
  beside its base grade and stickers nudging within it
- Multiple concurrent certifications and a crack-out/regrade history
- An append-only purchase ledger, with cost, proceeds and profit derived rather than typed
- Bulk add and bulk edit
- Full-text search that works for two-character CJK terms and folds diacritics

## Development

```bash
python3.12 -m venv .venv
.venv/bin/pip install -e ".[dev]"
.venv/bin/pytest
.venv/bin/ruff check src tests
```

Try it without writing any code:

```bash
python -m numis demo /tmp/Demo.numis      # a small library exercising the awkward cases
python -m numis info /tmp/Demo.numis
python -m numis list /tmp/Demo.numis --sort date_issued
python -m numis list /tmp/Demo.numis --subcollection ancients
```

The CLI exists to prove the core is usable without an interface. The spreadsheet-style table view
is the intended way to use the program, and comes next.

## Design principles

1. **Local first.** The collection lives in a folder on your machine. No account, no cloud, works
   offline.
2. **The user owns the schema.** Columns are user-defined. Nothing is undeletable, and features are
   *told* which field to use rather than inferring it.
3. **Core is separate from UI.** `src/numis/` never imports Qt, so the same logic serves a GUI, a
   CLI and the tests.
4. **Nothing is destroyed quietly.** Removal archives by default; the purchase ledger is
   append-only; permanent deletion is explicit and states what will be lost.
5. **Warn, do not block.** The database enforces structural integrity only. Judgements about
   plausible data become warnings, never refusals at save time.
