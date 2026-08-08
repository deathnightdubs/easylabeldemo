# 01 — Core data model

Status: **Proposed — awaiting approval**
Scope: the base v1 schema. Labels, albums, wishlists and importers are specified separately and
are deliberately excluded here; the seams they will attach to are listed in Part 12.

---

## Part 0 — Foundational decisions

These apply to every table. They are listed first because most schema bugs in collection software
come from getting these wrong once and repeating the mistake everywhere.

### 0.1 The Library

A **Library** is a folder, not just a file. Opening a library means opening this folder:

```
MyCollection.numis/
├── collection.db          SQLite database (this document)
├── media/                 originals, never modified after import
│   ├── originals/2026/07/<uuid>.jpg
│   ├── previews/<uuid>.webp
│   └── thumbs/<uuid>.webp
├── backups/               timestamped .db copies
├── presets/               presets installed into this library
└── exports/               user output (PDF labels, CSV, JSON)
```

Rationale: photographs must not live in SQLite, but a collection is worthless if the database and
its photographs can be separated by accident. Making the library a single folder means "back up my
collection" and "move my collection to another computer" are one operation. All media paths in the
database are **relative to `media/`**, never absolute, so the folder is portable across machines
and operating systems.

One library is open at a time. The list of recent libraries lives in the OS user-config directory,
not in any library.

### 0.2 Identity: integer PK + UUID

Every entity table has both:

- `id INTEGER PRIMARY KEY` — the SQLite rowid alias. Used for all joins and foreign keys. Fast and
  compact.
- `uuid TEXT NOT NULL UNIQUE` — a UUIDv4 supplied by the application, never reused.

The UUID exists because rows must be identifiable *outside* their database: preset sharing,
merging two libraries, re-importing an export without creating duplicates, referencing a specimen
in an exported album, and any future sync. An integer id is meaningless outside its own file.
Adding UUIDs later would be a painful, error-prone migration, so they are in the base.

### 0.3 Time

- All timestamps are UTC, stored as ISO-8601 text with milliseconds: `2026-07-26T14:03:11.482Z`.
  Text is chosen over integers because a collection database should be human-readable when opened
  in any SQLite browser, which matters for an open-source tool people trust with irreplaceable data.
- Python always works with timezone-aware `datetime` objects. Naive datetimes are rejected at the
  data-access boundary.
- `created_at` / `updated_at` are maintained by the application (SQLAlchemy ORM events), not by SQL
  triggers, so the behaviour is testable and identical for bulk imports.
- **Calendar dates that describe coins are a different thing entirely** and never use these
  columns. See 0.6.

### 0.4 Money: integers only

Money is stored as `amount_minor INTEGER` plus `currency TEXT` (ISO 4217, 3 characters).
`amount_minor` is the amount in the currency's minor unit (cents, pence, yen — the exponent comes
from a currency table in code, since JPY has 0 decimals and USD has 2).

Floating point is never used for money. `0.1 + 0.2 != 0.3` is an acceptable curiosity in a physics
calculation and unacceptable in a ledger that computes what a collection cost over thirty years.

Multi-currency purchases are supported: the event stores the original amount and currency, plus an
optional `fx_rate_to_base` and the derived `base_amount_minor`, so historical purchases keep their
true value while reports can still total in one currency.

### 0.5 Physical measurement units

Different rule from money, deliberately:

| Quantity | Canonical storage | Type | Display |
|---|---|---|---|
| Mass | grams | `REAL` | g, mg, grains, oz t, dwt |
| Length | millimetres | `REAL` | mm, in |
| Fineness | parts per thousand (0–1000) | `REAL` | 0.900, 900, 90%, 22K |
| Angle (die axis) | degrees | `REAL` | degrees or clock hours |

`REAL` is a 64-bit double, which represents a weight of `27.153 g` with an error around 1e-15 g.
That is fifteen orders of magnitude below the precision of any coin scale, so exact-decimal storage
buys nothing here, whereas money arithmetic accumulates across thousands of transactions and does
need exactness. Equality comparisons on measurements always use a tolerance, never `=`.

Display units are a per-field or per-library preference. Stored values never change when the user
switches display units.

### 0.6 Dates that describe coins: the fuzzy date

Numismatic dates cannot be a `DATE` column. Real examples the model must hold without loss:

- `1943` — exact year
- `1736–1795` — a reign, an actual range
- `c. 350 BC` — approximate, negative year
- `AH 1256` — a different calendar entirely
- `Qianlong, year 22` — regnal dating
- `undated` — genuinely unknown, but the coin still needs to sort somewhere sensible
- `1804 (restrike 1860)` — two dates with different meanings

The base therefore stores a **composite fuzzy date** wherever a coin-related date is needed:

| Column | Purpose |
|---|---|
| `year_start`, `year_end` | signed integers; the normalised span. Negative = BC using the historical convention (no year zero, so 1 BC is `-1`). Equal values mean a single year. |
| `month_start`, `day_start`, `month_end`, `day_end` | nullable; present only when known |
| `precision` | `exact_day`, `exact_month`, `exact_year`, `range`, `decade`, `century`, `circa`, `unknown` |
| `calendar` | `gregorian`, `julian`, `islamic_ah`, `chinese_regnal`, `jewish`, `french_republican`, `other` |
| `era_label` | the era/regnal expression as given, e.g. `AH`, `Qianlong 22`, `Meiji 3` |
| `display` | exactly what the user typed or what should be shown; never regenerated over the top of user input |
| `sort_key` | derived integer used for ordering; see below |

`sort_key` is computed as the midpoint of the span (`(year_start + year_end) // 2`) scaled by 10000
plus month/day when known, so `1736–1795` sorts between `1700` and `1800` rather than at either
extreme. Unknown dates get `NULL` and sort last by explicit `NULLS LAST` handling.

Two columns carry the whole design: `year_start`/`year_end` make *"coins minted between X and Y"*
a plain indexed integer range query even when the underlying data is a reign, an approximation, or
a non-Gregorian era; and `display` guarantees the collector's own expression is never destroyed by
normalisation. This is the single most important difference between software built for numismatists
and a generic database with a date picker.

### 0.7 Deletion

- Entities holding user-authored content — `specimen`, `coin_type`, `variety`, `media_asset` — use
  soft deletion (`deleted_at TEXT NULL`) so a mis-click is recoverable from a Trash view. All normal
  queries filter `deleted_at IS NULL`.
- Purging from Trash is a real delete, cascading to that record's field values, links and media
  links. Media *files* are only removed once no asset references them.
- Join/child rows (`field_value_*`, `media_link`, `specimen_tag`) are hard-deleted with
  `ON DELETE CASCADE`.
- Registry rows referenced by data (`catalog`, `grading_company`, `storage_location`) use
  `ON DELETE RESTRICT` or `SET NULL`; you may not silently destroy every KM number by deleting a
  catalog.
- `specimen_event` is **append-only and never deleted**. See Part 8.

### 0.8 Validation philosophy: warn, rarely block

The database enforces only what protects integrity: foreign keys, exactly-one-subject checks,
enumerated status values. It deliberately does **not** enforce things like "certification numbers
are unique" or "weight must be under 100 g", because a collector entering a real coin at 11pm must
never be stopped by software that thinks it knows their collection better than they do. Those
become **warnings** surfaced in the UI with a "looks like a duplicate cert number — continue?"
affordance, and a Library Health report that lists suspicious data without changing it.

### 0.9 Connection settings

Applied on every connection:

```sql
PRAGMA journal_mode = WAL;        -- crash-safe, allows a reader during a write
PRAGMA foreign_keys = ON;         -- off by default in SQLite; must be set per connection
PRAGMA busy_timeout = 5000;
PRAGMA synchronous = NORMAL;      -- with WAL, safe against app crash
PRAGMA temp_store = MEMORY;
```

`foreign_keys = ON` per connection is easy to forget and silently disables every `REFERENCES`
clause in this document, so it is set in a single SQLAlchemy `connect` event handler.

### 0.10 Migrations and backups

- Alembic, with the first migration creating exactly this schema. `library_meta.schema_version`
  records the current Alembic revision.
- Before any migration runs, the application copies `collection.db` to
  `backups/collection-<timestamp>-pre-<revision>.db`. Non-negotiable: from the moment this app is
  stateful, it is responsible for data it cannot recreate.
- Opening a library whose `schema_version` is *newer* than the running application is refused with
  a clear message rather than attempted.
- A rolling scheduled backup (configurable count) runs on library close.

### 0.11 DDL shorthand used below

To keep the schema readable, three fragments are written as macros and expanded exactly as shown:

```sql
-- <id>
  id   INTEGER PRIMARY KEY,
  uuid TEXT NOT NULL UNIQUE,

-- <audit>
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,

-- <softdel>
  deleted_at TEXT NULL,

-- <subject>   attach a row to exactly one of the three subject entities
  specimen_id  INTEGER NULL REFERENCES specimen(id)  ON DELETE CASCADE,
  coin_type_id INTEGER NULL REFERENCES coin_type(id) ON DELETE CASCADE,
  variety_id   INTEGER NULL REFERENCES variety(id)   ON DELETE CASCADE,
  CHECK ((specimen_id IS NOT NULL) + (coin_type_id IS NOT NULL)
       + (variety_id IS NOT NULL) = 1)
```

`<subject>` is used by field values, catalog references and media links. Three nullable foreign
keys with a `CHECK` are chosen over a generic `(subject_kind, subject_id)` pair because the latter
cannot have real foreign keys, which means orphaned rows and no cascade deletes — the classic way
these databases rot.

**SQLite NULL gotcha, applied throughout:** in a `UNIQUE` index, SQLite treats NULLs as distinct,
so a unique index spanning the three subject columns would *not* prevent duplicates. Every
uniqueness rule over an optional column is therefore written as a **partial index** with
`WHERE <column> IS NOT NULL`, or uses a generated `scope_key` column.

---

## Part 1 — Library metadata and subcollections

```sql
CREATE TABLE library_meta (
  id INTEGER PRIMARY KEY CHECK (id = 1),      -- exactly one row
  library_uuid            TEXT NOT NULL UNIQUE,
  schema_version          TEXT NOT NULL,      -- Alembic revision
  app_version_created     TEXT NOT NULL,
  app_version_last_opened TEXT,
  media_root              TEXT NOT NULL DEFAULT 'media',
  base_currency           TEXT NOT NULL DEFAULT 'USD',
  unit_system             TEXT NOT NULL DEFAULT 'metric',  -- metric | imperial
  settings_json           TEXT NOT NULL DEFAULT '{}',
  <audit>
);
```

`settings_json` holds preferences with no query requirement (last window layout, default fonts).
Anything that must be filtered or sorted gets a real column instead — JSON is for display-only data.

### Subcollections

A subcollection is the "separate sheet" concept: World Coins, Ancients, Chinese Cash, Tokens.

```sql
CREATE TABLE subcollection (
  <id>
  name              TEXT NOT NULL,
  slug              TEXT NOT NULL UNIQUE,     -- stable key for presets/imports
  description       TEXT,
  sort_order        INTEGER NOT NULL DEFAULT 0,
  colour            TEXT,                     -- UI accent, e.g. '#8B6F47'
  icon              TEXT,
  naming_template   TEXT NOT NULL DEFAULT '{country} {denomination} {date}',
  default_view_json TEXT NOT NULL DEFAULT '{}',
  is_archived       INTEGER NOT NULL DEFAULT 0,
  <audit>
);
CREATE INDEX ix_subcollection_order ON subcollection(sort_order, name);
```

Design consequences, matching the requirement that subcollections may differ yet still be viewable
together:

1. **Fields can be scoped to a subcollection.** `field_definition.subcollection_id` is `NULL` for
   global fields shared by everything, or set for fields belonging to one subcollection. Ancients
   can have *Emperor* and *RIC* while World Coins has *Country* and *KM#*, with no cross-pollution.
2. **A specimen has exactly one home subcollection** (`specimen.subcollection_id NOT NULL`). This
   preserves the spreadsheet-sheet mental model and avoids the ambiguity of a coin belonging
   partly to two schemas. Cross-cutting groupings — "all my silver regardless of subcollection" —
   are handled by tags and saved views, which is a better tool for that job.
3. **The combined view is a union.** Global fields, and any subcollection-specific fields that share
   the same *semantic role* (document 02), collapse into one column; the rest appear as extra
   sparse columns, blank where inapplicable. This works only because values are stored per-field in
   typed value tables rather than as fixed table columns — the heterogeneous combined view is a
   direct consequence of the field architecture, not a special case bolted on later.
4. `naming_template` renders `specimen.display_name` from role-bearing fields, which is why no
   individual field ever has to be mandatory (see Part 3).

---

## Part 2 — The field system

The user-facing model is: *"the app offers field types; I build whatever schema I want; presets are
just convenient starting points I can freely edit."* Nothing in the default preset is
undeletable.

### 2.1 Two kinds of field

| Kind | Meaning | Storage |
|---|---|---|
| `value` | one scalar (or an ordered list) per record | typed `field_value_*` tables |
| `relational` | a structured, multi-row child collection | its own dedicated table |
| `computed` | derived from other fields by a formula | not stored; cached in `specimen_cache` |

`relational` is the important subtlety. Catalog references, certifications, images, provenance and
history are inherently multi-row with their own columns and their own integrity rules — flattening
them into generic values would destroy exactly the structure that makes them useful. But the user
should still be able to add, remove, rename, reorder and group them like any other field.

So a `relational` field definition **stores no values**. It is a *presentation and behaviour
binding*: "show the catalog references here, labelled *Catalogue Numbers*, in the Identification
group, third from the top, sorted by catalog code." Remove the field and the block disappears from
the form; the underlying references remain intact and reappear if it is added back. One consistent
"manage fields" experience, without paying for it in data integrity.

### 2.2 Groups and definitions

```sql
CREATE TABLE field_group (
  <id>
  subcollection_id INTEGER NULL REFERENCES subcollection(id) ON DELETE CASCADE,
  scope_key        INTEGER NOT NULL GENERATED ALWAYS AS (COALESCE(subcollection_id, 0)) STORED,
  key              TEXT NOT NULL,
  label            TEXT NOT NULL,
  sort_order       INTEGER NOT NULL DEFAULT 0,
  collapsed_default INTEGER NOT NULL DEFAULT 0,
  <audit>
);
CREATE UNIQUE INDEX ux_field_group_key ON field_group(scope_key, key);
```

```sql
CREATE TABLE field_definition (
  <id>
  subcollection_id INTEGER NULL REFERENCES subcollection(id) ON DELETE CASCADE,
  scope_key        INTEGER NOT NULL GENERATED ALWAYS AS (COALESCE(subcollection_id, 0)) STORED,
  entity           TEXT NOT NULL CHECK (entity IN ('specimen','coin_type','variety')),
  key              TEXT NOT NULL,          -- immutable machine key, e.g. 'weight'
  label            TEXT NOT NULL,          -- freely renameable
  kind             TEXT NOT NULL CHECK (kind IN ('value','relational','computed')),
  data_type        TEXT NOT NULL,          -- registry key; see document 02
  relation         TEXT NULL,              -- kind='relational': catalog_reference|certification|media|provenance|specimen_link|event
  role             TEXT NULL,              -- semantic role; see document 02
  config_json      TEXT NOT NULL DEFAULT '{}',
  group_id         INTEGER NULL REFERENCES field_group(id) ON DELETE SET NULL,
  sort_order       INTEGER NOT NULL DEFAULT 0,
  is_multi         INTEGER NOT NULL DEFAULT 0,
  is_required      INTEGER NOT NULL DEFAULT 0,   -- a soft prompt, not a DB constraint
  is_indexed       INTEGER NOT NULL DEFAULT 1,
  is_protected     INTEGER NOT NULL DEFAULT 0,   -- cannot be deleted; presets ship none
  is_hidden        INTEGER NOT NULL DEFAULT 0,
  is_archived      INTEGER NOT NULL DEFAULT 0,   -- removed from UI, values retained
  show_in_table    INTEGER NOT NULL DEFAULT 0,
  default_value_json TEXT NULL,
  help_text        TEXT,
  origin_preset    TEXT NULL,              -- provenance of the definition
  <audit>
  CHECK (kind <> 'relational' OR relation IS NOT NULL)
);
CREATE UNIQUE INDEX ux_field_key  ON field_definition(scope_key, entity, key);
CREATE UNIQUE INDEX ux_field_role ON field_definition(scope_key, entity, role)
  WHERE role IS NOT NULL AND is_archived = 0;
CREATE INDEX ix_field_order ON field_definition(scope_key, entity, sort_order);
```

Notes on specific columns:

- `key` is **immutable**; `label` is freely editable. Presets, importers, label layouts and
  wishlist criteria all reference `key`, so allowing renames would silently break them. The user
  renames the *label* and nothing downstream notices.
- `is_protected` defaults to `0` and **the shipped presets set it nowhere**. This is the direct
  implementation of the requirement that even basic users can strip fields out of the default
  preset. It exists only so a future specialist preset can guard a field it genuinely cannot work
  without.
- `is_archived` versus deletion: archiving hides a field but keeps every value, and is the default
  action offered in the UI. Actual deletion is a separate, explicitly confirmed operation that
  reports how many values will be destroyed. Given that users are expected to reshape their schema
  freely, reversible removal is what makes that safe.
- `scope_key` is a stored generated column purely to make the unique indexes work: `subcollection_id`
  is nullable for global fields, and SQLite would treat those NULLs as distinct, defeating
  uniqueness. `COALESCE(..., 0)` gives global scope a real value to be unique on.
- `ux_field_role` enforces at most one field per semantic role per scope and entity, which is what
  lets features resolve "the grade field" unambiguously.

### 2.3 Options for category fields

```sql
CREATE TABLE field_option (
  <id>
  field_definition_id INTEGER NOT NULL REFERENCES field_definition(id) ON DELETE CASCADE,
  parent_id  INTEGER NULL REFERENCES field_option(id) ON DELETE CASCADE,  -- hierarchical categories
  value_key  TEXT NOT NULL,
  label      TEXT NOT NULL,
  sort_order INTEGER NOT NULL DEFAULT 0,
  colour     TEXT,
  is_archived INTEGER NOT NULL DEFAULT 0,
  <audit>
);
CREATE UNIQUE INDEX ux_field_option_key ON field_option(field_definition_id, value_key);
```

`parent_id` supports hierarchies such as Metal → Silver → Billon, or Region → Europe → Austria,
so category filters can mean "anything under Silver".

### 2.4 Typed value tables

Eight tables, one per storage shape. The reason for typed tables rather than one `value TEXT`
column is blunt: a single text column cannot sort `9` before `10`, cannot answer "weight between
5 g and 8 g", and cannot answer "minted between 1850 and 1875". Those three queries are most of
what a collection manager does.

```sql
CREATE TABLE field_value_text (
  id INTEGER PRIMARY KEY,
  field_definition_id INTEGER NOT NULL REFERENCES field_definition(id) ON DELETE CASCADE,
  <subject>
  seq   INTEGER NOT NULL DEFAULT 0,       -- ordinal for is_multi fields
  value TEXT NOT NULL
);
CREATE UNIQUE INDEX ux_fvtext_sp ON field_value_text(field_definition_id, specimen_id, seq)  WHERE specimen_id  IS NOT NULL;
CREATE UNIQUE INDEX ux_fvtext_ct ON field_value_text(field_definition_id, coin_type_id, seq) WHERE coin_type_id IS NOT NULL;
CREATE UNIQUE INDEX ux_fvtext_vr ON field_value_text(field_definition_id, variety_id, seq)   WHERE variety_id   IS NOT NULL;
CREATE INDEX ix_fvtext_lookup ON field_value_text(field_definition_id, value COLLATE NOCASE);
```

The same three partial unique indexes plus a lookup index are created for every value table below;
they are not repeated in this document.

```sql
CREATE TABLE field_value_number (
  id INTEGER PRIMARY KEY,
  field_definition_id INTEGER NOT NULL REFERENCES field_definition(id) ON DELETE CASCADE,
  <subject>
  seq        INTEGER NOT NULL DEFAULT 0,
  value      REAL NOT NULL,          -- canonical unit per data_type (g, mm, per-mille, plain)
  entered_as TEXT NULL,              -- '22K', '1/2 in', '420 gr' as typed
  is_approximate INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE field_value_money (
  id INTEGER PRIMARY KEY,
  field_definition_id INTEGER NOT NULL REFERENCES field_definition(id) ON DELETE CASCADE,
  <subject>
  seq              INTEGER NOT NULL DEFAULT 0,
  amount_minor     INTEGER NOT NULL,
  currency         TEXT NOT NULL,
  fx_rate_to_base  REAL NULL,
  base_amount_minor INTEGER NULL,
  as_of            TEXT NULL         -- valuations are dated; a 1998 estimate is not today's
);

CREATE TABLE field_value_date (
  id INTEGER PRIMARY KEY,
  field_definition_id INTEGER NOT NULL REFERENCES field_definition(id) ON DELETE CASCADE,
  <subject>
  seq         INTEGER NOT NULL DEFAULT 0,
  year_start  INTEGER NULL,
  month_start INTEGER NULL,
  day_start   INTEGER NULL,
  year_end    INTEGER NULL,
  month_end   INTEGER NULL,
  day_end     INTEGER NULL,
  precision   TEXT NOT NULL DEFAULT 'exact_year'
              CHECK (precision IN ('exact_day','exact_month','exact_year','range',
                                   'decade','century','circa','unknown')),
  calendar    TEXT NOT NULL DEFAULT 'gregorian',
  era_label   TEXT NULL,
  display     TEXT NOT NULL,
  sort_key    INTEGER NULL
);
CREATE INDEX ix_fvdate_span ON field_value_date(field_definition_id, year_start, year_end);
CREATE INDEX ix_fvdate_sort ON field_value_date(field_definition_id, sort_key);

CREATE TABLE field_value_bool (
  id INTEGER PRIMARY KEY,
  field_definition_id INTEGER NOT NULL REFERENCES field_definition(id) ON DELETE CASCADE,
  <subject>
  seq   INTEGER NOT NULL DEFAULT 0,
  value INTEGER NOT NULL CHECK (value IN (0,1))
);

CREATE TABLE field_value_option (
  id INTEGER PRIMARY KEY,
  field_definition_id INTEGER NOT NULL REFERENCES field_definition(id) ON DELETE CASCADE,
  <subject>
  seq             INTEGER NOT NULL DEFAULT 0,
  field_option_id INTEGER NOT NULL REFERENCES field_option(id) ON DELETE RESTRICT
);

CREATE TABLE field_value_grade (
  id INTEGER PRIMARY KEY,
  field_definition_id INTEGER NOT NULL REFERENCES field_definition(id) ON DELETE CASCADE,
  <subject>
  seq         INTEGER NOT NULL DEFAULT 0,
  scale       TEXT NOT NULL,            -- sheldon | adjectival_us | adjectival_uk | european | proof | raw_note
  numeric     REAL NULL,                -- 64 for MS-64
  adjectival  TEXT NULL,                -- 'XF', 'SUP', 'gVF'
  qualifier   TEXT NULL,                -- '+', 'star', 'PL', 'DPL', 'details'
  problem     TEXT NULL,                -- 'cleaned', 'holed', 'environmental damage'
  normalised  REAL NULL,                -- 1..70 cross-scale sort axis
  display     TEXT NOT NULL,
  graded_by   TEXT NULL                 -- 'self', 'seller', 'NGC' for a non-slabbed opinion
);
CREATE INDEX ix_fvgrade_norm ON field_value_grade(field_definition_id, normalised);

CREATE TABLE field_value_json (
  id INTEGER PRIMARY KEY,
  field_definition_id INTEGER NOT NULL REFERENCES field_definition(id) ON DELETE CASCADE,
  <subject>
  seq   INTEGER NOT NULL DEFAULT 0,
  value TEXT NOT NULL                   -- escape hatch: display-only, never sorted or filtered
);
```

`field_value_grade` earns a dedicated table because grade is the attribute collectors sort and
filter by most, and it is expressed in mutually incompatible vocabularies. Storing `MS-64`, `SUP`
and `gVF` as text makes "grade at least VF" impossible; `normalised` provides one comparable axis
while `scale`, `adjectival` and `display` preserve exactly what was recorded. The normalisation
table (Sheldon 1–70, US and UK adjectival, European `B/TB/SUP/FDC`) lives in code, not in the
database, so it can be corrected in a patch release without a migration.

`field_value_money.as_of` exists because a valuation without a date is misinformation.

### 2.5 Why not JSON columns for everything

SQLite's JSON1 functions would make a single `values_json` blob per specimen tempting, and it would
be quicker to build. It is rejected for values that are queried because expression indexes over
JSON extraction are fragile, every filter becomes a full scan on large collections, type coercion
is implicit and surprising, and range queries on dates and weights — the app's core competence —
degrade badly. `field_value_json` remains available for genuinely exotic, display-only data.

---

## Part 3 — Coin types, varieties and specimens

The type/specimen split is the keystone of the model: a **type** is what the catalogue says exists,
a **specimen** is a physical object owned. Three specimens of one type legitimately differ in
grade, weight, price, photographs and storage position.

```sql
CREATE TABLE coin_type (
  <id>
  subcollection_id INTEGER NULL REFERENCES subcollection(id) ON DELETE SET NULL,
  display_name  TEXT NOT NULL,
  source        TEXT NOT NULL DEFAULT 'manual',   -- manual | numista | import:<name>
  source_ref    TEXT NULL,                        -- external id, e.g. Numista type id
  source_url    TEXT NULL,
  attribution   TEXT NULL,                        -- required by some external sources' terms
  fetched_at    TEXT NULL,
  <audit>
  <softdel>
);
CREATE UNIQUE INDEX ux_coin_type_source ON coin_type(source, source_ref) WHERE source_ref IS NOT NULL;

CREATE TABLE variety (
  <id>
  coin_type_id      INTEGER NOT NULL REFERENCES coin_type(id) ON DELETE CASCADE,
  parent_variety_id INTEGER NULL REFERENCES variety(id) ON DELETE CASCADE,
  display_name TEXT NOT NULL,
  sort_order   INTEGER NOT NULL DEFAULT 0,
  source       TEXT NOT NULL DEFAULT 'manual',
  source_ref   TEXT NULL,
  <audit>
  <softdel>
);
CREATE INDEX ix_variety_type ON variety(coin_type_id, sort_order);
```

`coin_type` and `variety` carry almost no descriptive columns of their own. Everything a catalogue
says — denomination, ruler, mint, metal, legends, mintage, date span — is field values attached to
the type, using the same field system as specimens (`field_definition.entity = 'coin_type'`). The
alternative, a wide fixed `coin_type` table, would immediately fail on the first collector who
needs *calligraphic style* or *obverse control mark*.

`variety` is in the base schema although its UI arrives later. Retrofitting a middle layer between
type and specimen after thousands of specimens exist is a genuinely awful migration; an unused
empty table costs nothing.

```sql
CREATE TABLE specimen (
  <id>
  subcollection_id INTEGER NOT NULL REFERENCES subcollection(id) ON DELETE RESTRICT,
  coin_type_id     INTEGER NULL REFERENCES coin_type(id) ON DELETE SET NULL,
  variety_id       INTEGER NULL REFERENCES variety(id)   ON DELETE SET NULL,
  display_name     TEXT NOT NULL DEFAULT '',      -- rendered from naming_template
  inventory_code   TEXT NULL,                     -- the collector's own numbering
  status           TEXT NOT NULL DEFAULT 'owned'
                   CHECK (status IN ('owned','ordered','sold','traded','gifted',
                                     'lost','stolen','returned','on_loan','wanted')),
  quantity         INTEGER NOT NULL DEFAULT 1 CHECK (quantity >= 0),
  storage_location_id INTEGER NULL REFERENCES storage_location(id) ON DELETE SET NULL,
  primary_media_id    INTEGER NULL REFERENCES media_asset(id)      ON DELETE SET NULL,
  is_favourite     INTEGER NOT NULL DEFAULT 0,
  <audit>
  <softdel>
);
CREATE INDEX ix_specimen_sub    ON specimen(subcollection_id, status) WHERE deleted_at IS NULL;
CREATE INDEX ix_specimen_type   ON specimen(coin_type_id);
CREATE INDEX ix_specimen_name   ON specimen(display_name COLLATE NOCASE);
CREATE UNIQUE INDEX ux_specimen_inv ON specimen(inventory_code) WHERE inventory_code IS NOT NULL;
```

Four deliberate decisions:

1. **`coin_type_id` is nullable.** Requiring a type before recording a coin would make the app
   hostile to the most common real workflow: a coin arrives, gets photographed and entered in two
   minutes, and is attributed properly next weekend. A specimen may stand alone and be linked to a
   type — or have one generated from it — at any later point.
2. **`display_name` is stored, not computed on read.** It is regenerated from
   `subcollection.naming_template` whenever relevant field values change. Storing it keeps list
   views, label previews and album tiles fast, and gives a sensible fallback when no field carries
   the naming roles. It is also what makes it possible for *no field to be mandatory*.
3. **`status` is a cached projection of `specimen_event`** (Part 8), not an independent truth. It is
   duplicated here because "show me what I currently own" must be a single indexed predicate rather
   than a per-row scan of an event log.
4. **`quantity`** supports lots and bulk holdings ("47 wheat cents") without forcing 47 rows, while
   still allowing individually catalogued coins at `quantity = 1`.

### 3.1 The derived cache

```sql
CREATE TABLE specimen_cache (
  specimen_id INTEGER PRIMARY KEY REFERENCES specimen(id) ON DELETE CASCADE,
  date_sort_key   INTEGER NULL,
  year_start      INTEGER NULL,
  year_end        INTEGER NULL,
  country         TEXT NULL,
  authority       TEXT NULL,
  mint            TEXT NULL,
  denomination    TEXT NULL,
  metal           TEXT NULL,
  fineness_permille REAL NULL,
  weight_g        REAL NULL,
  diameter_mm     REAL NULL,
  grade_normalised REAL NULL,
  grade_display   TEXT NULL,
  primary_catalog_code TEXT NULL,
  primary_catalog_sort TEXT NULL,
  cost_basis_minor     INTEGER NULL,
  cost_basis_currency  TEXT NULL,
  latest_value_minor   INTEGER NULL,
  realised_pl_minor    INTEGER NULL,
  acquired_on     TEXT NULL,
  disposed_on     TEXT NULL,
  media_count     INTEGER NOT NULL DEFAULT 0,
  has_certification INTEGER NOT NULL DEFAULT 0,
  asw_troy_oz     REAL NULL,
  rebuilt_at      TEXT NOT NULL
);
CREATE INDEX ix_cache_date  ON specimen_cache(date_sort_key);
CREATE INDEX ix_cache_grade ON specimen_cache(grade_normalised);
CREATE INDEX ix_cache_metal ON specimen_cache(metal, fineness_permille);
```

This table resolves the tension between "everything is a user-defined field" and "sorting 50,000
coins by date must be instant". Each column is filled from whichever field currently holds the
corresponding **semantic role** (document 02) — so the user still owns their schema, while common
sorts, groupings and reports read one flat indexed row.

It is **entirely derived**. It may be dropped and rebuilt at any time, a maintenance action
("Rebuild indexes") exists for exactly that, and no feature may treat it as the only home of any
value. Keeping it in a separate table rather than as columns on `specimen` makes that guarantee
structural and obvious.

---

## Part 4 — Catalogs and catalog references

```sql
CREATE TABLE catalog (
  <id>
  code        TEXT NOT NULL UNIQUE,     -- 'KM', 'Y', 'H', 'RIC', 'N', 'FD', 'S'
  name        TEXT NOT NULL,            -- 'Standard Catalog of World Coins'
  publisher   TEXT, edition TEXT, year INTEGER,
  scope       TEXT,                     -- 'world', 'china', 'roman-imperial'
  url_template TEXT,                    -- 'https://en.numista.com/catalogue/pieces{number}.html'
  number_pattern TEXT,                  -- optional validation hint
  sort_strategy TEXT NOT NULL DEFAULT 'prefix_aware'
               CHECK (sort_strategy IN ('prefix_aware','numeric','lexical')),
  letter_prefix_order TEXT NOT NULL DEFAULT 'after'
               CHECK (letter_prefix_order IN ('after','before')),
  is_builtin  INTEGER NOT NULL DEFAULT 0,
  notes       TEXT,
  <audit>
);
```

Catalogs are **data, not code**. Shipping Krause, Yeoman, Hartill, Fisher's Ding, Schjöth, RIC,
Numista and the rest as seeded rows means a collector can add an obscure specialist reference
without waiting for a release.

```sql
CREATE TABLE catalog_reference (
  <id>
  catalog_id INTEGER NOT NULL REFERENCES catalog(id) ON DELETE RESTRICT,
  <subject>
  number_raw    TEXT NOT NULL,     -- exactly as entered: 'A54.2', '22.123', '1042a'
  number_norm   TEXT NOT NULL,     -- matching key: uppercased, '#'/spaces stripped
  sort_segments TEXT NOT NULL,     -- ordering/range key, see 4.1
  segments_json TEXT NOT NULL,     -- parsed structure, for range arithmetic
  qualifier  TEXT NULL,            -- 'var.', 'cf.', 'plate coin'
  certainty  TEXT NOT NULL DEFAULT 'certain'
             CHECK (certainty IN ('certain','probable','cf','disputed')),
  is_primary INTEGER NOT NULL DEFAULT 0,
  url        TEXT NULL,
  notes      TEXT NULL,
  <audit>
);
CREATE INDEX ix_catref_lookup ON catalog_reference(catalog_id, number_norm);
CREATE INDEX ix_catref_range  ON catalog_reference(catalog_id, sort_segments);
CREATE INDEX ix_catref_sp     ON catalog_reference(specimen_id)  WHERE specimen_id  IS NOT NULL;
CREATE INDEX ix_catref_ct     ON catalog_reference(coin_type_id) WHERE coin_type_id IS NOT NULL;
CREATE UNIQUE INDEX ux_catref_primary ON catalog_reference(specimen_id)
  WHERE specimen_id IS NOT NULL AND is_primary = 1;
```

A reference may attach to a type (the normal case — the catalogue describes the type), to a variety,
or directly to a specimen (for auction/pedigree references such as *ex Stack's 1974, lot 812*, and
for the common case of an unattributed specimen the owner has nonetheless identified).

### 4.1 Catalog number normalisation, sorting and ranges

Catalog numbers look numeric and are not. `2` must sort before `10`; `1042a` must follow `1042`;
and `A54` is not an entry in the fifties-thousands, it is a variant *of* 54 and belongs beside it.
Plain text sorting gets all three wrong: it yields `10, 1042, 1042a, 2, 2.1, 22.123, 22.9, 54, A54`.

Parsing extracts three things — an optional leading letter **prefix**, the **base number**, and any
remaining **segments**. The catalog code itself is stripped, because it lives in `catalog_id`:

```
'A54.2'   -> prefix 'a', base 54,   segments [2]
'54.2'    -> prefix '',  base 54,   segments [2]
'22.123'  -> prefix '',  base 22,   segments [123]
'1042a'   -> prefix '',  base 1042, segments ['a']
'KM# 2.1' -> prefix '',  base 2,    segments [1]
```

- `number_norm` — uppercased, punctuation-normalised form, used for exact matching and duplicate
  detection.
- `sort_segments` — a fixed-width text key: base number zero-padded to 8 digits, then the prefix
  padded to 4 characters, then each remaining segment (numbers zero-padded, text lowercased and
  padded), joined by `|`. Sorting on the base number *first* is what keeps letter-prefixed variants
  next to their base entry.
- `segments_json` — the parsed structure, so range endpoints can be computed and the wishlist engine
  (document 05) can express "any variety within `H 22.100`–`H 22.199`" without re-parsing text.

Verified output of this encoding:

| Number | `sort_segments` |
|---|---|
| `2` | `00000002\|    ` |
| `2.1` | `00000002\|    \|00000001` |
| `10` | `00000010\|    ` |
| `22.9` | `00000022\|    \|00000009` |
| `22.123` | `00000022\|    \|00000123` |
| `54` | `00000054\|    ` |
| `54.2` | `00000054\|    \|00000002` |
| `A54` | `00000054\|a   ` |
| `A54.2` | `00000054\|a   \|00000002` |
| `B54` | `00000054\|b   ` |
| `1042` | `00001042\|    ` |
| `1042a` | `00001042\|    \|a       ` |

`ORDER BY sort_segments` produces exactly that order, and a range such as *10 through 54.2* is an
indexed `sort_segments BETWEEN '00000010|    ' AND '00000054|    |00000002'` predicate returning
`10, 22.9, 22.123, 54, 54.2`.

`catalog.letter_prefix_order` chooses whether `A54` sorts after `54` (default, achieved by padding
an absent prefix with spaces) or before it (an absent prefix is padded with a high sentinel
instead), because catalogues are not consistent with each other about this. It is a per-catalog
setting rather than a global assumption.

Storing all three forms costs a few dozen bytes per row and removes an entire class of "why is my
catalogue sorted wrong" complaints.

---

## Part 5 — Certification

```sql
CREATE TABLE grading_company (
  <id>
  code TEXT NOT NULL UNIQUE,        -- 'NGC','PCGS','ANACS','ICG','GBCA','BAOCUI','PMG'
  name TEXT NOT NULL,
  cert_url_template TEXT,           -- verification lookup
  default_scale TEXT,
  specialism TEXT,                  -- 'world','chinese','paper'
  is_builtin INTEGER NOT NULL DEFAULT 0,
  notes TEXT,
  <audit>
);

CREATE TABLE certification (
  <id>
  specimen_id        INTEGER NOT NULL REFERENCES specimen(id) ON DELETE CASCADE,
  grading_company_id INTEGER NOT NULL REFERENCES grading_company(id) ON DELETE RESTRICT,
  cert_number  TEXT NOT NULL,
  scale        TEXT NULL,
  grade_numeric REAL NULL,
  grade_adjectival TEXT NULL,
  grade_normalised REAL NULL,
  designation  TEXT NULL,      -- '+', 'star', 'CAC', 'PL', 'DPL', 'FB'
  is_details   INTEGER NOT NULL DEFAULT 0,
  details_note TEXT NULL,      -- 'Cleaned', 'Environmental Damage'
  holder_type  TEXT NULL,      -- slab generation / holder style
  label_variety TEXT NULL,     -- 'first releases', 'old green holder'
  graded_on    TEXT NULL,
  population_note TEXT NULL,
  verification_url TEXT NULL,
  verified_at  TEXT NULL,
  is_current   INTEGER NOT NULL DEFAULT 1,
  supersedes_id INTEGER NULL REFERENCES certification(id) ON DELETE SET NULL,
  notes TEXT,
  <audit>
);
CREATE INDEX ix_cert_specimen ON certification(specimen_id, is_current);
CREATE INDEX ix_cert_number   ON certification(grading_company_id, cert_number);
CREATE UNIQUE INDEX ux_cert_current ON certification(specimen_id) WHERE is_current = 1;
```

Certification is its own table, not a field value, because a coin can be graded more than once:
crossovers, regrades and reholders are real events with their own numbers and dates, and collectors
care about that trail. `is_current` plus `supersedes_id` keeps the history while making "the current
grade" a single indexed lookup.

`(grading_company_id, cert_number)` is **indexed but not unique**, per 0.8: a typo or a genuine
duplicate must be flagged as a warning, never refused at save time.

Grading companies are seeded data for the same reason catalogs are — new TPGs appear regularly, and
adding one should not require a code change.

---

## Part 6 — Media

```sql
CREATE TABLE media_asset (
  <id>
  rel_path       TEXT NOT NULL,          -- relative to library media/ root
  thumb_rel_path TEXT NULL,
  preview_rel_path TEXT NULL,
  original_filename TEXT NULL,
  mime_type  TEXT NOT NULL,
  byte_size  INTEGER NOT NULL,
  width      INTEGER NULL,
  height     INTEGER NULL,
  sha256     TEXT NOT NULL,
  phash      TEXT NULL,                  -- perceptual hash, hex
  captured_at TEXT NULL,
  exif_json  TEXT NULL,
  scale_px_per_mm REAL NULL,             -- enables true-scale album rendering and measuring
  attribution TEXT NULL,
  licence     TEXT NULL,
  copyright_holder TEXT NULL,
  source      TEXT NOT NULL DEFAULT 'user',   -- user | numista | import:<name>
  source_url  TEXT NULL,
  imported_at TEXT NOT NULL,
  <audit>
  <softdel>
);
CREATE UNIQUE INDEX ux_media_sha ON media_asset(sha256);
CREATE INDEX ix_media_phash ON media_asset(phash);

CREATE TABLE media_link (
  <id>
  media_asset_id INTEGER NOT NULL REFERENCES media_asset(id) ON DELETE CASCADE,
  <subject>
  role TEXT NOT NULL DEFAULT 'obverse'
       CHECK (role IN ('obverse','reverse','edge','slab','detail','overlay',
                       'certificate','invoice','provenance','other')),
  sort_order INTEGER NOT NULL DEFAULT 0,
  crop_json  TEXT NULL,          -- {"x":..,"y":..,"w":..,"h":..} fractions of original
  rotation   REAL NOT NULL DEFAULT 0,
  flip       TEXT NULL CHECK (flip IN (NULL,'h','v','hv')),
  adjust_json TEXT NULL,         -- brightness/contrast/white balance, non-destructive
  background_removed INTEGER NOT NULL DEFAULT 0,
  caption    TEXT NULL,
  <audit>
);
CREATE INDEX ix_medialink_sp ON media_link(specimen_id, role, sort_order) WHERE specimen_id IS NOT NULL;
CREATE INDEX ix_medialink_asset ON media_link(media_asset_id);
```

Key properties:

- **Originals are immutable.** Every edit — crop, rotation, flip, colour adjustment, background
  removal — is metadata on the *link*, applied at render time. A scan made once can never be
  degraded by later cropping, and the same photograph can be cropped differently in two contexts.
- **`sha256` is unique**, which makes re-importing the same file idempotent: the existing asset is
  reused and only a new link is created. `phash` catches *near* duplicates (re-saved, resized or
  slightly recompressed copies) and raises an import warning rather than silently merging.
- **Assets and links are separate** so one image can serve several subjects — a shared reference
  photograph on a type, a group shot covering several specimens.
- **`scale_px_per_mm`** is worth the one column: once a photograph's scale is known, the album can
  render coins at true relative size and the user can measure a coin from its image.
- **Licence and attribution are first-class**, because images will arrive from external catalogues
  whose terms require attribution, and mixing those with the user's own photographs without
  recording which is which would be a genuine legal problem for redistribution or publication.

---

## Part 7 — Storage locations

```sql
CREATE TABLE storage_location (
  <id>
  parent_id INTEGER NULL REFERENCES storage_location(id) ON DELETE CASCADE,
  name TEXT NOT NULL,
  kind TEXT NOT NULL DEFAULT 'other'
       CHECK (kind IN ('building','room','safe','deposit_box','cabinet','drawer','box',
                       'tray','album','page','pocket','envelope','tube','slab_box','other')),
  path_cache TEXT NOT NULL,        -- 'Safe / Box 3 / Tray 2' for display and search
  position   INTEGER NULL,         -- ordinal within parent
  capacity   INTEGER NULL,
  notes TEXT,
  sort_order INTEGER NOT NULL DEFAULT 0,
  <audit>
);
CREATE INDEX ix_storage_parent ON storage_location(parent_id, sort_order);
CREATE INDEX ix_storage_path   ON storage_location(path_cache COLLATE NOCASE);
```

A self-referencing hierarchy handles Safe → Box 3 → Tray 2 → Slot 14 to any depth. `path_cache` is
denormalised so a table view can show a full location without recursive queries, and is rebuilt
when a node moves.

This table is the anchor point for the virtual album (document 04): album pages and pockets are
storage locations with `kind IN ('album','page','pocket')`, which is what allows the on-screen
album to mirror the physical collection rather than being an unrelated decorative feature.

---

## Part 8 — History, money and provenance

```sql
CREATE TABLE specimen_event (
  <id>
  specimen_id INTEGER NOT NULL REFERENCES specimen(id) ON DELETE CASCADE,
  event_type TEXT NOT NULL CHECK (event_type IN (
      'acquired','ordered','received','sold','listed','traded_in','traded_out',
      'gifted_in','gifted_out','valued','graded_sent','graded_returned',
      'moved','conserved','lost','stolen','found','returned','loaned','note')),
  occurred_on TEXT NULL,               -- ISO date; NULL when genuinely unknown
  occurred_precision TEXT NOT NULL DEFAULT 'exact_day'
      CHECK (occurred_precision IN ('exact_day','exact_month','exact_year','circa','unknown')),
  quantity INTEGER NOT NULL DEFAULT 1,
  amount_minor    INTEGER NULL,        -- the headline price
  currency        TEXT NULL,
  fees_minor      INTEGER NULL,        -- buyer's premium, commission
  shipping_minor  INTEGER NULL,
  tax_minor       INTEGER NULL,
  fx_rate_to_base REAL NULL,
  base_total_minor INTEGER NULL,       -- derived: all components, converted
  counterparty      TEXT NULL,
  counterparty_kind TEXT NULL CHECK (counterparty_kind IN
      (NULL,'dealer','auction','private','show','mint','online','grading_service','other')),
  venue TEXT NULL,
  lot_reference     TEXT NULL,
  invoice_reference TEXT NULL,
  from_location_id INTEGER NULL REFERENCES storage_location(id) ON DELETE SET NULL,
  to_location_id   INTEGER NULL REFERENCES storage_location(id) ON DELETE SET NULL,
  linked_specimen_id INTEGER NULL REFERENCES specimen(id) ON DELETE SET NULL,  -- trades
  media_asset_id   INTEGER NULL REFERENCES media_asset(id) ON DELETE SET NULL, -- invoice scan
  valuation_source TEXT NULL,          -- 'catalogue', 'dealer quote', 'comparable sale'
  notes TEXT,
  is_void    INTEGER NOT NULL DEFAULT 0,
  void_reason TEXT NULL,
  voided_at  TEXT NULL,
  corrects_event_id INTEGER NULL REFERENCES specimen_event(id) ON DELETE SET NULL,
  created_at TEXT NOT NULL
);
CREATE INDEX ix_event_specimen ON specimen_event(specimen_id, occurred_on);
CREATE INDEX ix_event_type     ON specimen_event(event_type, occurred_on) WHERE is_void = 0;
```

The ledger is **append-only**. Rows are never updated except to set `is_void`, and a mistake is
corrected by voiding the original and inserting a replacement that points back via
`corrects_event_id`. There is no `updated_at`, because nothing is updated. This is why the
financial history of a collection assembled over decades can be trusted: it cannot be quietly
rewritten, and every correction is visible as a correction.

Everything monetary is derived from this ledger rather than stored as an editable number:

- **Cost basis** = the acquisition event's total, in base currency.
- **Realised profit or loss** = disposal total − cost basis, which directly answers "bought at X,
  sold at Y".
- **Unrealised value** = latest `valued` event (or a `VALUE_ESTIMATE` role field) − cost basis.
- **Current status** = projected from the latest non-void status-changing event, cached in
  `specimen.status`.

Sold and traded coins therefore stay in the database permanently with their full story, excluded
from "currently owned" views by one predicate but present in every report — a collection modelled
over time rather than as a snapshot.

```sql
CREATE TABLE provenance_entry (
  <id>
  specimen_id INTEGER NOT NULL REFERENCES specimen(id) ON DELETE CASCADE,
  sequence   INTEGER NOT NULL,          -- 1 = earliest known owner
  owner_name TEXT NOT NULL,             -- 'Eliasberg', 'Stack's', 'private collection, Vienna'
  owner_kind TEXT NULL,
  from_year  INTEGER NULL,
  to_year    INTEGER NULL,
  reference  TEXT NULL,                 -- sale, lot, plate, publication
  is_verified INTEGER NOT NULL DEFAULT 0,
  notes TEXT,
  <audit>
);
CREATE UNIQUE INDEX ux_prov_seq ON provenance_entry(specimen_id, sequence);
```

Pedigree is separate from the purchase ledger because a chain of previous owners is a scholarly
claim with citations and varying confidence, not a transaction the user took part in. For ancient
and rare coins it can matter more than the price.

```sql
CREATE TABLE specimen_link (
  <id>
  from_specimen_id INTEGER NOT NULL REFERENCES specimen(id) ON DELETE CASCADE,
  to_specimen_id   INTEGER NOT NULL REFERENCES specimen(id) ON DELETE CASCADE,
  kind TEXT NOT NULL CHECK (kind IN ('duplicate_of','upgraded_by','part_of_set','same_dies',
                                     'mule_pair','before_conservation','related')),
  notes TEXT,
  <audit>
  CHECK (from_specimen_id <> to_specimen_id)
);
CREATE UNIQUE INDEX ux_speclink ON specimen_link(from_specimen_id, to_specimen_id, kind);
```

Cheap to include now, and it covers several things collectors ask for constantly: sets, die-linked
pairs, and "this coin replaced that one" upgrade chains.

---

## Part 9 — Tags and saved views

```sql
CREATE TABLE tag (
  <id>
  parent_id INTEGER NULL REFERENCES tag(id) ON DELETE CASCADE,
  name TEXT NOT NULL,
  colour TEXT NULL,
  notes TEXT,
  <audit>
);
CREATE UNIQUE INDEX ux_tag_name ON tag(COALESCE(parent_id,0), name COLLATE NOCASE);

CREATE TABLE specimen_tag (
  specimen_id INTEGER NOT NULL REFERENCES specimen(id) ON DELETE CASCADE,
  tag_id      INTEGER NOT NULL REFERENCES tag(id) ON DELETE CASCADE,
  PRIMARY KEY (specimen_id, tag_id)
) WITHOUT ROWID;
CREATE INDEX ix_specimentag_tag ON specimen_tag(tag_id);

CREATE TABLE saved_view (
  <id>
  name TEXT NOT NULL,
  subcollection_id INTEGER NULL REFERENCES subcollection(id) ON DELETE CASCADE,
  scope TEXT NOT NULL DEFAULT 'specimen',
  filter_json  TEXT NOT NULL DEFAULT '{}',   -- filter tree, document 07
  sort_json    TEXT NOT NULL DEFAULT '[]',
  columns_json TEXT NOT NULL DEFAULT '[]',
  group_by     TEXT NULL,
  is_smart     INTEGER NOT NULL DEFAULT 1,
  sort_order   INTEGER NOT NULL DEFAULT 0,
  <audit>
);
```

Tags are the cross-cutting counterpart to subcollections: a coin lives in one subcollection but can
carry any number of tags, which is how "everything silver, across all subcollections" is expressed
without weakening the home-subcollection rule.

`saved_view` is included in the base because it costs one table and makes the app immediately more
useful; the filter grammar it stores is specified in document 07.

---

## Part 10 — Search

```sql
CREATE TABLE specimen_search (
  specimen_id INTEGER PRIMARY KEY REFERENCES specimen(id) ON DELETE CASCADE,
  title_blob   TEXT NOT NULL DEFAULT '',
  text_blob    TEXT NOT NULL DEFAULT '',   -- all text/long_text/option/lookup values, as entered
  catalog_blob TEXT NOT NULL DEFAULT '',   -- 'KM 2.1 KM#2.1 H 22.123 ...'
  note_blob    TEXT NOT NULL DEFAULT '',
  cjk_blob     TEXT NOT NULL DEFAULT '',   -- CJK content, one character per token
  rebuilt_at   TEXT NOT NULL
);

CREATE VIRTUAL TABLE specimen_fts USING fts5(
  title_blob, text_blob, catalog_blob, note_blob, cjk_blob,
  content = 'specimen_search',
  content_rowid = 'specimen_id',
  tokenize = "unicode61 remove_diacritics 2"
);
```

One FTS table, plus a pre-segmented CJK column. That design is the result of testing the
alternatives rather than assuming, because the obvious approaches both fail:

- `unicode61` splits on whitespace, so an entire CJK legend becomes **one token**. Indexing
  `乾隆通寶 寶泉` produces the tokens `乾隆通寶` and `寶泉`. Searching `乾隆通寶` matches; searching
  `通寶` — one of the most common terms in all of Chinese numismatics — matches **nothing**, because
  it is an infix of a single token. Prefix search (`乾隆*`) works; infix search does not.
- The `trigram` tokenizer handles infixes but only for sequences of **three or more characters**.
  `乾隆通` matches, while the two-character terms `通寶`, `乾隆`, `當十` and every single-character
  search match nothing. It also does not fold diacritics, so `Gunzburg` stops matching `Günzburg` —
  a real regression for European legends.

The solution is to have the application write a second copy of all CJK content into `cjk_blob` with
every ideograph space-separated (`乾隆通寶 寶泉` → `乾 隆 通 寶 寶 泉`). `unicode61` then indexes each
character as its own token, and any sequence of characters becomes an ordinary phrase query:

| User searches | Query issued | Matches |
|---|---|---|
| `通寶` | `cjk_blob : "通 寶"` | yes |
| `乾隆` | `cjk_blob : "乾 隆"` | yes |
| `當十` | `cjk_blob : "當 十"` | yes |
| `寶` | `cjk_blob : "寶"` | yes, every coin containing it |
| `乾隆通寶` | `cjk_blob : "乾 隆 通 寶"` | yes |

Latin, Greek and Cyrillic searches go to `text_blob` with diacritic folding and prefix matching
intact. The query builder detects CJK characters in the search string and routes accordingly, or
unions both when a query mixes scripts.

This costs one extra text column and gives correct, complete CJK search with no additional index,
which matters because Chinese cash coinage is a first-class target for this project and not an
afterthought. If Latin *infix* search (matching `Theres` inside `Theresia`) is later judged
important, a supplementary `trigram` index over `text_blob` can be added; it is deliberately not in
the base, since prefix search covers the common case.

The FTS table uses external content over `specimen_search`, so searchable text is materialised once
by the application on save and the index can be dropped and rebuilt at any time without data loss.

---

## Part 11 — Invariants

Enforced by the database:

1. Exactly one subject per `field_value_*`, `catalog_reference`, `media_link` row.
2. One field value per `(field_definition, subject, seq)`; non-multi fields only ever use `seq = 0`.
3. One field per semantic role per scope and entity.
4. At most one `is_current` certification per specimen; at most one primary catalog reference per
   specimen.
5. `media_asset.sha256` unique.
6. `specimen.subcollection_id` never null; deleting a subcollection with specimens is refused.
7. `specimen_link` cannot link a specimen to itself.

Enforced by the application, and verified by a Library Health report rather than blocked at entry:

8. `field_value_option.field_option_id` belongs to the same field definition as the value row.
9. A value's storage table matches its definition's `data_type`.
10. `specimen.status` agrees with the latest non-void event; `specimen_cache` is not stale.
11. Every `media_asset.rel_path` exists on disk, and every file under `media/` is referenced.
12. Duplicate certification numbers, implausible weights and diameters, catalog references whose
    format contradicts their catalog's pattern — reported, never rejected.

---

## Part 12 — Deliberately not in the base

Excluded, with the seams they will attach to:

| Later section | Attaches to |
|---|---|
| Holder templates, label layouts, label instances (03) | `specimen`, `field_definition.key`, `storage_location`, `media_asset` for cached previews |
| Virtual albums (04) | `storage_location` (`album`/`page`/`pocket` kinds), and `label_instance` from 03 so an album shows the label exactly as printed |
| Wishlist slots (05) | `catalog_reference.segments_json` for range criteria, `field_definition.role` for attribute criteria, `specimen` for matching |
| Import and export (06) | `coin_type.source`/`source_ref`, `media_asset.source`, `field_definition.key` for column mapping |
| Filter grammar (07) | `saved_view.filter_json`, the typed value tables, `specimen_cache` |
| Edit-level audit history | a future `change_log` table; deliberately not the same thing as `specimen_event` |

Two schema hooks are included now purely so those sections do not require painful migrations:
`variety` (an unused middle layer is far cheaper than inserting one later) and `subcollection`
scoping on field definitions.

---

## Part 13 — Decisions still needed

1. **Field values on types.** The schema allows fields on `coin_type` and `variety`. Should the
   first build expose that, or keep all data entry at specimen level until the Numista importer
   makes types genuinely useful?
2. **Quantity versus individual rows.** `specimen.quantity` supports bulk lots. Should bulk lots
   also be excluded from statistics such as average grade by default?
3. **Base currency changes.** If the user changes `base_currency` later, should stored
   `base_amount_minor` values be recomputed (requires historical rates) or left as recorded with the
   original rate? Leaving them is honest and simpler; recomputing is what most people expect.
4. **Trash retention.** How long do soft-deleted specimens stay before automatic purge, if ever?
5. **Grade scale coverage.** Which scales must be in the first release beyond Sheldon and US/UK
   adjectival — European `B/TB/SUP/FDC`, Japanese, Chinese market conventions?
6. **Seeded catalogs.** Which catalogs and grading companies ship as built-in rows in v1?
