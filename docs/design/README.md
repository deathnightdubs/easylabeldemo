# Design specifications

Design documents for the numismatic collection suite, written **before** implementation and approved
section by section. The current label generator (`LabelGenerator_v5_1_offsets.py`) becomes one module
of this suite; its behaviour is preserved, not replaced.

Naming note: the product name is deliberately undecided. These documents use the placeholder Python
package name `numis`. Renaming later touches only import paths, never the schema.

## Start here

- **[OVERVIEW.md](OVERVIEW.md)** — plain-English summary of every decision. No SQL. Read this first.
- **[schema/base-v1.sql](schema/base-v1.sql)** — the normative base schema, verified against
  SQLite 3.40. The markdown explains it; this file defines it.

## Documents

| # | Document | Scope | Status |
|---|---|---|---|
| 01 | [Core data model](01-core-data-model.md) | Library, subcollections, specimens, field storage, sort keys, history ledger, tags, search | **Implemented** |
| 02 | [Fields and special systems](02-fields-and-special-systems.md) | Field type palette, feature bindings, display labels, catalogues, grades, certifications, external links, schema changes, presets | **Implemented**, including editors for the special systems |
| — | The interface | Spreadsheet-style grid, undo, copy and paste, column management | **Implemented**; not separately specified, since it is a view over 01 and 02 |
| 03 | [Search, sorting and filtering](03-search-sorting-filtering.md) | Filter tree, operators from the field registry, multi-column sorting, per-column display, saved views | **Implemented**, except grouping and per-view column sets |
| 04 | Import and export | Spreadsheet mapping, Numista ID import with user-defined field mapping, preset sharing | Not started |
| 05 | Wishlist and slot matching | Slot criteria, the deterministic matching engine, fulfilment states | Not started |
| 06 | Labels | Holder geometry, label layouts, saved label instances, per-holder front/back offsets, migration of the existing generator | Not started |
| 07 | Virtual albums and media | Albums, pages, pockets, photographs, physical storage locations | Not started |

Documents 01 and 02 define **the base**; 03 is treated as part of the foundation rather than a later
feature, because a collection that cannot be sliced is only a list.

## Order of work

1. Core database — 01 and 02 ✅
2. Search, sorting and filtering — 03 ✅
3. Import and export, including Numista — 04
4. Wishlist — 05
5. Labels — 06
6. Virtual albums and photographs — 07

## Conventions

- SQL is SQLite DDL and lives in `schema/`. The SQLAlchemy models must produce an equivalent schema,
  asserted by `tests/test_schema_parity.py`. Migrations are hand-written and ordered in
  [`src/numis/migrations.py`](../../src/numis/migrations.py); Alembic was rejected because a
  single-file library that the user opens directly needs migrations that are readable and
  idempotent rather than generated.
- "Base" means the v1 schema created by the first migration.
- "Deferred" means a named seam that a later document will attach to, deliberately left unbuilt.
- Claims about SQLite behaviour in these documents were tested rather than assumed. Two designs were
  corrected that way: CJK full-text search, and the direction of fees in profit calculations.
