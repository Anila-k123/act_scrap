"""Real import job: pull Central + state bare acts from India Code into our own
tables (Act/Chapter/Section). Not live scraping - this is meant to be run
manually or on a schedule (e.g. monthly), never per user request, and never
as part of the ams app server.

There is no server-side filter for jurisdiction on India Code's API (only
identifier_collection/author/subject/dateIssued/itemtype are indexed as
discovery facets - checked live), so this walks the FULL "ACT" collection
(11,463 items across every state, confirmed live) and filters to the
requested jurisdictions client-side. For each matching act it then does one
follow-up query scoped by that act's own `act_id` (NOT by title - title text
collides across jurisdictions, confirmed by the spike) to pull every SECTION
item that belongs to it.

    python manage.py import_acts                                  # Central + Tamil Nadu
    python manage.py import_acts --jurisdictions CENTRAL           # Central only
    python manage.py import_acts --limit 20                        # smoke test
    python manage.py import_acts --refresh                         # re-sync acts already stored
"""

from __future__ import annotations

import time

import requests
from django.core.management.base import BaseCommand
from django.utils.dateparse import parse_date

from acts.models import Act, Section

API = "https://indiacode.gov.in/server/api"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36")

DEFAULT_JURISDICTIONS = ["CENTRAL", "Tamil Nadu"]
PAGE_SIZE = 100
REQUEST_DELAY = 0.3  # seconds between requests - be a polite, non-live citizen


def _md(item: dict, key: str) -> str:
    values = item.get("metadata", {}).get(key)
    return values[0]["value"] if values else ""


def _md_bool(item: dict, key: str) -> bool:
    return _md(item, key).strip().lower() == "true"


def _md_int(item: dict, key: str) -> int | None:
    v = _md(item, key).strip()
    return int(v) if v.isdigit() else None


class IndiaCodeClient:
    def __init__(self):
        self.session = requests.Session()
        self.session.headers["User-Agent"] = UA

    def _get(self, path: str, **params) -> dict:
        r = self.session.get(f"{API}{path}", params=params, timeout=30)
        r.raise_for_status()
        time.sleep(REQUEST_DELAY)
        return r.json()

    def iter_act_items(self):
        """Every item in the ACT collection, across all jurisdictions, paginated."""
        page = 0
        while True:
            data = self._get("/discover/search/objects", **{
                "f.identifier_collection": "ACT,equals",
                "size": PAGE_SIZE, "page": page,
            })
            result = data["_embedded"]["searchResult"]
            objects = result.get("_embedded", {}).get("objects", [])
            for obj in objects:
                yield obj["_embedded"]["indexableObject"]
            total_pages = result["page"]["totalPages"]
            page += 1
            if page >= total_pages:
                return

    def sections_for_act(self, act_id: str) -> list[dict]:
        """Every SECTION item sharing this act's act_id (confirmed a stable,
        exact join key - unlike title text, which collides across states)."""
        data = self._get("/discover/search/objects",
                          query=f"dc.identifier.act_id:{act_id}", size=200)
        objects = data["_embedded"]["searchResult"]["_embedded"]["objects"]
        items = [o["_embedded"]["indexableObject"] for o in objects]
        return [it for it in items if _md(it, "dc.identifier.collection") == "SECTION"]


class Command(BaseCommand):
    help = "Import Central + state bare acts (and their sections) from India Code."

    def add_arguments(self, parser):
        parser.add_argument("--jurisdictions", default=",".join(DEFAULT_JURISDICTIONS),
                             help="Comma-separated source state_name values to import "
                                  f"(default: {','.join(DEFAULT_JURISDICTIONS)}).")
        parser.add_argument("--limit", type=int, default=None,
                             help="Stop after importing this many acts (for smoke-testing).")
        parser.add_argument("--refresh", action="store_true",
                             help="Re-sync acts already stored, instead of skipping them.")

    def handle(self, *args, **options):
        # Windows' console/file default encoding (cp1252) can't represent every
        # character India Code's act titles carry (confirmed live - crashed a
        # full run partway through on one such title). Progress up to that
        # point was NOT lost - the DB write happens before this line - but
        # force UTF-8 so a title never kills the whole run again.
        for stream in (self.stdout, self.stderr):
            underlying = getattr(stream, "_out", None)
            if underlying is not None and hasattr(underlying, "reconfigure"):
                underlying.reconfigure(encoding="utf-8", errors="replace")

        jurisdictions = {j.strip() for j in options["jurisdictions"].split(",") if j.strip()}
        limit = options["limit"]
        refresh = options["refresh"]

        client = IndiaCodeClient()
        imported = skipped = matched = 0

        self.stdout.write(f"Scanning India Code's ACT collection for: {sorted(jurisdictions)}\n")

        for item in client.iter_act_items():
            state_name = _md(item, "dc.identifier.state_name")
            if state_name not in jurisdictions:
                continue
            matched += 1

            source_uuid = item["uuid"]
            if not refresh and Act.objects.filter(source_uuid=source_uuid).exists():
                skipped += 1
                continue

            title = item.get("name", "") or _md(item, "dc.title")
            act_id = _md(item, "dc.identifier.act_id")

            act, _ = Act.objects.update_or_create(
                source_uuid=source_uuid,
                defaults=dict(
                    title=title,
                    long_title=_md(item, "dc.title.long_title"),
                    abstract=_md(item, "dc.description.abstract"),
                    preamble_html=_md(item, "dc.identifier.preamble_description"),
                    source_state_name=state_name,
                    act_number=_md(item, "dc.identifier.act_number"),
                    act_year=_md_int(item, "dc.date.act_year"),
                    ministry_name=_md(item, "dc.identifier.ministry_name"),
                    department_name=_md(item, "dc.identifier.department_name"),
                    enact_date=parse_date(_md(item, "dc.date.enact_date")),
                    enforcement_date=_md(item, "dc.date.enforcement_date"),
                    repealed=_md_bool(item, "dc.identifier.repealed"),
                    no_of_chapter=_md_int(item, "dc.identifier.no_of_chapter") or 0,
                    no_of_section=_md_int(item, "dc.identifier.no_of_section") or 0,
                    source_act_id=act_id,
                    source_state_id=_md(item, "dc.identifier.state_id"),
                ),
            )

            n_sections = 0
            if act_id:
                for sec_item in client.sections_for_act(act_id):
                    source_section_id = _md(sec_item, "dc.identifier.section_id")
                    sec_title = sec_item.get("name") or ""  # some items carry JSON null here, not a missing key
                    Section.objects.update_or_create(
                        act=act,
                        source_section_id=source_section_id or f"{act.id}:{sec_title}",
                        defaults=dict(
                            number=_md(sec_item, "dc.identifier.section_number"),
                            title=sec_title,
                            content=_md(sec_item, "dc.identifier.section_page_note"),
                            footnote=_md(sec_item, "dc.identifier.section_footnote"),
                            order_number=_md_int(sec_item, "dc.identifier.order_number") or 0,
                        ),
                    )
                    n_sections += 1

            imported += 1
            self.stdout.write(f"  [{state_name}] {title} - {n_sections} section(s)")

            if limit and imported >= limit:
                self.stdout.write(self.style.WARNING(f"\nStopping at --limit {limit}."))
                break

        self.stdout.write(self.style.SUCCESS(
            f"\nDone. {matched} act(s) matched the requested jurisdictions, "
            f"{imported} imported/refreshed, {skipped} already stored (skipped)."
        ))
