# Design specifications

Design documents for the numismatic collection suite, written **before** implementation and
approved section by section. The current label generator (`LabelGenerator_v5_1_offsets.py`)
becomes one module of this suite.

Naming note: the product name is deliberately undecided. These documents use the placeholder
Python package name `numis`. Renaming later touches only import paths, never the schema.

## Status

| # | Document | Scope | Status |
|---|---|---|---|
| 01 | [Core data model](01-core-data-model.md) | Library, subcollections, types/specimens, catalogs, certification, media, storage, history, tags, search | **Proposed — awaiting approval** |
| 02 | [Field types and semantic roles](02-field-types-and-semantic-roles.md) | The field-type palette, semantic roles, presets, schema-change semantics | **Proposed — awaiting approval** |
| 03 | Holder geometry, label layouts and label instances | `holder_template`, `label_layout`, `label_instance`, per-side offsets, migration of the existing generator | Not started |
| 04 | Virtual albums | `album`, `album_page`, `page_slot`, canvas rendering | Not started |
| 05 | Wishlist slots and the matching engine | Slot criteria DSL, evaluation states | Not started |
| 06 | Import and export | Excel/CSV mapping, Numista, preset sharing | Not started |
| 07 | Search, filtering and smart collections | Filter tree, query builder, saved views | Not started |

Documents 01 and 02 define **the base**. Nothing else is built until they are settled, because
every later section is expressed in their terms.

## Conventions used in these documents

- SQL is written as SQLite DDL. It is the normative specification; the SQLAlchemy models must
  produce an equivalent schema, and Alembic migrations are generated from the models.
- "Base" means the v1 schema created by the first migration.
- "Reserved" means a name or seam that later sections will use, deliberately left unbuilt.
