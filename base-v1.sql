-- Base v1 schema — normative DDL for documents 01 and 02.
--
-- This file is the single source of truth for the base schema. The markdown
-- specifications explain and justify it; they do not restate it. The SQLAlchemy
-- models must produce an equivalent schema, and the first Alembic migration
-- creates exactly this.
--
-- Verified against SQLite 3.40. Run:  sqlite3 :memory: ".read base-v1.sql"
--
-- Conventions (see 01 Part 0):
--   * every entity has an INTEGER PRIMARY KEY plus an application-supplied uuid
--   * timestamps are UTC ISO-8601 text, maintained by the application
--   * money is INTEGER minor units in the single library currency
--   * mass is REAL grams, length is REAL millimetres, fineness is REAL per mille
--   * uniqueness over a nullable column always uses a partial index, because
--     SQLite treats NULLs as distinct in unique indexes
--   * field values attach to a specimen only; there is no coin-type layer

PRAGMA foreign_keys = ON;


-- ---------------------------------------------------------------------------
-- 1. Library and subcollections
-- ---------------------------------------------------------------------------

CREATE TABLE library_meta (
  id                      INTEGER PRIMARY KEY CHECK (id = 1),
  library_uuid            TEXT NOT NULL UNIQUE,
  schema_version          TEXT NOT NULL,
  app_version_created     TEXT NOT NULL DEFAULT '',
  app_version_last_opened TEXT NULL,
  currency_symbol         TEXT NOT NULL DEFAULT '$',
  currency_code           TEXT NOT NULL DEFAULT 'USD',
  currency_decimals       INTEGER NOT NULL DEFAULT 2,
  length_display_unit     TEXT NOT NULL DEFAULT 'mm',
  mass_display_unit       TEXT NOT NULL DEFAULT 'g',
  settings_json           TEXT NOT NULL DEFAULT '{}',
  created_at              TEXT NOT NULL,
  updated_at              TEXT NOT NULL
);

CREATE TABLE subcollection (
  id              INTEGER PRIMARY KEY,
  uuid            TEXT NOT NULL UNIQUE,
  name            TEXT NOT NULL,
  slug            TEXT NOT NULL UNIQUE,
  description     TEXT NULL,
  colour          TEXT NULL,
  naming_template TEXT NOT NULL DEFAULT '',
  sort_order      INTEGER NOT NULL DEFAULT 0,
  is_archived     INTEGER NOT NULL DEFAULT 0,
  created_at      TEXT NOT NULL,
  updated_at      TEXT NOT NULL
);
CREATE INDEX ix_subcollection_order ON subcollection(sort_order, name);


-- ---------------------------------------------------------------------------
-- 2. Field system
--    Field definitions are library-wide. A subcollection opts in to a field
--    through subcollection_block, which also carries its per-subcollection
--    display label. Two subcollections sharing one definition therefore merge
--    into a single column in the master view automatically.
-- ---------------------------------------------------------------------------

CREATE TABLE field_group (
  id                INTEGER PRIMARY KEY,
  uuid              TEXT NOT NULL UNIQUE,
  key               TEXT NOT NULL UNIQUE,
  label             TEXT NOT NULL,
  sort_order        INTEGER NOT NULL DEFAULT 0,
  collapsed_default INTEGER NOT NULL DEFAULT 0,
  created_at        TEXT NOT NULL,
  updated_at        TEXT NOT NULL
);

CREATE TABLE field_definition (
  id                 INTEGER PRIMARY KEY,
  uuid               TEXT NOT NULL UNIQUE,
  key                TEXT NOT NULL UNIQUE,   -- immutable machine key
  label              TEXT NOT NULL,          -- canonical / master-view label
  kind               TEXT NOT NULL DEFAULT 'value' CHECK (kind IN ('value','computed')),
  data_type          TEXT NOT NULL,          -- registry key, see document 02
  config_json        TEXT NOT NULL DEFAULT '{}',
  help_text          TEXT NULL,
  is_multi           INTEGER NOT NULL DEFAULT 0,
  is_archived        INTEGER NOT NULL DEFAULT 0,
  default_value_json TEXT NULL,
  origin_preset      TEXT NULL,
  created_at         TEXT NOT NULL,
  updated_at         TEXT NOT NULL
);
CREATE INDEX ix_field_active ON field_definition(is_archived, key);

-- Which fields and special blocks a subcollection shows, in what order,
-- under what label. block_kind <> 'field' rows position the special systems
-- (catalogues, grades, certifications, links, history) in the same layout.
CREATE TABLE subcollection_block (
  id                  INTEGER PRIMARY KEY,
  uuid                TEXT NOT NULL UNIQUE,
  subcollection_id    INTEGER NOT NULL REFERENCES subcollection(id) ON DELETE CASCADE,
  block_kind          TEXT NOT NULL CHECK (block_kind IN
                        ('field','catalogues','grades','certifications','links','history')),
  field_definition_id INTEGER NULL REFERENCES field_definition(id) ON DELETE CASCADE,
  display_label       TEXT NULL,             -- NULL = use field_definition.label
  group_id            INTEGER NULL REFERENCES field_group(id) ON DELETE SET NULL,
  sort_order          INTEGER NOT NULL DEFAULT 0,
  show_in_table       INTEGER NOT NULL DEFAULT 0,
  is_required         INTEGER NOT NULL DEFAULT 0,
  config_json         TEXT NOT NULL DEFAULT '{}',
  created_at          TEXT NOT NULL,
  updated_at          TEXT NOT NULL,
  CHECK ((block_kind = 'field') = (field_definition_id IS NOT NULL))
);
CREATE UNIQUE INDEX ux_block_field   ON subcollection_block(subcollection_id, field_definition_id)
  WHERE field_definition_id IS NOT NULL;
CREATE UNIQUE INDEX ux_block_special ON subcollection_block(subcollection_id, block_kind)
  WHERE field_definition_id IS NULL;
CREATE INDEX ix_block_order ON subcollection_block(subcollection_id, sort_order);


-- ---------------------------------------------------------------------------
-- 3. Specimens. One row per specimen; there is no coin-type entity.
-- ---------------------------------------------------------------------------

CREATE TABLE specimen (
  id               INTEGER PRIMARY KEY,
  uuid             TEXT NOT NULL UNIQUE,
  subcollection_id INTEGER NOT NULL REFERENCES subcollection(id) ON DELETE RESTRICT,
  display_name     TEXT NOT NULL DEFAULT '',
  inventory_code   TEXT NULL,
  status           TEXT NOT NULL DEFAULT 'owned' CHECK (status IN
                     ('owned','ordered','sold','traded','gifted','lost','stolen',
                      'returned','on_loan','wanted')),
  quantity         INTEGER NOT NULL DEFAULT 1 CHECK (quantity >= 0),
  is_favourite     INTEGER NOT NULL DEFAULT 0,
  created_at       TEXT NOT NULL,
  updated_at       TEXT NOT NULL,
  deleted_at       TEXT NULL                 -- soft delete, retained indefinitely
);
CREATE INDEX ix_specimen_sub  ON specimen(subcollection_id, status) WHERE deleted_at IS NULL;
CREATE INDEX ix_specimen_name ON specimen(display_name COLLATE NOCASE);
CREATE UNIQUE INDEX ux_specimen_inv ON specimen(inventory_code) WHERE inventory_code IS NOT NULL;


-- ---------------------------------------------------------------------------
-- 4. Typed field values.
--    sort_value is the "sort key companion": an optional numeric ordering key
--    the application proposes and the user may override, so inconsistently
--    formatted text (dates, denominations) still sorts numerically.
-- ---------------------------------------------------------------------------

CREATE TABLE field_value_text (
  id                  INTEGER PRIMARY KEY,
  field_definition_id INTEGER NOT NULL REFERENCES field_definition(id) ON DELETE CASCADE,
  specimen_id         INTEGER NOT NULL REFERENCES specimen(id) ON DELETE CASCADE,
  seq                 INTEGER NOT NULL DEFAULT 0,
  value               TEXT NOT NULL,
  sort_value          REAL NULL,
  sort_source         TEXT NOT NULL DEFAULT 'none' CHECK (sort_source IN ('none','auto','manual')),
  needs_review        INTEGER NOT NULL DEFAULT 0
);
CREATE UNIQUE INDEX ux_fvtext     ON field_value_text(field_definition_id, specimen_id, seq);
CREATE INDEX        ix_fvtext_val ON field_value_text(field_definition_id, value COLLATE NOCASE);
CREATE INDEX        ix_fvtext_sort ON field_value_text(field_definition_id, sort_value);
CREATE INDEX        ix_fvtext_spec ON field_value_text(specimen_id);

CREATE TABLE field_value_number (
  id                  INTEGER PRIMARY KEY,
  field_definition_id INTEGER NOT NULL REFERENCES field_definition(id) ON DELETE CASCADE,
  specimen_id         INTEGER NOT NULL REFERENCES specimen(id) ON DELETE CASCADE,
  seq                 INTEGER NOT NULL DEFAULT 0,
  value               REAL NOT NULL,         -- canonical unit for the data_type
  entered_as          TEXT NULL,             -- '22K', '1/2 in', '420 gr' as typed
  is_approximate      INTEGER NOT NULL DEFAULT 0
);
CREATE UNIQUE INDEX ux_fvnum      ON field_value_number(field_definition_id, specimen_id, seq);
CREATE INDEX        ix_fvnum_val  ON field_value_number(field_definition_id, value);
CREATE INDEX        ix_fvnum_spec ON field_value_number(specimen_id);

CREATE TABLE field_value_money (
  id                  INTEGER PRIMARY KEY,
  field_definition_id INTEGER NOT NULL REFERENCES field_definition(id) ON DELETE CASCADE,
  specimen_id         INTEGER NOT NULL REFERENCES specimen(id) ON DELETE CASCADE,
  seq                 INTEGER NOT NULL DEFAULT 0,
  amount_minor        INTEGER NOT NULL,
  as_of               TEXT NULL              -- valuations are dated
);
CREATE UNIQUE INDEX ux_fvmoney      ON field_value_money(field_definition_id, specimen_id, seq);
CREATE INDEX        ix_fvmoney_val  ON field_value_money(field_definition_id, amount_minor);
CREATE INDEX        ix_fvmoney_spec ON field_value_money(specimen_id);

CREATE TABLE field_value_date (
  id                  INTEGER PRIMARY KEY,
  field_definition_id INTEGER NOT NULL REFERENCES field_definition(id) ON DELETE CASCADE,
  specimen_id         INTEGER NOT NULL REFERENCES specimen(id) ON DELETE CASCADE,
  seq                 INTEGER NOT NULL DEFAULT 0,
  display             TEXT NOT NULL,         -- exactly as the user expressed it
  year_start          INTEGER NULL,          -- signed; negative = BC (no year zero)
  year_end            INTEGER NULL,
  month_start         INTEGER NULL,
  day_start           INTEGER NULL,
  month_end           INTEGER NULL,
  day_end             INTEGER NULL,
  precision           TEXT NOT NULL DEFAULT 'exact_year' CHECK (precision IN
                        ('exact_day','exact_month','exact_year','range','decade',
                         'century','circa','unknown')),
  calendar            TEXT NOT NULL DEFAULT 'gregorian',
  era_label           TEXT NULL,             -- 'AH', 'Qianlong 22', 'Meiji 3'
  sort_value          REAL NULL,
  sort_source         TEXT NOT NULL DEFAULT 'none' CHECK (sort_source IN ('none','auto','manual')),
  needs_review        INTEGER NOT NULL DEFAULT 0
);
CREATE UNIQUE INDEX ux_fvdate      ON field_value_date(field_definition_id, specimen_id, seq);
CREATE INDEX        ix_fvdate_sort ON field_value_date(field_definition_id, sort_value);
CREATE INDEX        ix_fvdate_span ON field_value_date(field_definition_id, year_start, year_end);
CREATE INDEX        ix_fvdate_spec ON field_value_date(specimen_id);

CREATE TABLE field_value_bool (
  id                  INTEGER PRIMARY KEY,
  field_definition_id INTEGER NOT NULL REFERENCES field_definition(id) ON DELETE CASCADE,
  specimen_id         INTEGER NOT NULL REFERENCES specimen(id) ON DELETE CASCADE,
  seq                 INTEGER NOT NULL DEFAULT 0,
  value               INTEGER NOT NULL CHECK (value IN (0,1))
);
CREATE UNIQUE INDEX ux_fvbool      ON field_value_bool(field_definition_id, specimen_id, seq);
CREATE INDEX        ix_fvbool_spec ON field_value_bool(specimen_id);

CREATE TABLE field_option (
  id                  INTEGER PRIMARY KEY,
  uuid                TEXT NOT NULL UNIQUE,
  field_definition_id INTEGER NOT NULL REFERENCES field_definition(id) ON DELETE CASCADE,
  parent_id           INTEGER NULL REFERENCES field_option(id) ON DELETE CASCADE,
  value_key           TEXT NOT NULL,
  label               TEXT NOT NULL,
  sort_order          INTEGER NOT NULL DEFAULT 0,
  sort_value          REAL NULL,             -- explicit numeric order, e.g. denominations
  colour              TEXT NULL,
  is_archived         INTEGER NOT NULL DEFAULT 0,
  created_at          TEXT NOT NULL,
  updated_at          TEXT NOT NULL
);
CREATE UNIQUE INDEX ux_field_option ON field_option(field_definition_id, value_key);

CREATE TABLE field_value_option (
  id                  INTEGER PRIMARY KEY,
  field_definition_id INTEGER NOT NULL REFERENCES field_definition(id) ON DELETE CASCADE,
  specimen_id         INTEGER NOT NULL REFERENCES specimen(id) ON DELETE CASCADE,
  seq                 INTEGER NOT NULL DEFAULT 0,
  field_option_id     INTEGER NOT NULL REFERENCES field_option(id) ON DELETE RESTRICT
);
CREATE UNIQUE INDEX ux_fvopt      ON field_value_option(field_definition_id, specimen_id, seq);
CREATE INDEX        ix_fvopt_spec ON field_value_option(specimen_id);
CREATE INDEX        ix_fvopt_opt  ON field_value_option(field_option_id);

CREATE TABLE field_value_json (
  id                  INTEGER PRIMARY KEY,
  field_definition_id INTEGER NOT NULL REFERENCES field_definition(id) ON DELETE CASCADE,
  specimen_id         INTEGER NOT NULL REFERENCES specimen(id) ON DELETE CASCADE,
  seq                 INTEGER NOT NULL DEFAULT 0,
  value               TEXT NOT NULL          -- display only; never sorted or filtered
);
CREATE UNIQUE INDEX ux_fvjson      ON field_value_json(field_definition_id, specimen_id, seq);
CREATE INDEX        ix_fvjson_spec ON field_value_json(specimen_id);


-- ---------------------------------------------------------------------------
-- 5. Catalogues. Ships empty; the user creates every catalogue.
-- ---------------------------------------------------------------------------

CREATE TABLE catalog (
  id                  INTEGER PRIMARY KEY,
  uuid                TEXT NOT NULL UNIQUE,
  code                TEXT NOT NULL UNIQUE,  -- 'KM', 'H', 'FD', 'RIC'
  name                TEXT NOT NULL,
  publisher           TEXT NULL,
  edition             TEXT NULL,
  year                INTEGER NULL,
  scope               TEXT NULL,
  url_template        TEXT NULL,
  number_pattern      TEXT NULL,
  sort_strategy       TEXT NOT NULL DEFAULT 'prefix_aware'
                        CHECK (sort_strategy IN ('prefix_aware','numeric','lexical')),
  letter_prefix_order TEXT NOT NULL DEFAULT 'after'
                        CHECK (letter_prefix_order IN ('after','before')),
  sort_order          INTEGER NOT NULL DEFAULT 0,
  is_archived         INTEGER NOT NULL DEFAULT 0,
  notes               TEXT NULL,
  created_at          TEXT NOT NULL,
  updated_at          TEXT NOT NULL
);

CREATE TABLE catalog_reference (
  id            INTEGER PRIMARY KEY,
  uuid          TEXT NOT NULL UNIQUE,
  catalog_id    INTEGER NOT NULL REFERENCES catalog(id) ON DELETE RESTRICT,
  specimen_id   INTEGER NOT NULL REFERENCES specimen(id) ON DELETE CASCADE,
  number_raw    TEXT NOT NULL,               -- as entered
  number_norm   TEXT NOT NULL,               -- matching key
  sort_segments TEXT NOT NULL,               -- ordering and range key
  segments_json TEXT NOT NULL DEFAULT '{}',
  qualifier     TEXT NULL,                   -- 'var.', 'cf.'
  certainty     TEXT NOT NULL DEFAULT 'certain'
                  CHECK (certainty IN ('certain','probable','cf','disputed')),
  is_primary    INTEGER NOT NULL DEFAULT 0,
  url           TEXT NULL,
  notes         TEXT NULL,
  created_at    TEXT NOT NULL,
  updated_at    TEXT NOT NULL
);
CREATE UNIQUE INDEX ux_catref      ON catalog_reference(specimen_id, catalog_id, number_norm);
CREATE UNIQUE INDEX ux_catref_prim ON catalog_reference(specimen_id) WHERE is_primary = 1;
CREATE INDEX        ix_catref_sort ON catalog_reference(catalog_id, sort_segments);
CREATE INDEX        ix_catref_spec ON catalog_reference(specimen_id);
CREATE INDEX        ix_catref_norm ON catalog_reference(catalog_id, number_norm);


-- ---------------------------------------------------------------------------
-- 6. Grading. Scales, their ordered levels, and modifiers are all user data.
--    normalised is a shared numeric axis that makes different standards
--    comparable; the user controls every value on it.
-- ---------------------------------------------------------------------------

CREATE TABLE grade_scale (
  id          INTEGER PRIMARY KEY,
  uuid        TEXT NOT NULL UNIQUE,
  code        TEXT NOT NULL UNIQUE,
  name        TEXT NOT NULL,
  kind        TEXT NOT NULL DEFAULT 'ordinal' CHECK (kind IN ('numeric','ordinal')),
  notes       TEXT NULL,
  sort_order  INTEGER NOT NULL DEFAULT 0,
  is_archived INTEGER NOT NULL DEFAULT 0,
  created_at  TEXT NOT NULL,
  updated_at  TEXT NOT NULL
);

CREATE TABLE grade_level (
  id             INTEGER PRIMARY KEY,
  uuid           TEXT NOT NULL UNIQUE,
  grade_scale_id INTEGER NOT NULL REFERENCES grade_scale(id) ON DELETE CASCADE,
  label          TEXT NOT NULL,              -- 'MS63', 'AU', '8'
  aliases        TEXT NULL,                  -- 'MS-63|MS 63|Mint State 63'
  numeric_value  REAL NULL,
  normalised     REAL NOT NULL,              -- shared comparison axis
  sort_order     INTEGER NOT NULL DEFAULT 0,
  created_at     TEXT NOT NULL,
  updated_at     TEXT NOT NULL
);
CREATE UNIQUE INDEX ux_grade_level  ON grade_level(grade_scale_id, label);
CREATE INDEX        ix_grade_level_n ON grade_level(normalised);

CREATE TABLE grade_modifier (
  id               INTEGER PRIMARY KEY,
  uuid             TEXT NOT NULL UNIQUE,
  code             TEXT NOT NULL UNIQUE,
  label            TEXT NOT NULL,
  kind             TEXT NOT NULL CHECK (kind IN ('detail','sticker','qualifier','strike')),
  normalised_delta REAL NOT NULL DEFAULT 0,  -- keeps 'AU Details' beside 'AU'
  colour           TEXT NULL,
  notes            TEXT NULL,
  created_at       TEXT NOT NULL,
  updated_at       TEXT NOT NULL
);

CREATE TABLE specimen_grade (
  id             INTEGER PRIMARY KEY,
  uuid           TEXT NOT NULL UNIQUE,
  specimen_id    INTEGER NOT NULL REFERENCES specimen(id) ON DELETE CASCADE,
  grade_scale_id INTEGER NULL REFERENCES grade_scale(id) ON DELETE SET NULL,
  grade_level_id INTEGER NULL REFERENCES grade_level(id) ON DELETE SET NULL,
  raw_text       TEXT NOT NULL,              -- exactly as entered
  normalised     REAL NULL,                  -- level normalised + modifier deltas
  detail_note    TEXT NULL,                  -- 'Cleaned', 'Scratches'
  source         TEXT NOT NULL DEFAULT 'self'
                   CHECK (source IN ('self','seller','tpg','auction','other')),
  assigned_by    TEXT NULL,                  -- 'NGC', 'me', dealer name
  assigned_on    TEXT NULL,
  is_primary     INTEGER NOT NULL DEFAULT 0,
  notes          TEXT NULL,
  created_at     TEXT NOT NULL,
  updated_at     TEXT NOT NULL
);
CREATE UNIQUE INDEX ux_grade_primary ON specimen_grade(specimen_id) WHERE is_primary = 1;
CREATE INDEX        ix_grade_norm    ON specimen_grade(normalised);
CREATE INDEX        ix_grade_spec    ON specimen_grade(specimen_id);

CREATE TABLE specimen_grade_modifier (
  specimen_grade_id INTEGER NOT NULL REFERENCES specimen_grade(id) ON DELETE CASCADE,
  grade_modifier_id INTEGER NOT NULL REFERENCES grade_modifier(id) ON DELETE RESTRICT,
  PRIMARY KEY (specimen_grade_id, grade_modifier_id)
) WITHOUT ROWID;
CREATE INDEX ix_sgm_mod ON specimen_grade_modifier(grade_modifier_id);


-- ---------------------------------------------------------------------------
-- 7. Certification. Ships empty. Several certifications may be current at once
--    (for example a TPG grade plus a third-party endorsement sticker), so
--    'current' is not unique; only is_primary is.
-- ---------------------------------------------------------------------------

CREATE TABLE grading_company (
  id                INTEGER PRIMARY KEY,
  uuid              TEXT NOT NULL UNIQUE,
  code              TEXT NOT NULL UNIQUE,
  name              TEXT NOT NULL,
  cert_url_template TEXT NULL,
  default_scale_id  INTEGER NULL REFERENCES grade_scale(id) ON DELETE SET NULL,
  specialism        TEXT NULL,
  sort_order        INTEGER NOT NULL DEFAULT 0,
  is_archived       INTEGER NOT NULL DEFAULT 0,
  notes             TEXT NULL,
  created_at        TEXT NOT NULL,
  updated_at        TEXT NOT NULL
);

CREATE TABLE certification (
  id                 INTEGER PRIMARY KEY,
  uuid               TEXT NOT NULL UNIQUE,
  specimen_id        INTEGER NOT NULL REFERENCES specimen(id) ON DELETE CASCADE,
  grading_company_id INTEGER NOT NULL REFERENCES grading_company(id) ON DELETE RESTRICT,
  cert_number        TEXT NULL,              -- nullable: some endorsements have none
  specimen_grade_id  INTEGER NULL REFERENCES specimen_grade(id) ON DELETE SET NULL,
  holder_type        TEXT NULL,
  label_variety      TEXT NULL,              -- 'old green holder', 'first releases'
  graded_on          TEXT NULL,
  status             TEXT NOT NULL DEFAULT 'current' CHECK (status IN
                       ('current','pending','cracked_out','crossed_over','regraded','superseded')),
  supersedes_id      INTEGER NULL REFERENCES certification(id) ON DELETE SET NULL,
  is_primary         INTEGER NOT NULL DEFAULT 0,
  population_note    TEXT NULL,
  verification_url   TEXT NULL,
  verified_at        TEXT NULL,
  notes              TEXT NULL,
  created_at         TEXT NOT NULL,
  updated_at         TEXT NOT NULL
);
CREATE UNIQUE INDEX ux_cert_primary ON certification(specimen_id) WHERE is_primary = 1;
CREATE INDEX        ix_cert_spec    ON certification(specimen_id, status);
CREATE INDEX        ix_cert_number  ON certification(grading_company_id, cert_number);


-- ---------------------------------------------------------------------------
-- 8. External links
-- ---------------------------------------------------------------------------

CREATE TABLE external_link (
  id          INTEGER PRIMARY KEY,
  uuid        TEXT NOT NULL UNIQUE,
  specimen_id INTEGER NOT NULL REFERENCES specimen(id) ON DELETE CASCADE,
  kind        TEXT NOT NULL DEFAULT 'other' CHECK (kind IN
                ('zeno','numista','grading','auction','dealer','paper','forum',
                 'museum','image','other')),
  label       TEXT NULL,
  url         TEXT NOT NULL,
  reference   TEXT NULL,                     -- record id, lot number, page
  notes       TEXT NULL,
  sort_order  INTEGER NOT NULL DEFAULT 0,
  created_at  TEXT NOT NULL,
  updated_at  TEXT NOT NULL
);
CREATE INDEX ix_link_spec ON external_link(specimen_id, sort_order);


-- ---------------------------------------------------------------------------
-- 9. History ledger. Append-only: rows are never updated except to void them.
--    Amounts are minor units of the single library currency.
-- ---------------------------------------------------------------------------

CREATE TABLE specimen_event (
  id                 INTEGER PRIMARY KEY,
  uuid               TEXT NOT NULL UNIQUE,
  specimen_id        INTEGER NOT NULL REFERENCES specimen(id) ON DELETE CASCADE,
  event_type         TEXT NOT NULL CHECK (event_type IN
                       ('acquired','ordered','received','sold','listed','traded_in','traded_out',
                        'gifted_in','gifted_out','valued','graded_sent','graded_returned',
                        'moved','conserved','lost','stolen','found','returned','loaned','note')),
  occurred_on        TEXT NULL,
  occurred_precision TEXT NOT NULL DEFAULT 'exact_day' CHECK (occurred_precision IN
                       ('exact_day','exact_month','exact_year','circa','unknown')),
  quantity           INTEGER NOT NULL DEFAULT 1,
  amount_minor       INTEGER NULL,
  fees_minor         INTEGER NULL,
  shipping_minor     INTEGER NULL,
  -- Cash that actually moved. On acquisition fees and postage add to the cost;
  -- on disposal they are deducted from the proceeds. Summing them in both
  -- directions would overstate every profit figure.
  net_minor          INTEGER GENERATED ALWAYS AS (
                       CASE WHEN event_type IN ('sold','traded_out','gifted_out')
                            THEN COALESCE(amount_minor,0) - COALESCE(fees_minor,0)
                                 - COALESCE(shipping_minor,0)
                            ELSE COALESCE(amount_minor,0) + COALESCE(fees_minor,0)
                                 + COALESCE(shipping_minor,0)
                       END) STORED,
  counterparty       TEXT NULL,
  counterparty_kind  TEXT NULL CHECK (counterparty_kind IN
                       (NULL,'dealer','auction','private','show','mint','online',
                        'grading_service','other')),
  venue              TEXT NULL,
  lot_reference      TEXT NULL,
  invoice_reference  TEXT NULL,
  notes              TEXT NULL,
  is_void            INTEGER NOT NULL DEFAULT 0,
  void_reason        TEXT NULL,
  voided_at          TEXT NULL,
  corrects_event_id  INTEGER NULL REFERENCES specimen_event(id) ON DELETE SET NULL,
  created_at         TEXT NOT NULL
);
CREATE INDEX ix_event_spec ON specimen_event(specimen_id, occurred_on);
CREATE INDEX ix_event_type ON specimen_event(event_type, occurred_on) WHERE is_void = 0;


-- ---------------------------------------------------------------------------
-- 10. Feature bindings. The user tells a feature which field to use; features
--     never infer meaning. purpose is defined by the consuming feature, e.g.
--     ('labels','cutout_diameter') or ('labels','flag_country').
-- ---------------------------------------------------------------------------

CREATE TABLE feature_binding (
  id                  INTEGER PRIMARY KEY,
  uuid                TEXT NOT NULL UNIQUE,
  feature             TEXT NOT NULL,
  purpose             TEXT NOT NULL,
  subcollection_id    INTEGER NULL REFERENCES subcollection(id) ON DELETE CASCADE,
  scope_key           INTEGER NOT NULL GENERATED ALWAYS AS (COALESCE(subcollection_id, 0)) STORED,
  target_kind         TEXT NOT NULL CHECK (target_kind IN
                        ('field','catalogue','grade','certification','constant','none')),
  field_definition_id INTEGER NULL REFERENCES field_definition(id) ON DELETE SET NULL,
  catalog_id          INTEGER NULL REFERENCES catalog(id) ON DELETE SET NULL,
  constant_json       TEXT NULL,
  config_json         TEXT NOT NULL DEFAULT '{}',
  created_at          TEXT NOT NULL,
  updated_at          TEXT NOT NULL
);
CREATE UNIQUE INDEX ux_binding ON feature_binding(feature, purpose, scope_key);


-- ---------------------------------------------------------------------------
-- 11. Tags and saved views
-- ---------------------------------------------------------------------------

CREATE TABLE tag (
  id         INTEGER PRIMARY KEY,
  uuid       TEXT NOT NULL UNIQUE,
  parent_id  INTEGER NULL REFERENCES tag(id) ON DELETE CASCADE,
  name       TEXT NOT NULL,
  colour     TEXT NULL,
  notes      TEXT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE UNIQUE INDEX ux_tag_name ON tag(COALESCE(parent_id, 0), name COLLATE NOCASE);

CREATE TABLE specimen_tag (
  specimen_id INTEGER NOT NULL REFERENCES specimen(id) ON DELETE CASCADE,
  tag_id      INTEGER NOT NULL REFERENCES tag(id) ON DELETE CASCADE,
  PRIMARY KEY (specimen_id, tag_id)
) WITHOUT ROWID;
CREATE INDEX ix_specimen_tag ON specimen_tag(tag_id);

CREATE TABLE saved_view (
  id               INTEGER PRIMARY KEY,
  uuid             TEXT NOT NULL UNIQUE,
  name             TEXT NOT NULL,
  subcollection_id INTEGER NULL REFERENCES subcollection(id) ON DELETE CASCADE,
  filter_json      TEXT NOT NULL DEFAULT '{}',
  sort_json        TEXT NOT NULL DEFAULT '[]',
  columns_json     TEXT NOT NULL DEFAULT '[]',
  group_by         TEXT NULL,
  sort_order       INTEGER NOT NULL DEFAULT 0,
  created_at       TEXT NOT NULL,
  updated_at       TEXT NOT NULL
);


-- ---------------------------------------------------------------------------
-- 12. Search. One FTS index; cjk_blob holds CJK content with every ideograph
--     space separated so any character sequence is a phrase query.
-- ---------------------------------------------------------------------------

CREATE TABLE specimen_search (
  specimen_id  INTEGER PRIMARY KEY REFERENCES specimen(id) ON DELETE CASCADE,
  title_blob   TEXT NOT NULL DEFAULT '',
  text_blob    TEXT NOT NULL DEFAULT '',
  catalog_blob TEXT NOT NULL DEFAULT '',
  note_blob    TEXT NOT NULL DEFAULT '',
  cjk_blob     TEXT NOT NULL DEFAULT '',
  rebuilt_at   TEXT NOT NULL
);

CREATE VIRTUAL TABLE specimen_fts USING fts5(
  title_blob, text_blob, catalog_blob, note_blob, cjk_blob,
  content       = 'specimen_search',
  content_rowid = 'specimen_id',
  tokenize      = "unicode61 remove_diacritics 2"
);
