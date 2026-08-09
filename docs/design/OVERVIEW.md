# Plain-English overview

A non-technical companion to the design documents. Same decisions, no SQL. If something here and in
the technical documents disagree, the technical documents are correct and this file needs fixing.

---

## What we are building

A desktop program for cataloguing a coin collection. It runs on your own computer, keeps everything
in a folder you control, and works with no internet connection. Nothing is uploaded anywhere.

It is built in stages. The first stage is the database: a place to record coins, with columns you
define yourself, that sorts and filters properly. Everything else — labels, albums, wishlists,
imports — is built on top of that, and each stage is useful on its own.

Your existing label generator is not being thrown away. It becomes one part of the program, and its
current behaviour is preserved: the four corners of text, the flag images, the cutout circles, and the
separate front and back offsets for badly punched 2×2 holders.

---

## How your collection is organised

**Every coin has an ID.** A plain number, assigned automatically, which you can change to anything
you like as long as no other coin is using it. If the number you want belongs to a coin sitting in
the Trash, the program asks first and then lets you have it — and if you later restore that coin it
is given a new number, because its data matters more than its number does.

**One row is one coin.** That is the whole model. There is no hidden "coin type" concept behind the
scenes, no catalogue of things-that-exist that your coins have to be attached to. If you own three of
the same coin, that is three rows, each with its own grade, price, photo and notes.

**Coins can be moved between subcollections** at any time, one at a time by typing in the
Subcollection column or several at once with *Move to…*. Nothing is lost in the move: values the
destination does not display are kept, and moving the coin back brings them into view again.

**Subcollections are like separate spreadsheet tabs.** You might have *Modern*, *Ancients* and
*Chinese Cash*. Each one can have completely different columns. You can look at one at a time, or look
at everything together in a master view.

**You build your own columns.** The program offers types — text, number, weight, measurement, date,
money, purity, a star rating, a calculated value — and you create whatever columns
you want from them. Nothing is fixed and nothing is undeletable. If you dislike a column in the
starting setup, delete it.

### The clever bit about the master view

Say your *Modern* tab calls a column **Ruler** and your *Ancients* tab calls it **Emperor**, but you
want them to be one column when you look at everything together.

You make **one** column, and give it a different display name in each subcollection. In *Modern* it
reads *Ruler*, in *Ancients* it reads *Emperor*, and in the master view it is a single column because
it is genuinely the same column underneath. It works the other way too: two columns that happen to
have similar names stay separate, because they are separate.

### Removing a column does not destroy anything

Taking a column out of a subcollection just hides it there. Deleting a column entirely archives it by
default — the data stays, and one click brings it back. Permanently destroying data is a separate
action that tells you exactly how many values you are about to lose and offers to export them first.

The reasoning: you are expected to rearrange your own setup constantly, so that has to be safe rather
than frightening.

---

## Sorting, and the thing your spreadsheet already got right

In your spreadsheet you kept two columns for dates: one showing `1736–1795` or `c. 350 BC`, and a
second holding a plain number so that clicking *sort* actually worked.

That approach is correct. The program does the same thing, but as **one** column instead of two you
have to keep in step by hand. Every date and text value can carry a hidden sort number:

| You type | It sorts at | What happens |
|---|---|---|
| `1943` | 1943 | used straight away, no fuss |
| `1736-1795` | 1765.5 | *"Looks like a range — sort it in the middle?"* |
| `c. 350 BC` | −350 | recognised, sorted before year 1 |
| `AH 1256` | 1840.6 | converted, and it tells you it converted |
| `Qianlong year 22` | — | *"I can't read this. What number should it sort at?"* |
| `undated` | — | sorts at the end |

Three rules:

1. If it is obvious, it just works and does not interrupt you.
2. If it guessed or converted, **it says so** and lets you confirm.
3. If it has no idea, it asks. You can right-click any cell and change the sort number at any time,
   and once you set one by hand the program never overwrites it.

The same mechanism handles denominations, which have the same problem. `wen`, `cash`, `mace`, `tael`
and anything else you invent will sort in the order you decide:

```
1 wen         1
10 wen       10
50 wen       50
100 cash    100
1 mace     1000
half tael 18650
```

---

## The four things that are not ordinary columns

Catalogue numbers, grades, certifications and external links each get their own special handling,
because each one can appear several times on a single coin and each needs its own sorting rules. You
can still position them anywhere in your layout and label them what you like.

**None of these come pre-loaded.** No catalogues, no grading companies, no grading scales. You add
the ones you actually use.

### Catalogue numbers

A coin can have as many as you like, from as many catalogues as you like.

You choose how to see them: every number in one cell, only the ones from one catalogue (making it a
*Hartill* column), or just the one you ranked first. Crucially, **a column narrowed to one catalogue
sorts and filters by that catalogue**, and you can drop the repeated `H` prefix so it reads `1.01`
rather than `H 1.01` on every row. You can filter for "is in Hartill" or "Hartill number between
22.100 and 22.199".

Numbers sort the way a catalogue actually reads, not the way a computer sorts text. `2` comes before
`10`, `1042a` follows `1042`, and `A54` sits next to `54` instead of being flung into the
fifty-thousands.

### Grades

The difficult part is that grades come in incompatible languages: `MS63` from a grading company, a
plain `AU` from a dealer, `8` on the Chinese 1–10 scale, a CAC sticker on top of a grade, and
"details" grades for problem coins.

You define the scales yourself. For each scale you say where its grades sit on one shared ruler, and
after that everything sorts together:

```
MS63 CAC gold
MS63 CAC green
MS63
MS63 Details      <- Cleaned
MS62
AU
AU Details        <- Scratches
8    (Chinese scale)
6    (Chinese scale)
VF
```

Note where the details grades land: **immediately beside their base grade**, not dumped at the
bottom. `MS63 Details` sits between MS62 and MS63, exactly as you asked. Stickers nudge a coin
slightly up within its grade.

"Show me everything VF or better" then works across all three scales at once, and "hide problem coins"
is a single tick-box.

What the actual problem is — *Cleaned*, *Scratches* — can go in its own separate column, or be shown
in brackets after the grade. Your choice per view.

A coin can hold more than one grade at once, so a dealer's optimistic opinion and your own can sit
side by side.

### Certifications

Multiple current certifications on one coin are supported, because that is now normal — a grading
company's slab plus a separate endorsement sticker.

Grading history is properly tracked, which matters when cracking coins out is routine. The program
keeps the chain: this coin was graded in 2019, cracked out, regraded in 2024, and here is the trail.
The current grade is still a single simple lookup.

### External links

If a coin is already documented somewhere else — a Zeno record, a grading company's verification
page, an auction archive lot, a published paper — you can attach as many links as you like, each
labelled and categorised.

---

## Coins you no longer own

Selling a coin should not mean losing it from your records. Marking one as **sold** keeps
everything — its values, photographs, catalogue numbers, grades and history — and simply stops
listing it among what you own.

*View → Show sold and disposed* brings them back into the grid, in italics so they are obviously
different from the rest. The same applies to coins traded, given away, lost or stolen.

This is deliberately separate from the **Trash**, which is for mistakes. A sold coin is not a
mistake; it is part of your collection's history.

## Buying, selling and history

Every coin has a history log: bought, sold, valued, sent for grading, moved, and so on. Each entry can
record a price, plus fees and postage.

The log is **append-only**: entries are never edited or deleted. If you make a mistake, you void the
entry and add a corrected one, so the trail always shows what happened including the correction. For
a collection built up over decades, that is the difference between financial history you can trust and
numbers that might have been quietly changed.

Profit and loss is worked out from the log rather than typed in. One detail worth mentioning because
we got it wrong at first: fees and postage **add** to what a coin cost you but **come off** what you
received when selling it. The first version added them in both directions, which in testing
overstated a profit as £188.75 when it was really £126.75. It now handles the direction correctly.

Sold coins stay in the database forever with their full story, just filtered out of your
current-holdings view.

---

## Catalogue numbers, grades, certifications and links

These four do not fit in a single cell, because a coin can have several of each. They live in a
panel beside the grid, showing whichever coin you have selected, with Add and Remove for each.

The grid can also show them as columns, giving a one-line summary — the main catalogue number, the
grade you marked, the current certification, how many links there are. Add those columns through
*Columns… → Add built-in list*.

Since the program ships with no catalogues, grading companies or grading scales, every one of those
dialogs can create what it needs as you go. Adding your first catalogue number will offer to define
the catalogue; adding your first grade will offer to define the scale and where each grade sits on
it. A blank slate should be a clean start, not a dead end.

## "As easy as Excel to edit" — the interface

This is a real requirement, so it shapes the choice of toolkit rather than being decoration.

**The base is PySide6, the official Python binding for Qt.** Its table widget is explicitly designed
as a spreadsheet-style grid, and it stays fast with very large tables because it only draws the rows
currently on screen. Editing is a standard, well-documented part of it — the underlying model declares
which cells are editable and handles values being changed
([PySide6 table editing guide](https://www.pythonguis.com/faq/editing-pyside6-tableview/)).

**This is now built.** See the screenshots in the [repository README](../../README.md). Everything in
the table below works, and the interface tests run Qt headlessly so they need no screen.

What "spreadsheet-easy" means concretely:

| Behaviour | Notes |
|---|---|
| Type directly into cells | No dialog box between you and your data |
| Tab and arrow keys move around | Enter commits and moves down, like a spreadsheet |
| Copy and paste blocks of cells | Including to and from Excel, since both use tab-separated text |
| Fill down / fill series | Select a column range and apply |
| Multi-cell and multi-row editing | Change one field across 40 selected coins in one action |
| Undo and redo everything | Qt provides an undo stack built for exactly this ([Qt undo framework](https://doc.qt.io/qtforpython-6.10/overviews/qtdoc-qundo.html)) |
| Sort by clicking a column header | Using the sort numbers described above |
| Freeze and reorder columns, hide columns | Per view, saved |
| Bulk add | Create many coins at once from one filled-in form |
| Editable ID, Name and Subcollection | Every coin is numbered automatically; type another subcollection's name to move it |

Two practical notes carried over from what is known about this toolkit: automatic column resizing is
the usual cause of sluggish large tables and will be avoided in favour of fixed or user-set widths;
and undo has a known pitfall where an edit can be applied twice if wired naïvely, so edits go through
the undo system rather than being applied directly and then recorded.

Because there is no coin-type layer, shared data is not filled in once and inherited. **Bulk add and
bulk edit are what replace that**, so they are part of the first build rather than a later
convenience.

*Content from the linked sources was rephrased for compliance with licensing restrictions.*

---

## Narrowing it down

Searching, filtering and sorting are one question, not three, because that is how they are actually
asked: *Qianlong cash, graded, heaviest first.*

**Filtering** is a list of tests — a column, something to ask of it, a value — matched with *all* or
*any* of them. What you can ask depends on the column: a date column offers "is in the decade of", a
weight column offers "is at least", a notes column offers only "contains". You never have to
remember which; only the sensible options are offered.

When a flat list is not enough, a group covers the rest: *bronze, and either Qianlong or Jiaqing*.
That question cannot be written as a list of conditions at all, and it is an ordinary thing to want.

One deliberate choice worth knowing: **"metal is not bronze" does not include coins with no metal
recorded.** A coin that records nothing makes no claim about its metal, and counting it would fill a
narrowing filter with blank rows. If you do want the blanks as well, you ask for it — *any of: metal
is empty, metal is not bronze* — and it reads as exactly what it is.

**Sorting** stacks. Click a header to sort by it; Ctrl-click a second to sort by that *within* the
first, so *country, then date* is two clicks. Blanks always sort to the bottom, whichever direction
you choose, because a blank is not "smaller than everything" — it is missing.

Grades sort by the number they work out to, which is the whole reason grades are typed with a value
rather than chosen from a list: `MS63` can be compared with `gVF`, and `AU Details` sits immediately
below `AU` instead of at the bottom of the collection.

**Saved views** remember a filter and a sort order under a name. A view can also just be a list of
particular coins — *ID is any of 3, 7, 11* — which is the answer to "I want this handful of coins
grouped together" without a coin needing to belong to two subcollections at once.

Whenever a filter is on, the status bar says what is being hidden. A filtered grid otherwise looks
exactly like a collection that has lost data.

---

## What we are deliberately leaving until later

| Left for later | Why it is safe to wait |
|---|---|
| Photographs | Arrives with virtual albums; nothing in the database depends on it, so adding it later only adds new tables |
| Where a coin is physically stored (safe, box, tray) | Also arrives with albums, since the album *is* the picture of your storage. For now a plain column records it |
| Previous owners / pedigree chains | Not needed for the base |
| Multiple currencies | One currency per collection for now; adding more later does not disturb anything |
| Custom date systems you define yourself | The groundwork is in place; the feature comes later |

Every one of these is an *addition* later, not a rebuild. That was the test used to decide what could
safely wait.

---

## Order of work

1. **The database** — coins, your own columns, subcollections, bulk add and edit, history log. ✅
2. **Sorting, searching and filtering** — advanced sorting, saved views, smart filters. Treated as
   equally important to the database itself, since a collection you cannot slice is just a list. ✅
   *(Grouping and per-view column sets are the parts still outstanding.)*
3. **Import and export** — spreadsheets in and out, then Numista: you paste in a list of Numista
   IDs, choose which of their fields map onto which of your columns, and they come in as coins.
4. **Wishlist** — the list of what you are hunting, including "any one of these catalogue numbers
   fills this gap".
5. **Labels** — your generator, driven from the database, with saved label layouts and the per-holder
   front/back offsets.
6. **Virtual albums** — the on-screen picture of your physical pages and holders, plus photographs.

---

## Decisions now settled

| Question | Answer |
|---|---|
| Starting content | Completely blank. Test builds contain no example catalogues, scales or columns |
| Bulk lots | One row is one coin. Adding 47 coins creates 47 rows in one action; your total says 47 |
| Details grades | Sort immediately **below** their base grade |
| Multiple grades | You choose which one is the headline grade. The program never guesses |
| Combined catalogue column | Coins with no number in the catalogue you sorted by go to the **bottom** |
| Remembered dropdown entries | Dropped for now. Ordinary text columns, filtered as text |
| Fixed dropdown lists | Also dropped for now. Every column is plain text; to be revisited |

Two small things left open, neither blocking:

1. **The shared grading ruler** currently uses numbers resembling 1–70, because that made the example
   readable. It could just as easily be an abstract 0–100 so no standard looks privileged.
