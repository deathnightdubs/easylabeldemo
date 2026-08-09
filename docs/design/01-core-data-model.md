# 01 — Core data model

Status: **Proposed — awaiting approval**
Normative schema: [`schema/base-v1.sql`](schema/base-v1.sql) — verified against SQLite 3.40.
Plain-English companion: [`OVERVIEW.md`](OVERVIEW.md)

This document explains and justifies the base schema. It does not restate the DDL; the `.sql` file
is the single source of truth so the two cannot drift apart.

---

## Part 0 — What changed from the first draft

Recorded so the revisions are easy to absorb.

| Change | Reason |
|---|---|
| **Coin types and varieties removed entirely** | One row is one specimen. The software has no concept of a "type". Type-like grouping is expressed later by the wishlist as a set of field criteria, which is where it is actually needed. |
| **Semantic roles removed** | Replaced by `feature_binding`: the user tells each feature which field to use. Nothing is inferred. See document 02. |
| **Field definitions are library-wide** | A subcollection opts in via `subcollection_block` and may give the field its own display label. Sharing one field is what merges columns in the master view. |
| **Multi-currency removed** | One currency per library. No FX rates, no per-value currency codes. |
| **Media and physical storage locations deferred** | Both arrive with virtual albums. Nothing in the base depends on them. |
| **`specimen_cache` removed** | Sorting is served by indexed `sort_value` columns instead, which removes a whole class of stale-derived-state bugs. |
| **Sort key companion added** | Your two-column spreadsheet trick (a display date plus a numeric date), integrated into one field. |
| **Catalogues, grades, certifications and links are separate systems** | Not ordinary user fields, because each is multi-valued with its own structure and sorting rules. |
| **Grading is fully user-defined data** | Scales, levels and modifiers are rows, not code. The app ships with none. |
| **Soft deletion retained indefinitely** | No automatic purge until you ask for one. |
| **`net_minor` replaces `total_minor`** | A bug found in testing: the original summed fees in both directions, overstating every profit figure. |
| **`quantity` removed from `specimen`** | One row is one coin. Bulk add creates 47 rows; totals stay a plain row count. |
| **The `lookup` and `category` field types removed** | Remembered-entry vocabularies and fixed lists are both dropped for now. Every field is plain text. |
| **Registries ship completely empty** | No seed catalogues, grading companies, scales or modifiers in test builds. |

---

## Part 1 — Conventions

### 1.1 The library is a folder

```
MyCollection.numis/
├── collection.db      SQLite database
├── backups/           timestamped copies, made before every migration
├── presets/           installed presets
└── exports/           PDFs, CSVs, JSON the user asked for
```

`media/` joins this later with virtual albums. One library is open at a time; the recent-library
list lives in the OS user-config directory, never inside a library.

### 1.2 Identity

Every entity has `id INTEGER PRIMARY KEY` for joins and an application-supplied `uuid` for
identity that survives leaving the database — preset sharing, re-importing an export without
creating duplicates, merging two libraries. Retrofitting UUIDs later is a miserable migration, so
they are in the base.

### 1.3 Time

UTC ISO-8601 text with milliseconds, maintained by the application rather than SQL triggers so bulk
imports behave identically and the logic is testable. Text keeps the file readable in any SQLite
browser, which matters for a tool people trust with irreplaceable data.

Dates that describe coins are a completely different thing and never use these columns; see 1.6.

### 1.4 Money: one currency, integers only

`library_meta` holds a single `currency_symbol`, `currency_code` and `currency_decimals`. Every
monetary value is an `INTEGER` in minor units. There are no per-value currency codes and no
exchange rates.

Integers rather than floats because `0.1 + 0.2 != 0.3` is a curiosity in a physics calculation and
unacceptable in a ledger that accumulates thousands of transactions over decades.

If multi-currency is ever wanted, the migration is additive — a nullable `currency` column
defaulting to the library currency — so nothing here blocks it.

### 1.5 Measurement units

| Quantity | Stored as | Displayed as |
|---|---|---|
| Mass | `REAL` grams | g, mg, grains, troy oz, dwt |
| Length | `REAL` millimetres | mm, inches |
| Fineness | `REAL` parts per thousand (0–1000) | 0.900, 900, 90%, 22K |
| Angle | `REAL` degrees | degrees or clock hours |

A different rule from money, deliberately. `REAL` is a 64-bit double, so `27.153 g` carries an error
around 1e-15 g — fifteen orders of magnitude below any coin scale. Money arithmetic accumulates and
must be exact; a weight does not. Equality on measurements always uses a tolerance, never `=`.

Display units are a per-field or per-library preference. Stored values never change when the user
switches display units, so switching is free and reversible.

### 1.6 Dates that describe coins

A `DATE` column cannot hold `1736–1795`, `c. 350 BC`, `AH 1256`, `Qianlong year 22` or `undated`.
`field_value_date` therefore stores three things at once:

1. **`display`** — exactly what the user typed. Never regenerated over the top of their input.
2. **`year_start` / `year_end`** plus `precision`, `calendar` and `era_label` — the normalised span,
   signed so negatives are BC (historical convention, no year zero). This powers "minted between X
   and Y" as an indexed integer range even when the underlying value is a reign or an approximation.
3. **`sort_value`** — the numeric ordering key described in Part 4.

Custom calendar systems are deferred, but `calendar` plus a parser registry is the seam they will
attach to, so adding "define your own era" later is additive.

### 1.7 Deletion

`specimen` uses `deleted_at` and is retained **indefinitely**; deleted coins are visible in a Trash
view and restorable. No automatic purge exists until you ask for one. Purging is explicit and
cascades to that specimen's values, references, grades, certifications, links and events.

Child rows cascade. Registry rows that data points at — `catalog`, `grading_company`, `grade_level`
— use `RESTRICT` or `SET NULL`: deleting a catalogue must never silently destroy every reference
recorded against it.

### 1.8 Validation warns, it does not block

The database enforces only structural integrity: foreign keys, enumerated statuses, one primary per
specimen. It deliberately does not enforce judgements like "certification numbers are unique" or
"weight must be under 100 g". A collector entering a real coin at 11pm must never be stopped by
software that thinks it knows their collection better than they do.

Those become warnings in the UI and entries in a Library Health report that lists suspicious data
without altering it. The one exception is a change that would silently corrupt meaning — for
example binding a text field to a feature that needs a number — which is refused with an
explanation.

### 1.9 Connection settings and migrations

`WAL`, `foreign_keys = ON`, `busy_timeout = 5000`, `synchronous = NORMAL`. `foreign_keys` is off by
default in SQLite and is set per connection in a single SQLAlchemy event handler, because forgetting
it silently disables every `REFERENCES` clause in the schema.

Alembic owns migrations; `library_meta.schema_version` records the revision. A timestamped backup is
copied into `backups/` before any migration runs. Opening a library newer than the running
application is refused rather than attempted.

---

## Part 2 — Subcollections and the master view

A subcollection is the "separate spreadsheet tab" concept: Modern, Ancients, Chinese Cash, Tokens.

- A specimen belongs to exactly one subcollection (`specimen.subcollection_id NOT NULL`).
- Field definitions are **library-wide**. `subcollection_block` records which fields (and which
  special blocks) a subcollection shows, in what order, in which group, under what label.
- `subcollection_block.display_label` overrides the field's canonical label for that subcollection
  only.

This delivers the behaviour you described, in both directions:

**One field, different names per subcollection.** A single field `head_of_state` is shown as *Ruler*
in Modern and *Emperor* in Ancients. In the master view it is one column, *Head of state*, because
it is literally the same field. No role matching, no name matching, no heuristics — the merge is a
consequence of identity. Verified:

```
in 'Ancients' shown as 'Emperor'   master column 'Head of state'  (key head_of_state)
in 'Modern'   shown as 'Ruler'     master column 'Head of state'  (key head_of_state)
```

**Different fields that should stay separate** simply are separate definitions and appear as their
own columns, each under its own canonical label.

Consequences worth stating plainly:

- Two subcollections can have entirely different field sets, and the master view is the union:
  shared fields line up, unshared fields appear as extra columns that are blank for rows from the
  other subcollection.
- Removing a field from a subcollection is removing a `subcollection_block` row. The definition and
  every value survive, and re-adding it brings the data straight back.
- `naming_template` renders `specimen.display_name` from field keys, which is why no individual
  field ever has to be mandatory.

---

## Part 3 — Specimens

One row per specimen. There is no coin-type entity, no variety entity, and no inheritance. What the
software knows about a coin is: which subcollection it is in, a display name, an optional inventory
code, a status, and whatever field values, catalogue references, grades, certifications,
links and events are attached to it.

This is a real simplification with real consequences, stated honestly:

- Shared data is not entered once and inherited; twelve specimens of the same issue each carry their
  own values. **Bulk add and bulk edit are therefore not conveniences, they are the feature that
  replaces inheritance**, and they must be present from the first build rather than added later.
- Numista import creates specimens directly, with the user mapping Numista's fields onto their own.
  It is not a type catalogue that specimens then point at.
- The wishlist does not need a type table: a slot is a set of field criteria, and "these catalogue
  numbers all satisfy this slot" is expressed there. That is the only place a type-like concept was
  ever needed, and it is cheaper to express as criteria than as a table.

### Bulk add and bulk edit

**One row is exactly one coin.** There is no `quantity` column. A lot of 47 identical coins is 47
rows, and collection totals are therefore always a plain row count with no special cases anywhere in
reporting. Per-row lots may be revisited later; nothing in the schema prevents adding a nullable
`quantity` column when they are.

**Bulk add** creates *n* separate rows from one filled-in form. This is the primary way shared data
gets entered, since there is no type layer to inherit from.

**Bulk edit** applies a change to every selected row — set a field, add a tag, move subcollection,
add a catalogue reference — as one undoable operation.

Both are first-build features, not later conveniences.

### Status

`status` is a small enumeration cached on the row (`owned`, `sold`, `wanted`, and so on) so
"what do I currently own" is one indexed predicate. It is a projection of the event ledger, which
remains the truth (Part 5). Sold coins stay in the database permanently with their history and are
excluded from current-holdings views by that one predicate.

---

## Part 4 — Field storage and the sort key companion

Document 02 specifies the field types themselves. Two things belong here because they are storage
decisions.

### 4.1 Typed value tables

Values live in six tables by storage shape — text, number, money, date, bool, json —
rather than one stringly-typed table. The reason is blunt: a single text column cannot sort `9`
before `10`, cannot answer "weight between 5 g and 8 g", and cannot answer "minted between 1850 and
1875". Those three queries are most of what a collection manager does.

Each row is keyed by `(field_definition_id, specimen_id, seq)`, with `seq` carrying ordinal position
for multi-valued fields.

### 4.2 The sort key companion

Your spreadsheet used two columns — a display date and a numeric date — so that clicking *sort*
worked even though the display format was inconsistent. That trick is correct, and the schema makes
it one field instead of two columns the user has to maintain in parallel.

`field_value_text` and `field_value_date` each carry:

| Column | Meaning |
|---|---|
| `sort_value REAL NULL` | the numeric ordering key |
| `sort_source` | `auto` (the app worked it out), `manual` (the user set it), `none` |
| `needs_review` | the app produced a value or a guess it wants confirmed |

Behaviour on entry, verified against the schema:

```
as typed               sort   source   precision    prompt user?
1943                 1943.0   auto     exact_year   no
1736-1795            1765.5   auto     range        YES
c. 350 BC            -350.0   auto     circa        no
AH 1256              1840.6   auto     exact_year   YES
Qianlong year 22          -   none     unknown      YES
undated                   -   none     unknown      no
1804                 1804.0   auto     exact_year   no
```

- A plain number is used as-is, silently. No prompt, because there is nothing to confirm.
- A recognised range produces the midpoint and **says so**, inviting confirmation rather than
  assuming. `1736–1795` sorts at 1765.5, between 1700 and 1800, instead of at either extreme.
- A recognised era converts and flags itself. `AH 1256` → 1840.6.
- Anything unrecognised stores the text, sorts nowhere, and asks the user for a number.
- The user can override any sort value at any time, and a manual value is never overwritten by the
  parser.

Rows with no sort value sort last, by explicit `ORDER BY sort_value IS NULL, sort_value`.

The same mechanism solves denominations, which was the other case where display and order disagree.
Free text the user invents — `wen`, `cash`, `mace`, `tael` — orders correctly once each value has a
number, whether the app guessed it or the user typed it:

```
1 wen             1  auto
10 wen           10  auto
50 wen           50  auto
100 cash        100  auto
1 mace         1000  manual
half tael     18650  manual
```

Every field is plain text for now, so the sort key is the only ordering mechanism. If fixed lists
return later, an ordered list of choices would give the same capability without a per-row sort value.

The principle: **the app proposes, the user disposes, and the app always says which happened.** This
is the only place anything resembling guessing exists, and it is always visible and always editable.

---

## Part 5 — History and money

`specimen_event` is an append-only ledger. Rows are never updated except to set `is_void`; a mistake
is corrected by voiding the original and inserting a replacement that points back via
`corrects_event_id`. There is no `updated_at`, because nothing is updated. That is what makes the
financial history of a collection assembled over decades trustworthy: it cannot be quietly
rewritten, and every correction is visible as a correction.

Event types cover acquisition, disposal, valuation, grading submissions, movement and plain notes.

### `net_minor` and why it exists

Each event records `amount_minor` plus optional `fees_minor` and `shipping_minor`. `net_minor` is a
stored generated column holding the cash that actually moved:

```
acquired   amount=12500  fees=1875  ship=850  ->  net=15225   total cost paid
sold       amount=31000  fees=3100  ship=  0  ->  net=27900   net proceeds received
```

Fees and postage **add** to a purchase and **subtract** from a sale. My first draft summed them in
both directions; in the test above that reported a profit of 18875 instead of 12675, overstating it
by 6200. The sign has to depend on the event type, so the generated column uses a `CASE` on
`event_type`.

Everything monetary is derived from this ledger rather than stored as an editable number:

- cost basis = the acquisition event's `net_minor`
- realised profit or loss = disposal `net_minor` − acquisition `net_minor`
- unrealised value = latest valuation − cost basis
- current status = projected from the latest non-void status-changing event

---

## Part 6 — Tags, saved views and search

**Tags** are the cross-cutting counterpart to subcollections: a coin lives in one subcollection but
carries any number of tags, so "everything silver, across every subcollection" is expressible
without weakening the one-home rule. Tags are hierarchical.

**`saved_view`** stores a named filter tree, sort list, column list and grouping. Document 03
specifies the grammar; the table is in the base because it costs one table and every later feature
reads it.

**Search** uses one FTS5 index over a materialised `specimen_search` row per specimen. The design
was corrected by testing:

- `unicode61` makes an entire CJK legend **one token**. Searching `通寶` inside `乾隆通寶` matched
  nothing.
- The `trigram` tokenizer only handles sequences of three or more characters, so `通寶`, `乾隆` and
  `當十` all failed — and it stopped folding diacritics, so `Gunzburg` no longer matched `Günzburg`.

The fix is a `cjk_blob` column into which the application writes CJK content with every ideograph
space-separated (`乾隆通寶` → `乾 隆 通 寶`). Each character becomes its own token, so any sequence is
an ordinary phrase query, verified for one-, two- and four-character searches. Latin, Greek and
Cyrillic go to `text_blob` with diacritic folding and prefix matching intact.

---

## Part 7 — Deliberately deferred

| Deferred | Arrives with | Seam it will attach to |
|---|---|---|
| Photographs and thumbnails | virtual albums | new `media_asset` / `media_link` tables; nothing in the base references them |
| Hierarchical physical storage locations | virtual albums | a `storage_location` table; until then a plain user field records where a coin is |
| Provenance and pedigree chains | later | a `provenance_entry` table keyed by specimen |
| Edit-level audit history | later | a `change_log` table; distinct from `specimen_event`, which is about ownership not edits |
| Multi-currency | only if wanted | nullable `currency` column defaulting to the library currency |
| Coin types as first-class records | probably never | if ever needed, a nullable `coin_type_id` on `specimen` |

The deferrals are all additive: each adds tables or nullable columns and changes nothing that
already exists. That is the test I applied when deciding what could safely wait.

---

## Part 8 — Decisions

### Resolved

| Question | Answer |
|---|---|
| Bulk lots in one row | Not for now. One row is one coin; `quantity` removed. Bulk add and bulk edit cover the need |
| Seed data | Test builds ship a completely blank slate |
| Soft delete retention | Indefinite; no automatic purge |
| Inventory codes | **Assigned automatically** as the lowest unused whole number, and freely editable. Unique across the library. A code held by a coin in the Trash may be reused **after confirmation**; that coin is then given a fresh code if it is ever restored, since its data matters more than its number |
| Naming template | Kept, but only as a *default*. `display_name` is editable, and `display_name_manual` records which it is: an automatic name follows the template as values are filled in, a typed one is never touched. Clearing the name returns it to the template |
| Coins that have left the collection | `status` is directly editable and also derived when a ledger entry is added. Disposed coins (sold, traded, gifted, lost, stolen) stay in the database with their full history and are simply not listed unless asked for — separate from the Trash |
| Changing subcollection | A coin's subcollection is editable — by typing in the Subcollection column, or with **Move to…** for several at once. Values are kept even when the destination does not show that field |

### Still open

1. **Membership in several subcollections at once.** A coin currently has exactly one home
   subcollection, which is now easy to change.

   The requested case is a coin that genuinely belongs in two — a modern Chinese 1 fen sitting in
   both *China* and *Modern*. That is worth separating into two different jobs a subcollection is
   currently doing at once:

   * **defining the schema and layout** — which fields exist, what they are called, in what order.
     This has to be singular, or there is no answer to which layout applies.
   * **deciding which coins are seen together** — naturally many-to-many.

   The second job is better done by **saved views** (document 03) than by membership. A *China*
   view is `country = China`; a *Modern* view is `date ≥ 1900`. The 1 fen appears in both with no
   bookkeeping, and stays correct when the coin is edited — whereas manual membership has to be
   maintained by hand and silently goes stale. Tags cover the cases no rule can express
   ("inherited from my grandfather").

   So the recommendation is to build document 03 first and see whether the need survives. If it
   does, a `specimen_subcollection` join table keeping today's column as the primary is the way,
   and the three questions to answer first are: which template names such a coin, whether the
   master view lists it once or once per membership, and what removing one membership means.
2. **Per-subcollection numbering.** Identifiers are unique library-wide, so one subcollection's
   codes are not contiguous. Should numbering optionally restart per subcollection, at the cost of
   a code no longer identifying a coin on its own?
3. **Status vocabulary.** Fixed in the schema. Is the current list right, or should users add their
   own statuses?
4. **Master view scope.** Does it show every subcollection by default, or is it assembled by picking
   which subcollections to combine?
