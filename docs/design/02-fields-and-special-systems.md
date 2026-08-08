# 02 — Fields and special systems

Status: **Proposed — awaiting approval**
Depends on: [01 — Core data model](01-core-data-model.md)
Normative schema: [`schema/base-v1.sql`](schema/base-v1.sql)

Replaces the earlier *Field types and semantic roles*. Semantic roles are gone; features are told
which fields to use.

---

## Part 1 — Ordinary fields

### 1.1 The registry

Each field type is one entry in `core/fields/registry.py`:

```
key               'weight'
label             'Weight'
storage           field_value_number
canonical_unit    'g'
config_schema     declared options with defaults
supports_multi    whether is_multi is allowed
parse(raw, cfg)       user text -> canonical stored value (raises FieldParseError)
format(value, cfg)    canonical value -> display string
sort_expression()     SQL used for ORDER BY
filter_operators      operator set offered in the filter builder
propose_sort_value()  optional numeric ordering key (see 1.3)
convert_from(type, v) value migration when a field's type changes
to_export/from_export lossless JSON round-trip
```

Editor widgets live in `ui/fields/editors.py`, registered against the same `key`. `core/` never
imports Qt, so field behaviour is unit-testable and reusable from the CLI. Adding a field type is one
registry entry plus one editor.

### 1.2 The palette

| `data_type` | Storage | Canonical unit | Key config | Filter operators |
|---|---|---|---|---|
| `text` | text | — | `max_length`, `pattern`, `transform` | is, is not, contains, starts, ends, empty |
| `long_text` | text | — | `rows`, `markdown` | contains, empty (full-text indexed) |
| `number` | number | plain | `decimals`, `min`, `max`, `step`, `unit_label` | =, ≠, <, ≤, >, ≥, between, empty |
| `weight` | number | gram | `display_unit` (g, mg, gr, ozt, dwt), `decimals` | numeric set, between |
| `dimension` | number | millimetre | `display_unit` (mm, in), `decimals` | numeric set, between |
| `purity` | number | per mille 0–1000 | `display_style` (decimal, permille, percent, karat) | numeric set, between |
| `angle` | number | degree | `display_style` (degrees, clock hours) | numeric set, between |
| `money` | money | minor units | `dated` | =, <, >, between, empty |
| `date` | date | fuzzy span | `allow_range`, `allow_circa`, `calendars`, `display_format` | in year, between years, before, after, in decade, in century, is circa, unknown |
| `boolean` | bool | 0/1 | `true_label`, `false_label` | is true, is false, empty |
| `rating` | number | 0–5 | `max_stars`, `allow_half` | =, ≥, ≤, empty |
| `computed` | not stored | per formula | `expression`, `result_type`, `decimals` | operators of `result_type` |
| `json` | json | — | — | empty, not empty |

Notes on the ones that matter:

**There is no `lookup` type.** An earlier draft proposed free text with a remembered vocabulary for
mints, rulers and dealers. It is dropped: identity fields are plain `text`, and filtering text is
perfectly good for finding things. Remembered entries can return later as a *convenience on top of
`text`* — an autocomplete that suggests previous values — without being a distinct field type, which
means adding it later changes no stored data.

**`purity`** stores per mille, so `0.900`, `900`, `90%` and `22K` are one value entered four ways.
`entered_as` on the value row preserves the original expression.

**There is no `category` type either.** Fixed lists for metal, shape and edge were considered and
dropped for now: every field is plain text, which keeps entry unblocked and filtering simple. A
controlled list is a real convenience for a handful of attributes, so it will likely return — as an
addition that changes no stored data, not as a prerequisite.

There is deliberately **no `grade` field type**. Grading is a special system (Part 4.2), because a
coin can carry several grades from several standards with modifiers and history.

### 1.3 Computed fields

Declared by formula over other field keys, evaluated by the application:

```
asw            = {weight} * {fineness} / 1000 / 31.1034768
price_per_gram = {price_paid} / {weight}
age_years      = year(today()) - {date_issued.year_end}
```

Grammar: numeric literals, `{field_key}` references, `+ - * / ( )`, comparisons, and a fixed function
set (`min max round abs floor ceil if coalesce year month today days_between concat upper lower`).

Parsed with `ast.parse(mode='eval')` and walked against a strict node whitelist. **`eval` is never
called on user input**, even though this is a local desktop application, because presets are meant to
be shared between collectors and a shared preset must not be able to execute code.

Cycles are rejected at save time by topological sort. A formula referencing a missing or archived
field yields no value and raises one clear warning on the definition, not an error per row.

### 1.4 The sort key companion

Specified in [01 Part 4.2](01-core-data-model.md). Summary: `text` and `date` values carry an
optional numeric `sort_value` with a `sort_source` of `auto`, `manual` or `none`, plus a
`needs_review` flag. The app proposes, the user disposes, and the interface always shows which
happened. A manual value is never overwritten by the parser.

The date parser recognises plain years, ranges, `circa`, BC and `AH`, and flags anything it converted
or guessed. Anything it cannot read is stored as typed, sorts nowhere, and prompts for a number.
Editing a sort value is available from the cell's right-click menu at any time.

**Custom calendars are deferred but planned for.** `field_value_date.calendar` plus a parser registry
is the seam: adding "define your own era, with this conversion rule" later is additive and touches no
existing data.

---

## Part 2 — Feature bindings, instead of roles

The earlier draft had fields carry a `role` and features look themselves up. That is replaced with
the inverse, which is what you asked for: **the user tells each feature which field to use, and
nothing is ever inferred.**

`feature_binding` records one answer per question:

| Column | Meaning |
|---|---|
| `feature` | who is asking — `labels`, `albums`, `wishlist`, `naming`, `import:numista` |
| `purpose` | what it needs — `cutout_diameter`, `flag_country`, `hole_diameter`, `title` |
| `subcollection_id` | `NULL` for the library default, or an override for one subcollection |
| `target_kind` | `field`, `catalogue`, `grade`, `certification`, `constant`, `none` |
| `field_definition_id` / `catalog_id` / `constant_json` | the answer |

Resolution order: subcollection override, then library default, then unset. Verified behaviour:

```
duplicate library-wide binding:                          BLOCKED
per-subcollection override alongside library default:    ALLOWED
binding to a fixed constant instead of a field:          ALLOWED
```

Three properties this gives:

1. **The label generator's fixed columns become questions.** `diameter_column = A` in the current
   `config.txt` becomes *"which field holds the diameter?"*, answered once. `country_column = B`,
   which drives the `[flag]` token, becomes *"which field holds the country?"*.
2. **Constants are first-class.** *"All the coins in this print run are 38.1 mm"* is a binding to a
   constant, so a print run does not require a field to exist at all.
3. **Nothing breaks silently.** When a feature's binding is unset it says exactly what it needs and
   offers to set it: *"No field is set as the coin's diameter, so cutout circles cannot be drawn.
   Choose a field, or set a fixed diameter for this run."* Never a stack trace, never a silent zero.

Bindings are also how the wishlist learns which fields define a slot, and how Numista import maps
external fields onto the user's own — both specified in their own documents.

---

## Part 3 — Display labels and the master view

Specified in [01 Part 2](01-core-data-model.md). Summary: field definitions are library-wide; a
subcollection opts in through `subcollection_block`, which carries a `display_label` override.

One field shown as *Ruler* in Modern and *Emperor* in Ancients is one column in the master view,
because it is the same field. The merge is a consequence of identity, not of name matching or role
matching. Distinct fields stay distinct and appear under their own canonical labels.

---

## Part 4 — Special systems

Catalogue references, grades, certifications and external links are **not ordinary user fields**.
Each is multi-valued per coin, each has its own internal structure, and each needs its own sorting
and filtering rules. Modelling them as generic fields would either lose that structure or force the
user to fake it with numbered columns.

They are still positioned like fields in the interface: `subcollection_block` rows with
`block_kind` of `catalogues`, `grades`, `certifications` or `links` place each block in the form
layout, in any order, under any label, and removing a block hides it without deleting any data.

**All four registries ship empty**, and for test builds that is deliberate and absolute: no
catalogues, no grading companies, no grade scales, no modifiers, no example fields. Every test
constructs exactly the registry rows it needs, which keeps tests honest about what the code actually
requires and means no fixture data can be mistaken for a product decision.

Starter packs remain available as a later idea for real users, shipped as ordinary presets rather
than built-in rows.

### 4.1 Catalogue references

`catalog` defines a reference work; `catalog_reference` attaches numbers to specimens. A coin may
carry any number of references, including several from the same catalogue.

**Number handling** (verified, see 01): each number is stored three ways — `number_raw` exactly as
typed, `number_norm` for matching, and `sort_segments` for ordering and ranges. Parsing separates an
optional letter prefix, the base number and remaining segments, so `A54` sorts beside `54` rather
than in the fifty-thousands:

```
2, 2.1, 10, 22.9, 22.123, 54, 54.2, A54, A54.2, B54, 1042, 1042a
```

A range such as *10 through 54.2* is an indexed `BETWEEN` on `sort_segments`.
`catalog.letter_prefix_order` chooses whether `A54` precedes or follows `54`, per catalogue, because
catalogues disagree with each other.

**Display, both ways you asked for.** Table columns are descriptors in `saved_view.columns_json`:

| Descriptor | Shows | Sorts by |
|---|---|---|
| `catalogue:<id>` | only that catalogue's numbers — a dedicated *KM* column, a dedicated *Hartill* column | that catalogue's `sort_segments` |
| `catalogues:all` | every reference in one cell, e.g. `KM 2.1 · H 22.123 · N# 12345` | a chosen catalogue, set per column |

The second is the important one: **a combined column is still sortable and filterable by one
catalogue.** The column carries a `sort_by_catalog` setting, and the query orders by that catalogue's
segments while displaying everything. Filters available on either form: *has a reference in
catalogue X*, *number in range in catalogue X*, *has no reference in catalogue X*, *number matches*.

`is_primary` marks one reference per coin as the headline one, for compact displays and labels.

### 4.2 Grades

The hard requirements: several standards must interoperate, details grades must sort next to their
base grade, stickers must be representable, and a grade may come from a grading company, a dealer or
the collector's own opinion.

Four tables:

- **`grade_scale`** — a standard the user defines: Sheldon 1–70, adjectival, Chinese 1–10, anything.
- **`grade_level`** — the ordered values within a scale, each with a `normalised` position on one
  shared numeric axis, plus `aliases` so `MS-63`, `MS 63` and `Mint State 63` all resolve.
- **`grade_modifier`** — `Details`, `+`, `star`, CAC green, CAC gold, each with a
  `normalised_delta`.
- **`specimen_grade`** — a grade on a coin: scale, level, `raw_text` exactly as entered, the computed
  `normalised`, an optional `detail_note`, and `source` (`self`, `seller`, `tpg`, `auction`, `other`)
  with `assigned_by`.

`normalised` is what makes incompatible standards comparable, and every number on that axis is user
data. Verified output across three standards, with modifiers applied:

```
  norm   as entered       scale    by      detail
 63.30   MS63 CAC gold    SHELDON  NGC
 63.15   MS63 CAC green   SHELDON  NGC
 63.00   MS63             SHELDON  NGC
 62.60   MS63 Details     SHELDON  NGC     Cleaned
 62.00   MS62             SHELDON  PCGS
 53.00   AU               ADJ      me
 52.60   AU Details       ADJ      dealer  Scratches
 50.00   8                CN10     GBCA
 35.00   6                CN10     me
 27.50   VF               ADJ      dealer
```

This is the behaviour you asked for. `Details` is a modifier with a delta of −0.4, so *MS63 Details*
lands between MS62 and MS63, and *AU Details* immediately below *AU* — sorted **alongside** the base
grade rather than banished to the bottom or treated as an unrelated string. Stickers nudge upward
within the same grade. *"At least VF"* is one numeric predicate that works across Sheldon,
adjectival and Chinese scales at once. *"Exclude problem coins"* filters on modifier kind.

**The detail can be shown either way**, also as you asked. Column descriptors:

| Descriptor | Shows |
|---|---|
| `grade:primary` | the primary grade, e.g. `MS63 Details` |
| `grade:detail` | a separate column containing just `Cleaned` |
| `grade:combined` | `MS63 Details (Cleaned)` in one cell |
| `grade:scale:<id>` | only grades recorded on that scale |

Multiple grades per coin are supported, with `is_primary` marking the one shown by default — so a
dealer's optimistic *AU* and your own *XF* can coexist and be compared.

### 4.3 Certifications

`grading_company` and `certification`. A certification links to the `specimen_grade` it carries, so
grades from slabs sort on the same axis as everything else.

**Several certifications may be current at once.** Verified: a coin holds an NGC certification and a
CAC endorsement simultaneously. `status = 'current'` is therefore not unique per coin; only
`is_primary` is:

```
Two concurrent certifications on one coin (TPG + endorsement): 2
second primary: BLOCKED (correct)
```

`cert_number` is nullable, because some endorsements do not issue one.

**Certification history is first-class**, which matters in a field where cracking out is routine.
`status` covers `current`, `pending`, `cracked_out`, `crossed_over`, `regraded` and `superseded`, and
`supersedes_id` chains them:

```
2019-04-02  NGC  111111-001   cracked_out
2024-11-15  NGC  222222-002   current      supersedes the above
```

The coin's grade history is therefore readable as a sequence, and the current grade is one indexed
lookup. Column descriptors: `cert:primary`, `cert:company:<id>`, `cert:all`, `cert:history_count`.
Filters: certified by company X, has any certification, raw only, status is `cracked_out`, cert
number matches.

### 4.4 External links

`external_link` stores any number of links per coin, each with a `kind` (`zeno`, `numista`,
`grading`, `auction`, `dealer`, `paper`, `forum`, `museum`, `image`, `other`), an optional `label`,
the `url`, and a `reference` for a record id, lot number or page.

This covers the case where a specimen is already documented elsewhere — a Zeno record, a grading
company's verification page, an auction archive lot, a published plate — so the coin's external
paper trail lives with it. Filters: has a link of kind X, has any link, URL contains.

---

## Part 5 — Changing the schema after data exists

Users are expected to reshape their schema constantly, so these operations must be safe rather than
merely possible.

| Operation | Behaviour |
|---|---|
| Rename a label | Immediate. `key` is untouched, so bindings, presets and label layouts are unaffected |
| Reorder or regroup | Immediate; presentation only |
| Change config (decimals, display unit, options) | Immediate; stored canonical values unchanged |
| Remove from a subcollection | Deletes a `subcollection_block` row. Definition and values retained; re-adding restores everything |
| **Archive the definition (default "delete")** | Hidden everywhere, values retained, one click to restore |
| Delete permanently | Separate, confirmed action stating exactly how many values will be destroyed, offering to export them first |
| Change `data_type` | Add-convert-archive, never in place (below) |
| `is_multi` false → true | Immediate; existing values become `seq = 0` |
| `is_multi` true → false | Requires choosing which value to keep; the rest are exported first |

Archive is the default because you expect users to add and remove fields freely. A destructive
default would make experimenting with one's own schema frightening, which is the opposite of the
goal.

### Type changes

A `data_type` change never mutates in place. The application creates a new definition with the target
type, converts every value through `convert_from`, and archives the original — so nothing is
destroyed, the operation is reversible, and rows that fail conversion are reported rather than
silently blanked. Every conversion runs as a **dry run first**, reporting would-succeed,
would-fail and would-change-meaning counts, and takes a backup before proceeding.

| From → To | Behaviour |
|---|---|
| `text` → `number`/`weight`/`dimension`/`purity` | parsed; failures listed in the dry run |
| `text` → `date` | parsed by the fuzzy-date parser |
| `number` ↔ `weight`/`dimension`/`purity` | numerically preserved, unit reinterpreted, with a warning naming the assumed unit |
| `date` → `text` | uses `display`, so the user's own expression survives |
| anything → `long_text` / `json` | always |
| any other pair | offered only via `long_text` as an intermediate, with a warning |

---

## Part 6 — Presets

A preset is a JSON file bundling field groups, field definitions, and optionally
catalogues, grade scales, levels and modifiers. Applying one is
**additive and merges by key**: an existing key is left alone and reported as skipped, nothing is
ever deleted, and the user previews exactly what will be added before confirming.
`field_definition.origin_preset` records provenance so "remove what this preset added" stays
possible.

Users export their own setup as a preset in one action, which is what makes sharing schemas, grade
scales and catalogue definitions between collectors work.

Presets contain only declarative data. The only expression-like content is `computed` formulas,
evaluated by the whitelisted parser of 1.3.

---

## Part 7 — Decisions

### Resolved

| Question | Answer | Where it lands |
|---|---|---|
| Starter packs | None. Test builds are a completely blank slate | 4 |
| `Details` grades | Sort **just below** their base grade | 4.2, modifier delta −0.4 |
| Multiple grades | The primary is **chosen by the user**; never inferred from recency or source | 4.2, `is_primary` |
| Combined catalogue column | Coins with no reference in the sorted catalogue go **to the bottom** | 4.1 |
| Remembered entries | Dropped. Plain `text` fields, filtered as text | 1.2 |
| Fixed lists (`category`) | Also dropped. Everything is plain text for now; to be revisited | 1.2 |
| Vocabulary scope | Moot, since vocabularies are gone | — |

### Still open

1. **Grade axis.** The shared axis currently resembles Sheldon 1–70, because that made the worked
   example readable. Would you rather it were an abstract 0–100 so no standard appears privileged?
   It changes nothing structurally — only the numbers users type when defining a scale.
2. **When fixed lists return**, should they be a distinct field type again, or a constraint layered
   on top of `text` so no conversion is ever needed?
