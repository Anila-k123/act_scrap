"""Feasibility spike — NOT the real import job.

Pulls one act end-to-end from India Code's public DSpace REST API (metadata +
its section-level records) and prints what actually comes back, so we can look
at real, messy data before designing the Act/Chapter/Section models and the
real import command. Throwaway: no models, no DB writes.

    python manage.py spike_fetch_act
    python manage.py spike_fetch_act --query "Tamil Nadu Town and Country Planning Act"
"""

from __future__ import annotations

import requests
from django.core.management.base import BaseCommand

API = "https://indiacode.gov.in/server/api"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36")

DEFAULT_QUERY = "Right to Information Act 2005"


def _md(item: dict, key: str) -> str:
    values = item.get("metadata", {}).get(key)
    return values[0]["value"] if values else ""


def _search(session: requests.Session, query: str, size: int = 100) -> list[dict]:
    r = session.get(f"{API}/discover/search/objects",
                     params={"query": query, "dsoType": "item", "size": size}, timeout=30)
    r.raise_for_status()
    objects = r.json()["_embedded"]["searchResult"]["_embedded"]["objects"]
    return [o["_embedded"]["indexableObject"] for o in objects]


class Command(BaseCommand):
    help = "Spike: fetch one act's metadata + sections from India Code and print it."

    def add_arguments(self, parser):
        parser.add_argument("--query", default=DEFAULT_QUERY,
                             help="Act name to search for (default: a well-known multi-chapter act).")

    def handle(self, *args, **options):
        query = options["query"]
        session = requests.Session()
        session.headers["User-Agent"] = UA

        self.stdout.write(f"Searching India Code for: {query!r}\n")
        results = _search(session, query)

        acts = [it for it in results if _md(it, "dc.identifier.collection") == "ACT"]
        if not acts:
            self.stderr.write(self.style.ERROR("No ACT-collection item found for that query."))
            return

        act = acts[0]
        act_title = act.get("name", "")
        self.stdout.write(self.style.SUCCESS(f"\n=== ACT: {act_title} ===\n"))
        for key in ("dc.identifier.act_number", "dc.date.act_year", "dc.identifier.state_name",
                    "dc.identifier.ministry_name", "dc.identifier.department_name",
                    "dc.date.enact_date", "dc.date.enforcement_date",
                    "dc.identifier.repealed", "dc.identifier.no_of_chapter",
                    "dc.identifier.no_of_section"):
            self.stdout.write(f"  {key}: {_md(act, key)}")

        # Sections belonging to this act are separate SECTION-collection items;
        # there's no direct parent-item link exposed here, so we match by the
        # act name they carry plus the same state/act-number/year — the spike
        # is exactly to see how reliable that matching is on real data.
        self.stdout.write("\nSearching for this act's sections...\n")
        sec_results = _search(session, act_title, size=200)
        sections = [
            it for it in sec_results
            if _md(it, "dc.identifier.collection") == "SECTION"
            and _md(it, "dc.title.act_name") == act_title
        ]

        def sort_key(it):
            try:
                return (0, int(_md(it, "dc.identifier.section_number") or 0))
            except ValueError:
                return (1, _md(it, "dc.identifier.section_number"))

        sections.sort(key=sort_key)

        self.stdout.write(self.style.SUCCESS(f"\n=== {len(sections)} SECTION item(s) matched ===\n"))
        for sec in sections[:15]:
            num = _md(sec, "dc.identifier.section_number")
            title = sec.get("name", "")
            content = _md(sec, "dc.identifier.section_page_note")
            preview = (content[:220] + "…") if len(content) > 220 else content
            self.stdout.write(f"\n--- Section {num}: {title} ---")
            self.stdout.write(f"  order_number: {_md(sec, 'dc.identifier.order_number')}")
            self.stdout.write(f"  content ({len(content)} chars): {preview}")

        if len(sections) > 15:
            self.stdout.write(f"\n... ({len(sections) - 15} more not shown)")

        if not sections:
            self.stdout.write(self.style.WARNING(
                "\nNo sections matched by exact act_name — this is exactly the kind of "
                "gap the spike is meant to surface (e.g. amended acts where act_name text "
                "differs slightly, or older acts with no SECTION-collection items at all)."
            ))
