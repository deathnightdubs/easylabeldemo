# Architecture and handover notes

Written for whoever picks this up next — a person or another model. It explains how the code is
arranged, which rules are load-bearing, and which mistakes have already been made so they are not
made twice.

The design *specifications* live in [`docs/design/`](design/) and describe what the software should
do and why. **This document describes what the code actually is.** Where the two disagree, this one
is wrong and should be corrected.

---

## 1. What this is

A desktop numismatic (coin) collection manager. The user builds their own schema: there are no
built-in fields, catalogues or grading scales, and a new library is genuinely empty.

The interface is a spreadsheet-style grid — the central premise is "as easy as Excel to edit", so
values are typed straight into cells and everything is undoable.

| | |
|---|---|
| Language | Python 3.12 (3.11 minimum) |
| Storage | SQLite via SQLAlchemy 2.0 ORM, plus FTS5 for search |
| Interface | PySide6 (Qt 6) |
| Tests | pytest — 677 at the time of writing, Qt tests run headless |
| Lint | ruff, line length 100, `E,F,I,UP,B,SIM` |

A **library** is a folder, not a file: `MyCollection.numis/` containing `collection.db` plus
backups. Users open the folder.

---

## 2. The one structural rule

```
src/numis/          core library — imports no GUI toolkit, touches no widgets
src/numis/ui/       Qt interface — the only place PySide6 is imported
```

This is not stylistic. It is what allows the core to be tested without a display, driven by the
CLI, and reused by any later interface. `PySide6` appearing anywhere outside `src/numis/ui/` is a
bug.

The core is installable and usable **without** PySide6 at all — see the `gui` extra in
`pyproject.toml`.

### Module map

| Module | Responsibility |
|---|---|
| `models.py` | Every SQLAlchemy model, plus the FTS5 DDL and its sync triggers. No logic. |
| `services.py` | **All business logic.** One class, `CollectionService`, sectioned by area. ~2000 lines; the section comments (`# -- grading ---`) are the map. |
| `db.py` | Creating, opening, backing up and migrating a library. `Library` wraps engine + path. |
| `migrations.py` | Hand-written ordered migrations. See §6. |
| `fields/` | The field-type system: `registry.py` (13 types), `dates.py` (coin dates), `units.py` (mass, length, purity, angle, money). |
| `catalogs.py` | Catalogue number parsing and the sort-segment scheme that makes `2` precede `10`. |
| `grading.py` | Calculated grade values and how a grade *reads*. |
| `columns.py` | `ColumnDisplay` — how a special-system column displays itself. |
| `filters.py` | `Criterion`, `FilterGroup`, `SortKey` — a filter as a value. No SQL, no ORM. |
| `filter_sql.py` | Translating those into SQLAlchemy. |
| `search.py` | Building FTS5 queries, including CJK segmentation. Pure text, no SQL. |
| `constants.py` | Every closed vocabulary (statuses, kinds, event types). Check constraints reference these. |
| `errors.py` | Exceptions, and `Warning_` — see §4. |
| `cli.py` | A thin CLI, useful for scripted checks. |

| UI module | Responsibility |
|---|---|
| `main_window.py` | The window: toolbar, menus, subcollection switching, all the top-level actions. |
| `table_model.py` | `QAbstractTableModel` over the collection. The grid's brain. |
| `sheet_view.py` | The table widget and its spreadsheet behaviours (Enter moves down, block paste, fill down, context menus). |
| `commands.py` | `QUndoCommand` subclasses. **Every** grid mutation goes through one. |
| `detail_panel.py` | The dock beside the grid: catalogue numbers, grades, certifications, links for the selected coin. |
| `grade_dialog.py`, `modifier_dialogs.py` | Recording a grade; managing modifier definitions. |
| `fields_dialog.py` | Adding/removing/renaming columns. |
| `column_settings.py` | Per-column display settings (batch C). |
| `filter_dialog.py` | Building a filter (batch D). |
| `clipboard.py` | Tab-separated block copy/paste, so it interoperates with Excel. |

---

## 3. The data model in one page

```
library_meta          one row: schema version, currency, preferences
subcollection         a named group of coins; carries a naming template
specimen              ONE PHYSICAL COIN. No quantity column, ever — see below
  ├─ field_value_text / _number / _money / _date / _bool / _json
  ├─ catalog_reference  → catalog
  ├─ specimen_grade     → grade_scale
  │    └─ specimen_grade_modifier → grade_modifier
  │                              → certification   (a sticker's issuer)
  ├─ certification      → grading_company, optionally → specimen_grade
  ├─ external_link
  ├─ specimen_event     append-only purchase/sale ledger
  └─ specimen_search    materialised text, mirrored into specimen_fts (FTS5)

field_definition      a user-defined column; data_type keys into fields/registry
subcollection_block   places a field OR a special system in a subcollection's layout
saved_view            a named filter + sort order
```

**There is no quantity column and no coin-type layer.** A lot of 47 coins is 47 rows, created in
one action by bulk add. This is deliberate and load-bearing: totals are always a plain row count,
and *bulk add and bulk edit are what replace inheritance*. Do not add a `quantity` column; it would
break every count and every ledger figure.

### Field values are split by storage type

`field_definition.data_type` (`text`, `weight`, `date`, `money`, …) determines which
`field_value_*` table holds the value. `fields.registry.FieldType.storage` names it, and
`models.VALUE_MODELS` maps it to the class. Numbers are stored in **canonical units** — grams,
millimetres, per-mille, degrees — and money in **integer minor units**. A filter or sort that
compares raw user text against these is wrong; go through `parse_value`.

Multi-valued fields use `seq`. The grid shows and sorts `seq == 0` only.

### Sort keys

`text` and `date` values carry `sort_value`, `sort_source` (`none`/`auto`/`manual`) and
`needs_review`. This is how `10 wen` follows `1 wen` and how `1736-1795` sorts at 1765.5. When the
application guesses, it flags the cell (pale yellow) rather than interrupting entry with a dialog —
and a `manual` value is never overwritten by the parser afterwards. That combination is what makes
the flag trustworthy rather than noise.

### `rank`, not `is_primary`

Catalogue references, grades, certifications and links each carry `rank`: 1 is the one a
single-value column shows, 2 and 3 behind it. It replaced a boolean `is_primary`, because "which is
the *second* one you would cite" is a real question. Ranks are **not** uniquely enforced, so every
query orders by `(rank, id)` for a stable tie-break, and `service.reorder()` renumbers 1..n.

---

## 4. Invariants — break these and things get subtly wrong

### 4.1 Refuse almost nothing; warn instead

`errors.Warning_` is a record, never raised. The library refuses only what would corrupt meaning or
lose data. A duplicate certification number, an implausible weight — these are warnings collected on
`service.warnings` and surfaced by the interface. See `errors.py`.

### 4.2 A Python exception cannot cross Qt's C++ layer

This one has bitten repeatedly and is the single most important thing to know about the UI.

An exception raised inside a Qt slot is **printed and swallowed**. The user sees nothing happen. Worse, if it came from a failed SQL statement, the SQLAlchemy session is now unusable, so *every
later operation also fails* — the window looks dead and the coins look gone.

Three mechanisms guard against it, and new code must use them:

- `commands._Command.guarded()` — undo commands catch their own failures and set `self.error`.
- `MainWindow.guard(description, work)` — wraps risky work, rolls back, reports in the status bar.
  It returns whatever `work` returns, so **`work` must return something truthy** on success.
- Panels emit a `failed` signal; the *window* shows the dialog. A panel must never open a modal
  itself, because a modal in a headless test can never be dismissed and the suite hangs.

### 4.3 A grid refresh is all-or-nothing

`SpecimenTableModel.refresh()` builds columns, rows and cached values into locals and only then
assigns them. A failure part-way through used to leave new columns beside old rows — one
subcollection's coins under another's headings.

### 4.4 A sort is remembered by target, not by column position

Column 6 means different things in different subcollections. Sorts are stored as
`SortKey("field:weight")`, and a key whose column leaves the screen is dropped with
`sort_cleared` emitted so the view clears its indicator.

### 4.5 Filters compile to `EXISTS`, never joins

A coin can hold several values for one field, several catalogue numbers, several grades. A join
multiplies rows: `ruler is not Victoria` would match a coin whose *second* ruler is Albert, and
counts would double-count. `EXISTS`/`NOT EXISTS` asks the intended question and composes correctly
under AND/OR/NOT. Sort keys are scalar subqueries for the same reason.

### 4.6 One query answers the grid

`CollectionService.query_specimens()` handles filtering, searching and sorting together.
They were once three code paths and the result was that searching silently discarded the sort while
a sorted column silently ignored the filter. Do not add a fourth path.

### 4.7 The search index maintains itself

`service.reindex(specimen)` is called from the service at every point that changes indexed content
(values, names, identifiers, catalogue numbers). It used to be the caller's job, nobody in the UI
did it, and search was quietly stale for everything typed into the grid. Keep it in the service.

---

## 5. How the grid works

```
MainWindow
 ├── SheetView (QTableView)      keyboard, clipboard, context menus, header clicks
 ├── SpecimenTableModel          rows = specimens, columns = FIXED_COLUMNS + Column list
 ├── QUndoStack                  every mutation is a command
 └── DetailPanel (QDockWidget)   the special systems for the selected coin
```

Columns are `services.Column(key, label, kind, field_id, data_type, display)`. `kind` is `field` or
one of `catalogues` / `grades` / `certifications` / `links`. The first four grid columns are fixed:

```python
ID_COLUMN, NAME_COLUMN, SUBCOLLECTION_COLUMN, STATUS_COLUMN = 0, 1, 2, 3
```

User columns start at index 4. Never hardcode positions beyond that — resolve by header name
(there is a helper doing exactly that in `tools/screenshots.py` and in the tests) because
hardcoding positions is what broke the grid when switching subcollections.

`table_model.sort_target(column)` converts a column to a filter/sort target: a field becomes
`field:<key>`, a special system is named by its kind. Identity columns use `__id__`, `__name__`,
`__subcollection__`, `__status__`.

Special-system cells are rendered by `service.special_cell(specimen, kind, display)` — in the
*service*, not the model, so exports and label templates can render a column identically.

### Editing flow

`setData` → `commands.SetValues` → `undo.push(...)` → command calls the service → `model.refresh()`.
Rejected input reports itself through the `error` signal and never enters the undo history.

---

## 6. Migrations

`SCHEMA_VERSION` lives in `src/numis/__init__.py`. Migrations are **hand-written, ordered and
idempotent** in `migrations.py`. Alembic was rejected: users open these libraries directly, and a
migration that locks someone out of their own collection is unrecoverable, so they must be readable
rather than generated.

Adding a schema change means all four of:

1. Update the model in `models.py`.
2. Update `docs/design/schema/base-v1.sql` — it is **normative**, and
   `tests/test_schema_parity.py` asserts the ORM matches it.
3. Add a `Migration` to `MIGRATIONS` and bump `SCHEMA_VERSION`. There is a test asserting those two
   agree, because shipping a change without a migration locks people out.
4. Check `Library.migrate()` still works. Table rebuilds need `PRAGMA foreign_keys=OFF`, an
   explicit `BEGIN`/`COMMIT`, and a `PRAGMA foreign_key_check` afterwards — all already in place.

A backup is taken automatically before migrating.

**Adding keys inside an existing `config_json` needs no migration at all.** That is why per-column
display settings went there.

---

## 7. Running it

```bash
python -m venv .venv && .venv/bin/pip install -e ".[dev]"

.venv/bin/pytest                      # the suite
.venv/bin/ruff check src tests tools  # lint
.venv/bin/python -m numis.ui          # the application
```

### Headless Qt

```bash
QT_QPA_PLATFORM=offscreen .venv/bin/pytest
QT_QPA_PLATFORM=offscreen .venv/bin/python tools/screenshots.py docs/images
```

On a bare Linux container Qt needs system libraries that are easy to forget:

```
mesa-libGL mesa-libEGL libxkbcommon libxkbcommon-x11 xcb-util-cursor
fontconfig dejavu-sans-fonts google-noto-sans-cjk-ttc-fonts
```

The CJK font matters: without it the Chinese legends in tests and screenshots render as boxes.

### Testing conventions

- Test names are sentences describing behaviour, not `test_method_1`.
- Where a test exists because of a real bug, the docstring says so. Those comments are the
  regression history and are worth preserving.
- Fixtures build only what the test needs. There is deliberately **no** "sample collection"
  fixture: the application ships empty, and a fixture quietly supplying catalogues would hide
  whether the code requires them.
- **Never let a test open a real modal.** Subclass the dialog and override `exec()`. Where the
  window connects a signal to something that opens one, `disconnect()` it first. A blocked modal
  does not fail — it hangs the suite, and pytest's buffered output makes that look like a silent
  crash. Build menus in a method that returns the menu so its contents can be inspected without
  `exec()` (see `SheetView.header_menu`).

---

## 8. Traps

| Trap | What happens |
|---|---|
| Tuples as Qt item data | `QComboBox.findData` cannot match a Python tuple; it returns `-1` and the selection is silently lost. Use a `"kind:id"` string. |
| `QAbstractItemView.SelectionMode.NoSelection` | Also prevents *editing* cells in that widget. |
| Exceptions in slots | Swallowed; poison the session. See §4.2. |
| `MainWindow.guard` returning `None` | It returns `work()`'s value, so a `work` returning `None` reads as failure. |
| Sorting text in SQLite | Default collation is case-sensitive; text sorts go through `func.lower()`. |
| `value_grid` is `seq == 0` | Multi-valued fields show only their first value. |
| Qt's built-in header sorting | Deliberately disabled — it gives the model no way to know whether Ctrl was held, which is the whole difference between "sort by this" and "then by this". |
| `/tmp` in some sandboxes | Not persistent between shell invocations; use `.scratch/` (gitignored). |

---

## 9. Where things stand

Implemented and tested: design documents 01 (core data model), 02 (fields and special systems) and
03 (search, sorting, filtering), plus the interface.

Not built: import/export and Numista (04), wishlists (05), labels driven from the database (06),
virtual albums and photographs (07). The history ledger has no editor — prices can be recorded
through the core and CLI but not the interface. Grouping and per-view column sets are stored
(`saved_view.group_by`, `columns_json`) but unread.

`legacy/` holds the original single-file 2×2 holder label generator, unchanged. Its behaviour is to
be *preserved* by document 06, not replaced.

### Deliberate non-goals

- **No coin-type/inheritance layer.** Bulk add and bulk edit replace it.
- **No pre-loaded reference data.** A new library is empty; presets will ship later as JSON.
- **One coin belongs to one subcollection.** Saved views (including "ID is any of …") cover the
  "this coin fits two collections" case without a join table. If that changes, the join table is
  additive — nothing depends on the single-membership assumption structurally.

---

## 10. If you are adding a feature

- **A new field type** → register a `FieldType` in `fields/registry.py`. Declare `storage`,
  `sort_column` and `filter_operators`; the grid, the filter dialog and sorting all pick it up
  without further changes.
- **A new filter operator** → add it to the relevant tuple in `fields/registry.py` or
  `filters.py`, then handle it in `filter_sql.py`. Add words for it to `OPERATOR_WORDS` so it can
  describe itself.
- **A new column display option** → add a field to `columns.ColumnDisplay`, a widget in
  `ui/column_settings.py`, and honour it in `services.special_cell` (or `grading.render`).
- **A new grid mutation** → a `_Command` subclass in `ui/commands.py`. Never write to the session
  from a widget.
- **A new special system column** → add the kind to `constants.BLOCK_KINDS`, a branch in
  `service.special_cell`, and an entry in `fields_dialog.SPECIAL_BLOCKS`.

Prose style throughout the codebase: comments explain *why*, particularly when the reason is a bug
that has already happened. Docstrings are written for a reader who does not already know the
domain. Please keep that up — it is most of what makes the code navigable.
