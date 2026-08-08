"""Building the searchable text for each specimen.

One FTS5 index covers everything, with a separate ``cjk_blob`` column holding CJK content
with every ideograph space-separated. That column exists because of a real limitation found
by testing (docs/design/01, Part 6):

* ``unicode61`` treats a whole CJK legend as **one token**, so searching 通寶 inside 乾隆通寶
  matched nothing.
* The ``trigram`` tokenizer only handles sequences of three or more characters, so the
  two-character terms collectors actually use — 通寶, 乾隆, 當十 — all failed, and it stopped
  folding diacritics so ``Gunzburg`` no longer matched ``Günzburg``.

Splitting CJK into single characters makes any sequence an ordinary phrase query.
"""

from __future__ import annotations

import re

#: CJK ideographs, kana and hangul. Matches the ranges the label generator already detects.
CJK_RE = re.compile(
    r"[\u3000-\u303f\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\uac00-\ud7af]"
)


def contains_cjk(text: str) -> bool:
    return bool(CJK_RE.search(text))


def segment_cjk(text: str) -> str:
    """Space-separate every CJK character, leaving other scripts intact.

    ``乾隆通寶 寶泉 Qianlong`` becomes ``乾 隆 通 寶 寶 泉 Qianlong``.
    """
    out: list[str] = []
    for token in text.split():
        if contains_cjk(token):
            out.extend(
                " ".join(char) if CJK_RE.match(char) else char for char in token
            )
        else:
            out.append(token)
    return " ".join(part for part in " ".join(out).split() if part)


def build_query(
    term: str,
    *,
    columns: tuple[str, ...] = ("title_blob", "text_blob", "catalog_blob", "note_blob"),
) -> str:
    """Turn a user's search term into an FTS5 query.

    A term containing CJK is routed to ``cjk_blob`` as a phrase of single characters, which
    is what makes two-character and single-character searches work. Everything else keeps
    ordinary word and prefix matching with diacritic folding.
    """
    term = term.strip()
    if not term:
        return ""
    if contains_cjk(term):
        chars = " ".join(char for char in term if not char.isspace())
        return f'cjk_blob : "{chars}"'
    escaped = term.replace('"', '""')
    return " OR ".join(f'{column} : "{escaped}"*' for column in columns)
