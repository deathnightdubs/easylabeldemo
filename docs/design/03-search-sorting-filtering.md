# 03 — Search, sorting and filtering

**Status: implemented**, apart from grouping and per-view column sets, which are noted as deferred
at the end.

A collection that cannot be sliced is only a list. This document covers how a user narrows the grid
down to the coins they mean, orders them, decides how much of each multi-valued system a column
shows, and saves the result to come back to.

Related code: [`filters.py`](../../src/numis/filters.py) (the filter as a value),
[`filter_sql.py`](../../src/numis/filter_sql.py) (the translation to SQL),
[`columns.py`](../../src/numis/columns.py) (per-column display),
`CollectionService.query_specimens`, and the two dialogs
[`filter_dialog.py`](../../src/numis/ui/filter_dialog.py) and
[`column_settings.py`](../../src/numis/ui/column_settings.py).

---

## Part 1 — One question, not three

Filtering, searching and sorting were built as three separate paths and it was a mistake that
showed immediately: a search silently discarded the active sort, and a sorted column silently
ignored the filter. A collector's actual question is a single one — *Qianlong cash, graded,
heaviest first* — so there is a single method that answers it:

```python
service.query_specimens(
    subcollection,
    filters=group,        # a FilterGroup, or None
    sort=keys,            # a sequence of SortKey, most significant first
    term="通寶",           # full-text search, intersected with the rest
    include_deleted=False,
    include_disposed=True,
)
```

The grid calls nothing else. `count_specimens` answers the same question without loading rows,
which is what lets the filter dialog show a running match count.

---

## Part 2 — A filter is a tree

A `FilterGroup` holds criteria and further groups, matched with `all` or `any`, optionally negated.
Nesting is not decoration: *bronze, and either Qianlong or Jiaqing* cannot be written as a flat
list of conditions at all, and it is an ordinary thing to want.

A `Criterion` is a target, an operator and zero or more values:

| Target | Asks about |
|---|---|
| `__id__` | The coin's identifier |
| `__name__` | Its name |
| `__subcollection__` | Which subcollection it is in |
| `__status__` | `owned`, `sold`, `wanted`… |
| `__favourite__` | Whether it is starred |
| `field:<key>` | One of the user's own columns |
| `catalogues`, `grades`, `certifications`, `links` | One of the special systems |

### Operators come from the field registry

`FieldType.filter_operators` already declared, per data type, what could sensibly be asked of it.
Nothing read it. It is now the single source: a date column offers `in_decade` and `between_years`,
a weight column offers `gte` and `between`, a long-text column offers only `contains` and presence,
and a new field type arrives with its operators already decided rather than needing the filter UI
to be taught about it.

Numeric values are parsed **through the field type** before comparison, because a weight is stored
in grams and money in minor units. `5 g` and `£1.50` therefore mean what they say instead of being
read as bare numbers.

### Every criterion is EXISTS, never a join

This is the load-bearing decision. A coin can hold several values for one field, several catalogue
numbers, several grades. Joining multiplies rows, and the consequences are not subtle:

- `ruler is not Victoria` would match a coin whose *second* ruler value is Albert.
- A coin with three catalogue numbers would appear three times, and be counted three times.

`EXISTS` / `NOT EXISTS` asks the question that was meant — *does this coin have any value like
that* — and composes correctly under `AND`, `OR` and `NOT`. Sort keys are scalar subqueries for the
same reason: a coin with three catalogue numbers must still occupy exactly one row.

### What a negative filter means

`metal is not bronze` matches coins that record a metal which is not bronze. It does **not** match
coins with no metal recorded.

A criterion is a statement about what a field contains, and a coin that records nothing makes no
such statement. Treating absence as "not bronze" quietly fills a narrowing filter with blank rows,
which is the opposite of what a filter is for. Wanting the blanks as well is entirely expressible,
and reads as what it is: a group matching *any* of `metal is empty` or `metal is not bronze`.

### Picking coins by hand

`__id__` accepts `is_any_of`, taking a list. This is what makes a hand-picked set of coins
expressible as a filter, and therefore savable as a view — the mechanism offered in place of
letting one coin belong to several subcollections (see 01, Part 2). A view holding
`ID is any of 3, 7, 11` is a durable named set that costs no schema change.

---

## Part 3 — Sorting

Sorting is a list of `SortKey(target, descending)`, most significant first.

- Clicking a header sorts by that column alone, and clicking again reverses it.
- `Ctrl`-clicking adds a column *behind* the keys already chosen, so *country, then date within
  each country* is built by two clicks. Re-adding a column already in the list moves it rather
  than duplicating it.
- A sort key whose column leaves the screen is dropped, and the indicator is cleared, because a
  remembered sort pointing at an absent column silently orders by something else.

Qt's own header-click sorting had to be turned off to do this: it gives the model no way to know
whether a modifier was held, and that modifier is the entire difference between "sort by this" and
"then by this".

**Missing values sort last in both directions.** A blank is not smaller than everything; it is
absent, and burying blanks at the top of a descending sort would be worse than useless.

### What each column sorts by

| Column | Ordered by |
|---|---|
| Text field | The text, case-insensitively — or its sort key when `numeric_sort` is set, so `10 wen` follows `1 wen` |
| Date field | `sort_value`, the midpoint of the span, so `1736-1795` sorts at 1765.5 |
| Number, weight, dimension, purity, angle | The canonical value |
| Money | `amount_minor`, exact integers |
| ID | Numerically where the identifier is a number, then alphabetically, blanks last |
| **Grades** | `normalised`, the **calculated value** |
| Catalogue numbers | `sort_segments`, so a catalogue sorts as it reads |
| Certifications | The company's code |
| Links | How many there are |

Grades sorting by the calculated value is the point of having made grades typed rather than
registered (see 02, Part 4): the user types what they want to *see* and what it is *worth*, and the
worth is what gets compared. `MS63` therefore compares with `gVF`, and `AU Details` sits
immediately below `AU` rather than at the bottom of the collection.

A catalogue column narrowed to one catalogue sorts by *that* catalogue's numbers, not by whichever
reference happens to rank first.

---

## Part 4 — What a column shows

A coin can hold any number of catalogue numbers, grades, certifications and links; a column is one
cell wide. Each of those columns therefore carries its own display settings, stored in the existing
`subcollection_block.config_json` — no new table and no migration.

Which entries to show:

| Mode | Meaning |
|---|---|
| `all` | Every entry, joined by a separator |
| `only` | Just the ones from one place: only Numista, only Hartill, only PCGS |
| `rank` | Only the one the user put first, second, third… |

`only` is what turns a generic "catalogue numbers" column into a *Numista* column, without needing
a separate user field for every catalogue a collector cites.

`rank` counts positions in the user's order rather than matching the stored number, because ranks
are not uniquely enforced: the third one down is what was pointed at, whatever integers happen to
be on the rows.

How much of each, which necessarily differs by system:

- **Grades** — modifiers; whether to add what each modifier says *on this coin*
  (`Details — Harshly Cleaned`); full names instead of short forms (`Full Bands`, not `FB`);
  whether to name a sticker's company; the scale; the source; who assigned it. An individual grade
  can still opt out of showing its assigner via `hide_assigned_by`, so recording that a dealer
  graded a hundred coins need not put his name on a hundred rows. See
  [02](02-fields-and-special-systems.md), 4.2.1 for what each of those does to the text.
- **Catalogue numbers** — whether to print the catalogue's code. Dead weight in a column that only
  ever shows one catalogue.
- **Links** — a count, or the labels.

Settings are read forgivingly. An unknown mode, a rank of 99, a separator that is not a string, or
invalid JSON all fall back to the defaults, because a column with confused settings should still
draw. Only what differs from the defaults is stored, which keeps saved settings legible and lets a
later change of default reach existing columns.

**In the master view**, two subcollections can hold different settings for the same system and
neither is more correct. Disagreement falls back to the plain defaults rather than letting whichever
loaded first decide, and the settings dialog says that they belong to a subcollection.

---

## Part 5 — Search

Full-text search is unchanged in design (01, Part 6): one FTS5 index, with a `cjk_blob` column
holding CJK content split into single characters so that two-character terms such as 通寶 are
findable inside a longer legend.

What changed is that **the index maintains itself**. Nothing in the interface had ever rebuilt it,
so a coin edited in the grid could not be found by the text that had just been typed into it —
search was quietly stale for everything except data loaded by the CLI. Reindexing now happens in
the service at every point that changes indexed content: values, names, identifiers and catalogue
numbers. Putting it there rather than in the UI commands means it cannot depend on a caller
remembering.

Search results are intersected with the filter and ordered by the sort, rather than replacing
either.

---

## Part 6 — Saved views

`saved_view` existed in the base schema with `filter_json`, `sort_json` and `columns_json`, and had
no reader at all. It has one now.

A view remembers its subcollection (or none, meaning it applies anywhere), a filter and a sort
order. Saving a name that already exists replaces it, because that is what a person means by saving
a view they already have; the alternative is a list containing four things called "Chinese".

Views are reachable from *View → Saved views*, and survive reopening the library.

---

## Part 7 — Telling the user what they are looking at

A filtered grid looks like data loss. Three things guard against that reading:

- The status bar carries a permanent description of the active filter — *Filtered: Weight is at
  least 20 and (Ruler is Victoria or Ruler is Maria Theresia)* — with the shortcut to clear it.
- The toolbar action counts the active tests: *Filter (3)…*
- A special column's header tooltip says what that column is showing, since a column narrowed to
  one catalogue is otherwise indistinguishable from a coin having only one number.

A filter that cannot be carried out — `weight is at least heavy` — reports itself and leaves the
unfiltered rows on screen. Emptying the grid without explanation is the failure mode to avoid.

In the filter dialog, a row with no value is a placeholder rather than a question: the dialog opens
with one so there is somewhere to type. A row that is only *partly* filled — one end of a
`between` — is reported instead of being dropped, because dropping it would change the filter the
user believes they wrote.

---

## Deferred

- **Grouping.** `saved_view.group_by` exists and is stored; nothing groups yet.
- **Per-view column sets.** `saved_view.columns_json` is stored but not applied; a view currently
  restores a filter and a sort, not a choice of columns.
- **Smart collections.** A saved view is already a live query; promoting one to something that
  looks like a subcollection in the sidebar is presentation, and waits until there is a sidebar.
- **Ranked search results.** Matches are returned in the sort's order, not by relevance; `bm25`
  ranking waits until there is a reason to prefer it over the user's chosen order.
- **Filtering on the history ledger.** Cost, proceeds and dates of acquisition are queryable
  through the service but are not yet filter targets.
