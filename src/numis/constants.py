"""Enumerated vocabularies used by the schema.

These are the only closed vocabularies in the system. Everything a collector might invent
— catalogues, grading scales, grade levels, modifiers, grading companies, field names — is
data they create, not code. See docs/design/02, Part 4.
"""

from __future__ import annotations

# --- specimens ---------------------------------------------------------------

SPECIMEN_STATUSES = (
    "owned",
    "ordered",
    "sold",
    "traded",
    "gifted",
    "lost",
    "stolen",
    "returned",
    "on_loan",
    "wanted",
)

#: Statuses meaning the coin is no longer held.
DISPOSED_STATUSES = ("sold", "traded", "gifted", "lost", "stolen")

# --- fields ------------------------------------------------------------------

FIELD_KINDS = ("value", "computed")

BLOCK_KINDS = ("field", "catalogues", "grades", "certifications", "links", "history")

SORT_SOURCES = ("none", "auto", "manual")

DATE_PRECISIONS = (
    "exact_day",
    "exact_month",
    "exact_year",
    "range",
    "decade",
    "century",
    "circa",
    "unknown",
)

CALENDARS = (
    "gregorian",
    "julian",
    "islamic_ah",
    "chinese_regnal",
    "jewish",
    "french_republican",
    "other",
)

# --- catalogues --------------------------------------------------------------

CATALOG_SORT_STRATEGIES = ("prefix_aware", "numeric", "lexical")

LETTER_PREFIX_ORDERS = ("after", "before")

CATALOG_CERTAINTIES = ("certain", "probable", "cf", "disputed")

# --- grading -----------------------------------------------------------------

GRADE_SCALE_KINDS = ("numeric", "ordinal")

GRADE_MODIFIER_KINDS = ("detail", "sticker", "qualifier", "strike")

GRADE_SOURCES = ("self", "seller", "tpg", "auction", "other")

CERTIFICATION_STATUSES = (
    "current",
    "pending",
    "cracked_out",
    "crossed_over",
    "regraded",
    "superseded",
)

# --- links and history -------------------------------------------------------

LINK_KINDS = (
    "zeno",
    "numista",
    "grading",
    "auction",
    "dealer",
    "paper",
    "forum",
    "museum",
    "image",
    "other",
)

EVENT_TYPES = (
    "acquired",
    "ordered",
    "received",
    "sold",
    "listed",
    "traded_in",
    "traded_out",
    "gifted_in",
    "gifted_out",
    "valued",
    "graded_sent",
    "graded_returned",
    "moved",
    "conserved",
    "lost",
    "stolen",
    "found",
    "returned",
    "loaned",
    "note",
)

#: Events where fees and postage are deducted from the amount rather than added to it.
#: Getting this wrong overstates every profit figure. See docs/design/01, Part 5.
OUTGOING_EVENT_TYPES = ("sold", "traded_out", "gifted_out")

EVENT_PRECISIONS = ("exact_day", "exact_month", "exact_year", "circa", "unknown")

COUNTERPARTY_KINDS = (
    "dealer",
    "auction",
    "private",
    "show",
    "mint",
    "online",
    "grading_service",
    "other",
)

# --- feature bindings --------------------------------------------------------

BINDING_TARGET_KINDS = ("field", "catalogue", "grade", "certification", "constant", "none")
