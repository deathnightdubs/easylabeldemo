"""A small command line for inspecting a library.

This exists so the core can be exercised without a GUI, and to prove the architectural rule
that nothing important lives in the interface layer. It is not the intended way to use the
program; the spreadsheet-style table view comes later.

    python -m numis create ~/MyCollection.numis
    python -m numis demo ~/Demo.numis
    python -m numis info ~/MyCollection.numis
    python -m numis list ~/MyCollection.numis --sort date_issued
"""

from __future__ import annotations

import argparse
from pathlib import Path

from sqlalchemy import func, select

from .db import create_library, open_library
from .models import (
    Catalog,
    Certification,
    FieldDefinition,
    GradeScale,
    Specimen,
    SpecimenGrade,
    Subcollection,
)
from .services import CollectionService


def _create(args: argparse.Namespace) -> int:
    library = create_library(args.path)
    print(f"Created an empty library at {library.path}")
    print("It contains no catalogues, grading scales or fields: build what you use.")
    library.close()
    return 0


def _info(args: argparse.Namespace) -> int:
    library = open_library(args.path)
    try:
        with library.session() as session:
            meta = library.meta(session)
            print(f"Library      {library.path}")
            print(f"Schema       {meta.schema_version}")
            print(f"Currency     {meta.currency_symbol} ({meta.currency_code})")
            counts = {
                "subcollections": Subcollection,
                "fields": FieldDefinition,
                "specimens": Specimen,
                "catalogues": Catalog,
                "grade scales": GradeScale,
                "grades": SpecimenGrade,
                "certifications": Certification,
            }
            print()
            for label, model in counts.items():
                total = session.scalar(select(func.count()).select_from(model))
                print(f"  {label:<16}{total}")

            svc = CollectionService(session)
            queue = svc.needs_review()
            if queue:
                print(f"\n  {len(queue)} value(s) awaiting a sort position:")
                for specimen_id, key, shown in queue[:10]:
                    print(f"    specimen {specimen_id}  {key} = {shown!r}")
    finally:
        library.close()
    return 0


def _list(args: argparse.Namespace) -> int:
    library = open_library(args.path)
    try:
        with library.session() as session:
            svc = CollectionService(session)
            subcollection = None
            if args.subcollection:
                subcollection = session.scalar(
                    select(Subcollection).where(Subcollection.slug == args.subcollection)
                )
                if subcollection is None:
                    print(f"No subcollection with slug {args.subcollection!r}")
                    return 1

            columns = (
                svc.columns_for(subcollection)
                if subcollection
                else svc.master_columns()
            )
            if args.sort:
                specimens = svc.sorted_by_field(args.sort, subcollection=subcollection)
            else:
                specimens = list(session.scalars(svc.live_specimens(subcollection)))

            headers = ["id", "name", *[c.label for c in columns if c.kind == "field"]]
            rows = [
                [
                    str(specimen.id),
                    specimen.display_name or "",
                    *[
                        svc.display(specimen, c.key)
                        for c in columns
                        if c.kind == "field"
                    ],
                ]
                for specimen in specimens
            ]
            widths = [
                max(len(str(row[i])) for row in [headers, *rows]) for i in range(len(headers))
            ]
            for row in [headers, ["-" * w for w in widths], *rows]:
                print(
                    "  ".join(
                        str(cell).ljust(width)
                        for cell, width in zip(row, widths, strict=True)
                    )
                )
    finally:
        library.close()
    return 0


def _demo(args: argparse.Namespace) -> int:
    """Build a small library that exercises the awkward cases.

    Nothing here is shipped as default content; it is assembled by these calls so the
    output demonstrates what the user would have to define themselves.
    """
    library = create_library(args.path)
    try:
        with library.session() as session:
            svc = CollectionService(session)
            modern = svc.create_subcollection("Modern", naming_template="{country} {denom}")
            ancients = svc.create_subcollection("Ancients", naming_template="{head} {denom}")

            head = svc.create_field("head", "Head of state", "text")
            country = svc.create_field("country", "Country", "text")
            denom = svc.create_field("denom", "Denomination", "text",
                                     config={"numeric_sort": True})
            issued = svc.create_field("date_issued", "Date", "date")
            weight = svc.create_field("weight", "Weight", "weight")

            # The same field, labelled differently in each subcollection.
            svc.show_field(modern, head, display_label="Ruler", show_in_table=True)
            svc.show_field(ancients, head, display_label="Emperor", show_in_table=True)
            for sub in (modern, ancients):
                svc.show_field(sub, denom, show_in_table=True, sort_order=1)
                svc.show_field(sub, issued, show_in_table=True, sort_order=2)
                svc.show_field(sub, weight, show_in_table=True, sort_order=3)
            svc.show_field(modern, country, show_in_table=True, sort_order=4)

            svc.add_specimen(modern, values={
                "head": "Maria Theresia", "country": "Austria", "denom": "1 Thaler",
                "date_issued": "1780", "weight": "28.07",
            })
            svc.add_specimen(modern, values={
                "head": "Qianlong", "country": "China", "denom": "1 wen",
                "date_issued": "1736-1795", "weight": "4.1",
            })
            svc.add_specimen(ancients, values={
                "head": "Trajan", "denom": "1 Denarius",
                "date_issued": "c. 105", "weight": "3.2",
            })
            svc.bulk_add(modern, 3, values={
                "head": "Victoria", "country": "United Kingdom", "denom": "1 Crown",
                "date_issued": "1889",
            })
            svc.reindex_all()
    finally:
        library.close()
    print(f"Created a demonstration library at {args.path}")
    print("Try:  python -m numis list <path> --sort date_issued")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="numis", description=__doc__.splitlines()[0])
    subparsers = parser.add_subparsers(dest="command", required=True)

    for name, handler, helptext in (
        ("create", _create, "create a new, empty library"),
        ("info", _info, "show what a library contains"),
        ("demo", _demo, "create a small library demonstrating the awkward cases"),
    ):
        sub = subparsers.add_parser(name, help=helptext)
        sub.add_argument("path", type=Path)
        sub.set_defaults(handler=handler)

    listing = subparsers.add_parser("list", help="print specimens as a table")
    listing.add_argument("path", type=Path)
    listing.add_argument("--subcollection", help="slug of one subcollection")
    listing.add_argument("--sort", help="field key to sort by")
    listing.set_defaults(handler=_list)

    args = parser.parse_args(argv)
    return int(args.handler(args))


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
