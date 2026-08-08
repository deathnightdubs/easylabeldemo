# 02 — Field types and semantic roles

Status: **Proposed — awaiting approval**
Depends on: [01 — Core data model](01-core-data-model.md)

This document specifies the palette of field types the application offers, the semantic roles that
let numismatic features find the fields they need, the default preset, and what happens when a user
changes their schema.

---

## Part 0 — The two mechanisms, and why both exist

The product promise is *"a normal custom database: I choose from your field types and build the
schema I want; your presets are a starting point I can freely edit."* That gives complete freedom
and creates one problem: if every field is user-defined, how does the label generator know which
field holds the coin's diameter, or the wishlist know which field holds the grade?

The answer is **semantic roles**. A field definition may carry an optional `role`, and features
resolve fields by role rather than by hard-coded name:

- The existing generator's `diameter_column = A` becomes *"whichever field has role `DIAMETER`"*.
- Its `country_column = B`, which drives the `[flag]` token, becomes role `COUNTRY`.
- "Minimum grade" in a wishlist criterion becomes *"the field with role `GRADE`"*.
- Sorting an album by date uses role `DATE_ISSUED`, whatever the user called that field.

So there are exactly two mechanisms, and they are orthogonal:

| Mechanism | Answers | Owned by |
|---|---|---|
| **Field type** (`data_type`) | how a value is stored, validated, sorted, formatted and edited | the application; a fixed registry |
| **Semantic role** (`role`) | what a field *means* to numismatic features | the user (presets pre-assign it) |

A field can have a type and no role — pure custom data, fully functional, simply invisible to
features that need meaning. A field can be renamed, moved, regrouped and reordered at will without
any feature noticing, because features never look at labels.

---

## Part 1 — The field type registry

### 1.1 Registry contract

Each type is one entry in `core/fields/registry.py` implementing:

```
key                 'weight'
label               'Weight'
category            physical | identity | financial | condition | descriptive | meta
storage             field_value_number
config_schema       declared options with defaults and validation
supports_multi      whether is_multi is allowed
compatible_roles    roles this type may be assigned to
parse(raw, config)      user text -> canonical stored value  (raises FieldParseError)
format(value, config)   canonical value -> display string
sort_expression(alias)  SQL expression used for ORDER BY
filter_operators        the operator set offered in the filter builder
to_export/from_export   lossless JSON round-trip
convert_from(type, v)   value migration when a field's type changes
label_token(value, cfg) short form used on printed labels
```

Editor widgets live in `ui/fields/editors.py`, registered against the same `key`. The split keeps
`core/` free of Qt so field behaviour is unit-testable and reusable from the CLI, which is the
architectural rule the whole project depends on.

**Adding a field type is one registry entry plus one editor.** That is the extensibility seam.

### 1.2 Value types

Canonical storage units are as defined in 01 §0.5.

| `data_type` | Storage | Canonical unit | Key config options | Filter operators |
|---|---|---|---|---|
| `text` | `field_value_text` | — | `max_length`, `pattern`, `transform` (none/upper/title), `placeholder` | is, is not, contains, starts, ends, empty, not empty |
| `long_text` | `field_value_text` | — | `markdown`, `rows` | contains, empty, not empty (full-text indexed) |
| `lookup` | `field_value_text` | — | `vocabulary` (named term list), `allow_new` | is, is not, contains, in list, empty |
| `number` | `field_value_number` | plain | `decimals`, `min`, `max`, `step`, `unit_label`, `thousands` | =, ≠, <, ≤, >, ≥, between, empty |
| `weight` | `field_value_number` | gram | `display_unit` (g, mg, gr, ozt, dwt), `decimals`, `tolerance` | numeric set, between |
| `dimension` | `field_value_number` | millimetre | `display_unit` (mm, in), `decimals` | numeric set, between |
| `purity` | `field_value_number` | per mille (0–1000) | `display_style` (decimal, permille, percent, karat), `metal_role_link` | numeric set, between |
| `angle` | `field_value_number` | degree | `display_style` (degrees, clock hours) | numeric set, between |
| `money` | `field_value_money` | minor units + ISO code | `default_currency`, `allow_other_currencies`, `convert_to_base`, `dated` | =, <, >, between (in base currency), empty |
| `date` | `field_value_date` | fuzzy span | `allow_range`, `allow_circa`, `calendars`, `default_calendar`, `display_format`, `min_year`, `max_year` | in year, between years, before, after, in decade, in century, is circa, unknown |
| `boolean` | `field_value_bool` | 0/1 | `true_label`, `false_label`, `default` | is true, is false, empty |
| `category` | `field_value_option` | option id | `hierarchical`, `allow_new_from_entry`, `default_option`; `is_multi` for multi-select | is, is not, is any of, is under (hierarchy), empty |
| `grade` | `field_value_grade` | normalised 1–70 | `allowed_scales`, `default_scale`, `allow_qualifiers`, `allow_problem`, `allow_split_grade` | is, at least, at most, between, in scale, has problem, empty |
| `url` | `field_value_text` | — | `link_label` | contains, empty |
| `rating` | `field_value_number` | 0–5 integer | `max_stars`, `allow_half` | =, ≥, ≤, empty |
| `computed` | not stored | declared by formula | `expression`, `result_type`, `result_unit`, `decimals` | operators of `result_type` |
| `json` | `field_value_json` | — | `schema_hint` | empty, not empty only |

Notes on the less obvious ones:

**`lookup`** is free text with a remembered vocabulary. Typing *Kremnica* offers previous entries
and adds new ones automatically. This is the right type for mint names, dealers and rulers: a rigid
`category` forces the user to pre-declare every possible value before they can record a coin, while
plain `text` produces *Kremnica*, *kremnica* and *Kremnitz* as three unrelated values. It gives
data hygiene without administrative overhead, and is the default for most identity fields.

**`purity`** stores per mille so that `0.900`, `900`, `90%` and `22K` are the same value entered
four ways. `entered_as` on the value row preserves the original expression for display.

**`grade`** accepts split and problem grades. `allowed_scales` lets an Ancients subcollection offer
only adjectival grading while a US subcollection offers Sheldon; `normalised` still makes
*"at least VF"* work across both, which is why grade is not merely a `category`.

**`money` with `dated: true`** records a valuation date. An estimate without a date decays into
misinformation, so valuation fields default to dated.

**`rating`** exists because eye appeal is a real, useful, entirely subjective axis that collectors
sort by and no catalogue provides.

**`angle`** covers die axis, conventionally shown either in degrees or as clock hours.

### 1.3 Computed fields

`computed` fields are declared by a formula over other fields' keys and are evaluated by the
application, then written into `specimen_cache` so they remain sortable and filterable.

```
asw            = {weight} * {fineness} / 1000 / 31.1034768
price_per_gram = {price_paid} / {weight}
melt_value     = {asw} * spot('XAG')
age_years      = year(today()) - {date_issued.year_end}
premium_pct    = ({price_paid} - {melt_value}) / {melt_value} * 100
```

Grammar: numeric literals, `{field_key}` references, `+ - * / ( )`, comparison operators, and a
fixed function set — `min max round abs floor ceil if coalesce year month today days_between
concat upper lower`. `spot(metal)` reads an optional, manually maintained spot-price table and is
never fetched silently from the internet.

Implementation requirement: the expression is parsed with `ast.parse(mode='eval')` and walked
against a strict node whitelist, or with a small recursive-descent parser. **`eval` is never called
on user input**, even though this is a local desktop application, because presets are designed to
be shared between collectors and a shared preset must not be able to execute code.

Cycles are rejected at save time by topological sort. A formula referencing a missing or archived
field yields no value and surfaces one clear warning on the field definition, not an error per row.

Actual silver weight is the motivating example: every precious-metal collector wants it, it is
derivable from two fields they already have, and hard-coding it would be absurd when the same
mechanism gives them price-per-gram and premium over melt for free.

### 1.4 Relational types

Used with `kind = 'relational'`; the definition stores no values and acts as a presentation and
behaviour binding onto a table from document 01.

| `relation` | Table | Config options |
|---|---|---|
| `catalog_reference` | `catalog_reference` | `catalogs` (restrict to a subset), `default_catalog`, `sort_by` (catalog, number, primary first), `show_urls` |
| `certification` | `certification` + `grading_company` | `companies`, `show_history`, `require_number` |
| `media` | `media_asset` + `media_link` | `roles` shown, `max_items`, `layout` (grid/filmstrip), `allow_edit` |
| `provenance` | `provenance_entry` | `show_unverified`, `citation_style` |
| `specimen_link` | `specimen_link` | `kinds` allowed |
| `event` | `specimen_event` | read-only summary; `event_types`, `show_money` |

Because these are ordinary field definitions, a user can drag the *Catalogue Numbers* block above
*Physical Details*, rename it *References*, put it in a different group per subcollection, or remove
it entirely — and removing it never deletes a single catalog reference. Archiving hides the block;
re-adding it brings the data straight back.

---

## Part 2 — Semantic roles

### 2.1 Role catalog

`role` is `NULL` or one of the following. At most one field per role per scope and entity
(enforced by `ux_field_role` in 01 §2.2).

**Identity and attribution**

| Role | Compatible types | Consumed by |
|---|---|---|
| `TITLE` | text, lookup | naming template, list views, label token |
| `COUNTRY` | lookup, category | **`[flag]` label token**, grouping, cache, Numista mapping |
| `ISSUER` | lookup, category | grouping, wishlist criteria, cache (`authority`) |
| `RULER` | lookup | wishlist criteria ("one coin per emperor"), album ordering |
| `DYNASTY` | lookup, category | grouping, album ordering |
| `MINT` | lookup | wishlist criteria, grouping, cache |
| `MINTMARK` | text | label token, variety matching |
| `DENOMINATION` | lookup, category | wishlist ("one of every denomination"), grouping, cache |
| `FACE_VALUE` | number | sorting within denomination, reports |
| `CURRENCY_UNIT` | lookup | display, Numista mapping |
| `SERIES` | lookup, category | set completion, album ordering |
| `VARIETY_NAME` | text, lookup | label token, variety matching |

**Dating**

| Role | Compatible types | Consumed by |
|---|---|---|
| `DATE_ISSUED` | date | **date range filters**, album/label ordering, cache `date_sort_key`, wishlist |
| `DATE_ON_COIN` | text | label token (the date as actually struck, e.g. a regnal or AH expression) |

**Physical**

| Role | Compatible types | Consumed by |
|---|---|---|
| `DIAMETER` | dimension | **the label generator's cutout circle diameter**, album true-scale rendering, holder fit checks |
| `WEIGHT` | weight | ASW, filters, authenticity checks |
| `THICKNESS` | dimension | slab/holder fit, reports |
| `WIDTH`, `HEIGHT` | dimension | non-round coins: rectangular cutouts, album rendering |
| `SHAPE` | category | cutout shape selection (round, square, holed, scalloped) |
| `METAL` | category, lookup | grouping, ASW, cache, filters |
| `FINENESS` | purity | ASW, melt value |
| `EDGE` | category, text | label token, variety matching |
| `DIE_AXIS` | angle | scholarly detail, ancients |
| `HOLE_DIAMETER` | dimension | **central hole for cash coins**: label cutout, album rendering |

**Condition**

| Role | Compatible types | Consumed by |
|---|---|---|
| `GRADE` | grade | wishlist minimum-grade criteria, sorting, cache, label token |
| `CERTIFICATION` | relational `certification` | cert display, slab album rendering, cache |
| `EYE_APPEAL` | rating | personal sorting |
| `CONDITION_NOTE` | text, long_text | label token, warnings |

**Descriptive**

| Role | Compatible types | Consumed by |
|---|---|---|
| `OBVERSE_DESC`, `REVERSE_DESC`, `EDGE_DESC` | long_text | detail view, Numista mapping, full-text search |
| `OBVERSE_LEGEND`, `REVERSE_LEGEND` | text, long_text | label token, CJK search index |
| `CALLIGRAPHY` | lookup, category | Chinese cash variety matching and wishlist criteria |
| `MINTAGE` | number | rarity context, reports |
| `NOTES` | long_text | detail view, full-text search |

**Financial and logistics**

| Role | Compatible types | Consumed by |
|---|---|---|
| `PRICE_PAID` | money | import mapping and display; the ledger in `specimen_event` remains authoritative |
| `VALUE_ESTIMATE` | money (dated) | portfolio reports, insurance schedule |
| `ACQUIRED_DATE` | date | reports, timeline |
| `ACQUIRED_FROM` | lookup | dealer statistics |
| `QUANTITY` | number | totals |
| `STORAGE` | relational/text | album mapping, location reports |
| `CATALOG` | relational `catalog_reference` | catalog search, wishlist range criteria, label token |
| `IMAGES` | relational `media` | album tiles, detail view, exports |

The `DIAMETER`, `COUNTRY`, `SHAPE` and `HOLE_DIAMETER` rows are worth noting: they are precisely
the inputs the current generator reads from fixed spreadsheet columns. Roles are what let the same
rendering code work against an arbitrary user-defined schema without ever hard-coding a column.

### 2.2 Rules

1. **Roles are optional.** A library with no roles assigned is fully usable as a plain database;
   only role-dependent features are unavailable.
2. **One field per role, per scope, per entity.** Ambiguity would make every consumer guess.
3. **Type compatibility is enforced.** Assigning role `WEIGHT` to a `long_text` field is refused —
   the one place the app blocks rather than warns, because the result would be silently broken
   features rather than merely odd data.
4. **Missing roles degrade gracefully and actionably.** Printing labels with no `DIAMETER` role
   assigned produces: *"No field is marked as the coin's diameter, so cutout circles cannot be
   drawn. Assign a role to a field, or set a fixed diameter for this print run."* — with a button
   that does it. Never a stack trace, never a silent zero.
5. **Reassignment is free and instant.** Roles live on the definition, so changing which field is
   the grade field is one update plus a cache rebuild; no data moves.
6. **Type-to-specimen inheritance.** A specimen's effective value for a role is its own value if
   present, otherwise the value from its linked `coin_type` (then `variety`). Shared catalogue data
   is entered once on the type and every specimen inherits it, shown as inherited in the UI and
   overridable per specimen. Without this rule the type/specimen split would create duplicate
   typing instead of removing it.
7. **Roles drive the cache.** Every column in `specimen_cache` is populated from the field currently
   holding the matching role. That is the mechanism that makes a fully user-defined schema sort
   50,000 coins instantly.

### 2.3 Roles are how heterogeneous subcollections merge

This is the payoff for the requirement that subcollections with different fields still be viewable
together.

Suppose *World Coins* has a field `country` (lookup, role `COUNTRY`) and *Ancients* has
`authority` (lookup, role `ISSUER`), and both have differently named grade fields. In the combined
view:

- Columns are computed as the union of fields across the selected subcollections.
- Fields sharing a **role** collapse into one column, labelled by the role's display name — both
  grade fields become a single sortable *Grade* column, correctly ordered via `normalised`.
- Global fields appear once.
- Role-less, subcollection-specific fields appear as their own sparse columns, empty for rows from
  other subcollections.
- Sorting and filtering the merged column applies to each subcollection's role-holding field.

Without roles, a combined view could only ever match on field key, which would force every
subcollection to reuse identical keys and defeat the whole point of letting them differ.

---

## Part 3 — Presets

### 3.1 What a preset is

A preset is a JSON file: a bundle of field groups, field definitions with roles, category options,
lookup vocabularies, catalogs to seed, default views, and later default label layouts and holder
templates. Nothing more. It has no privileged status at runtime — once applied, its fields are
ordinary user fields.

Shipped with v1:

| Preset | Focus |
|---|---|
| `general-world` | the default: country, denomination, year, metal, weight, diameter, grade, KM reference |
| `us-coins` | Sheldon grading, mintmarks, series/type sets, PCGS/NGC certification, Red Book references |
| `chinese-cash` | dynasty, emperor/reign, calligraphy style, hole diameter, Hartill and Fisher's Ding references, obverse/reverse legend in Chinese |
| `ancients` | authority/ruler, mint, die axis, RIC/Sear references, adjectival grading, provenance emphasis |
| `precious-metals` | fineness, ASW/AGW computed fields, melt and premium, mint/refiner |
| `tokens-medals` | issuer, material, exonumia-oriented descriptive fields |

### 3.2 The default preset: `general-world`

Field list for `entity = 'specimen'`. Every one of these is deletable; `is_protected` is `0`
throughout, which is the direct expression of the requirement that even the default schema is fully
editable by a beginner.

| Group | Key | Label | Type | Role | In table |
|---|---|---|---|---|---|
| Identification | `country` | Country | lookup | `COUNTRY` | yes |
| Identification | `denomination` | Denomination | lookup | `DENOMINATION` | yes |
| Identification | `face_value` | Face value | number | `FACE_VALUE` | no |
| Identification | `date_issued` | Date | date | `DATE_ISSUED` | yes |
| Identification | `mint` | Mint | lookup | `MINT` | no |
| Identification | `mintmark` | Mint mark | text | `MINTMARK` | no |
| Identification | `ruler` | Ruler / issuer | lookup | `ISSUER` | no |
| References | `catalogue` | Catalogue numbers | relational `catalog_reference` | `CATALOG` | yes |
| Physical | `metal` | Metal | category | `METAL` | yes |
| Physical | `fineness` | Fineness | purity | `FINENESS` | no |
| Physical | `weight` | Weight | weight | `WEIGHT` | yes |
| Physical | `diameter` | Diameter | dimension | `DIAMETER` | yes |
| Physical | `thickness` | Thickness | dimension | `THICKNESS` | no |
| Physical | `shape` | Shape | category | `SHAPE` | no |
| Physical | `edge` | Edge | category | `EDGE` | no |
| Condition | `grade` | Grade | grade | `GRADE` | yes |
| Condition | `certification` | Certification | relational `certification` | `CERTIFICATION` | no |
| Condition | `eye_appeal` | Eye appeal | rating | `EYE_APPEAL` | no |
| Acquisition | `acquired_date` | Acquired | date | `ACQUIRED_DATE` | no |
| Acquisition | `acquired_from` | Acquired from | lookup | `ACQUIRED_FROM` | no |
| Acquisition | `price_paid` | Price paid | money | `PRICE_PAID` | no |
| Acquisition | `value_estimate` | Estimated value | money (dated) | `VALUE_ESTIMATE` | no |
| Storage | `storage` | Location | relational/text | `STORAGE` | no |
| Description | `obverse_desc` | Obverse | long_text | `OBVERSE_DESC` | no |
| Description | `reverse_desc` | Reverse | long_text | `REVERSE_DESC` | no |
| Description | `mintage` | Mintage | number | `MINTAGE` | no |
| Media | `images` | Images | relational `media` | `IMAGES` | no |
| Notes | `notes` | Notes | long_text | `NOTES` | no |

Seeded categories: `metal` (Gold, Silver, Billon, Copper, Bronze, Brass, Nickel, Cupro-nickel,
Aluminium, Zinc, Iron, Lead, Tin, Bimetallic, Other), `shape` (Round, Round with hole, Square,
Square with hole, Rectangular, Scalloped, Polygonal, Irregular, Holed off-centre),
`edge` (Plain, Reeded, Lettered, Ornamented, Interrupted reeding, Security).

### 3.3 Preset file format

```json
{
  "preset_format": 1,
  "id": "chinese-cash",
  "name": "Chinese Cash Coins",
  "version": "1.0.0",
  "author": "…",
  "description": "…",
  "requires_app_version": ">=0.1.0",
  "target_entity": "specimen",
  "groups":      [ { "key": "identification", "label": "Identification", "sort_order": 10 } ],
  "vocabularies":[ { "key": "mints_china", "terms": ["Baoquan", "Baoyuan", "…"] } ],
  "options":     [ { "field_key": "calligraphy", "values": [ { "key": "songti", "label": "Songti" } ] } ],
  "fields":      [ { "key": "dynasty", "label": "Dynasty", "kind": "value",
                     "data_type": "lookup", "role": "DYNASTY", "group": "identification",
                     "sort_order": 20, "show_in_table": true, "config": {} } ],
  "catalogs":    [ { "code": "H",  "name": "Hartill, Cast Chinese Coins" },
                   { "code": "FD", "name": "Fisher's Ding" } ],
  "views":       [ { "name": "By dynasty", "group_by": "dynasty", "sort": ["date_issued"] } ]
}
```

Application semantics:

- **Merge by `key`, never overwrite.** An existing field with the same key is left alone and
  reported as skipped.
- **Additive only.** Applying a preset never deletes a field, a value or an option.
- **Preview first.** The user sees exactly what will be added before confirming.
- **Provenance recorded.** `field_definition.origin_preset` records where a definition came from,
  which makes "remove everything this preset added" possible later.
- **No executable content.** Only declarative data; the only expression-like content is `computed`
  formulas, evaluated by the whitelisted parser of §1.3.

Users export their own setup as a preset with one action, which is what makes community sharing of
schemas, label layouts and album templates work.

---

## Part 4 — Changing the schema after data exists

Users are expected to reshape their schema continuously, so these operations must be safe rather
than merely possible.

| Operation | Behaviour |
|---|---|
| Rename label | Immediate; `key` is untouched, so nothing downstream breaks |
| Reorder / regroup | Immediate; presentation only |
| Change config (decimals, display unit, options list) | Immediate; stored canonical values unchanged |
| Hide | `is_hidden = 1`; values retained and still searchable |
| **Archive (default "remove")** | `is_archived = 1`; disappears from forms, table and filter builder; all values retained; one click to restore |
| Delete permanently | Separate, confirmed action stating the exact number of values to be destroyed; offers an export of those values first |
| Change `data_type` | Implemented as **add-convert-archive**, never in place (below) |
| Reassign role | Immediate, plus a cache rebuild |
| Change `is_multi` false→true | Immediate; existing values become `seq = 0` |
| Change `is_multi` true→false | Requires choosing which value to keep; extras exported first |

**Archive is the default because the requirement is that users add and remove fields freely.** A
destructive default would make experimenting with one's own schema frightening, which is the
opposite of the goal.

### 4.1 Type changes: add, convert, archive

A `data_type` change never mutates a field in place. Instead the application creates a new
definition with the target type, converts every value through `convert_from`, and archives the
original. Consequences: nothing is destroyed, the operation is fully reversible, and rows that fail
conversion are reported rather than silently blanked.

Conversion support:

| From → To | Behaviour |
|---|---|
| `text` → `lookup` | always succeeds; distinct values become the vocabulary |
| `text` → `category` | distinct values become options; blanks skipped |
| `text` → `number`/`weight`/`dimension`/`purity` | parsed; unparseable values listed in a dry-run report |
| `text` → `date` | parsed by the fuzzy-date parser, which handles most collector notation |
| `number` ↔ `weight`/`dimension`/`purity` | numerically preserved; unit reinterpreted, with a warning stating the assumed unit |
| `category` → `text`/`lookup` | option labels become text |
| `grade` → `text` | uses `display`; normalisation is lost |
| `text` → `grade` | parsed against the enabled scales; unrecognised values listed |
| anything → `long_text` | always succeeds via `format()` |
| anything → `json` | always succeeds |
| `date` → `text` | uses `display`, so the user's original expression survives |
| any other pair | offered only via `long_text` as an intermediate, with an explicit warning |

Every conversion runs as a **dry run first**, reporting counts of would-succeed, would-fail and
would-change-meaning rows, and takes a database backup before proceeding.

---

## Part 5 — Decisions still needed

1. **Role naming shown to users.** Are roles presented as a plain dropdown labelled *"What is this
   field?"* with entries like *Coin diameter*, or as an advanced setting hidden behind a toggle? It
   affects how discoverable the numismatic features are for a beginner who builds their own schema
   from scratch.
2. **Automatic role suggestion.** When a user creates a field called *Weight*, should the app
   pre-select the `WEIGHT` role? Convenient, and the only place anything resembling guessing enters
   the design — with the guess always visible and one click to change.
3. **Vocabulary sharing.** Should `lookup` vocabularies be global to the library or per field? Per
   field is cleaner; global means mint names typed in one subcollection help another.
4. **Split grades.** Worth supporting obverse/reverse split grades in v1, or defer?
5. **Spot prices.** Is a manually maintained spot-price table acceptable for `melt_value`, or is an
   optional online fetch wanted despite the local-first principle?
6. **Preset scope.** Should presets be applicable per subcollection (so one library can hold a
   `chinese-cash` subcollection and a `us-coins` subcollection with entirely separate schemas)?
   The schema in 01 supports this; it needs a UI decision.
