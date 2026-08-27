"""Real import job: pull Central + state bare acts from India Code into our own
tables (Act/Chapter/Section/ActPaper). Not live scraping - this is meant to be
run manually or on a schedule (e.g. monthly), never per user request, and
never as part of the ams app server.

There is no server-side filter for jurisdiction on India Code's API (only
identifier_collection/author/subject/dateIssued/itemtype are indexed as
discovery facets - checked live), so this walks the FULL "ACT" collection
(11,463 items across every state, confirmed live) and filters to the
requested jurisdictions client-side. For each matching act it then does one
follow-up query scoped by that act's own `act_id` (NOT by title - title text
collides across jurisdictions, confirmed by the spike) to pull every related
item: SECTION (the act's text), and RULE/NOTIFICATION (subordinate delegated
legislation - the "Act Papers" tab, confirmed live to be real, separate item
types with their own title/date/PDF, not something bundled into the ACT
record). Each ACT and ActPaper also gets its own attached source PDF fetched
via a bundles/bitstreams lookup - roughly doubles the request count for a
full run, since it's an extra 2 calls per item that has one.

    python manage.py import_acts                                  # Central + Tamil Nadu
    python manage.py import_acts --jurisdictions CENTRAL           # Central only
    python manage.py import_acts --limit 20                        # smoke test
    python manage.py import_acts --refresh                         # re-sync acts already stored
"""

from __future__ import annotations

import queue
import threading
import time

import requests
from django.core.management.base import BaseCommand
from django.utils.dateparse import parse_date

from acts.models import Act, ActPaper, Section

API = "https://indiacode.gov.in/server/api"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36")

DEFAULT_JURISDICTIONS = ["CENTRAL", "Tamil Nadu"]
PAGE_SIZE = 100
REQUEST_DELAY = 0.3  # seconds between requests - be a polite, non-live citizen
RETRIES = 5
RETRY_BACKOFF_BASE = 5  # seconds; doubles each attempt (5, 10, 20, 40, 80)
HANG_GUARD_SECONDS = 45  # hard wall-clock cap per request - see _get()


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

    def _raw_get(self, path: str, params: dict, out: queue.Queue) -> None:
        try:
            r = self.session.get(f"{API}{path}", params=params, timeout=30)
            r.raise_for_status()
            out.put(("ok", r.json()))
        except requests.exceptions.RequestException as exc:
            out.put(("error", exc))

    def _get(self, path: str, **params) -> dict:
        # India Code is confirmed intermittently flaky: plain connect
        # timeouts (raises cleanly, retry loop below handles it), and
        # separately a genuine multi-minute hang with ZERO open socket -
        # confirmed live via netstat, meaning it never even reached the
        # connect/read phase requests' own `timeout=30` bounds. That's a
        # known real gotcha (DNS resolution isn't covered by requests'
        # timeout on some systems) - no exception is ever raised to retry,
        # so the loop below alone can't save it.
        #
        # A daemon=True thread per attempt fixes it: join(timeout=...) lets
        # the main thread give up and retry on a fresh thread without
        # waiting for the hung one, and being daemon means it can't block
        # process exit either (unlike ThreadPoolExecutor's default
        # shutdown(wait=True) on its context-manager exit, which would just
        # move the hang to when the script tries to quit). The abandoned
        # thread leaks until the OS-level hang itself eventually resolves -
        # harmless for a batch job.
        last_exc = None
        for attempt in range(1, RETRIES + 1):
            out: queue.Queue = queue.Queue(maxsize=1)
            t = threading.Thread(target=self._raw_get, args=(path, params, out), daemon=True)
            t.start()
            t.join(timeout=HANG_GUARD_SECONDS)

            if t.is_alive():
                last_exc = TimeoutError(f"{path} hung past {HANG_GUARD_SECONDS}s hard cap")
            else:
                kind, payload = out.get()
                if kind == "ok":
                    time.sleep(REQUEST_DELAY)
                    return payload
                last_exc = payload

            if attempt < RETRIES:
                wait = RETRY_BACKOFF_BASE * (2 ** (attempt - 1))
                print(f"  [{attempt}/{RETRIES}] {path} failed ({last_exc}) - retrying in {wait}s")
                time.sleep(wait)
        raise last_exc

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

    def related_items_for_act(self, act_id: str) -> list[dict]:
        """Every item sharing this act's act_id (confirmed a stable, exact
        join key - unlike title text, which collides across states):
        SECTION items, but also RULE and NOTIFICATION items - subordinate
        delegated legislation and gazette notifications, confirmed live as
        real, separate India Code item types with their own title/date/PDF,
        not something bundled into the ACT record. One fetch covers all of
        them; the caller splits by dc.identifier.collection."""
        data = self._get("/discover/search/objects",
                          query=f"dc.identifier.act_id:{act_id}", size=200)
        objects = data["_embedded"]["searchResult"]["_embedded"]["objects"]
        return [o["_embedded"]["indexableObject"] for o in objects]

    def pdf_url_for_item(self, uuid: str) -> str:
        """The item's own attached source PDF, if any - confirmed live for
        both ACT and NOTIFICATION items: an "ORIGINAL" bundle holding the
        PDF, alongside TEXT/THUMBNAIL bundles this doesn't need. Best-effort:
        a missing/unreachable PDF shouldn't abort importing the rest of the
        act's data, so failures here are swallowed to an empty string."""
        try:
            bundles = self._get(f"/core/items/{uuid}/bundles")
            for b in bundles.get("_embedded", {}).get("bundles", []):
                if b.get("name") != "ORIGINAL":
                    continue
                href = b.get("_links", {}).get("bitstreams", {}).get("href", "")
                path = href[len(API):] if href.startswith(API) else href
                if not path:
                    continue
                bitstreams = self._get(path)
                items = bitstreams.get("_embedded", {}).get("bitstreams", [])
                if items:
                    return items[0].get("_links", {}).get("content", {}).get("href", "")
        except (requests.exceptions.RequestException, TimeoutError):
            pass
        return ""


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
            if matched % 25 == 0:
                # ~1,250 as of the last full discovery scan (846 Central + 404
                # Tamil Nadu) - an approximate denominator, not a live count;
                # India Code's own catalog can grow between runs.
                self.stdout.write(f"  ... {matched}/~1250 matched so far "
                                  f"({imported} imported/refreshed, {skipped} skipped)")

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
                    pdf_url=client.pdf_url_for_item(source_uuid),
                ),
            )

            related = client.related_items_for_act(act_id) if act_id else []
            section_items = [it for it in related if _md(it, "dc.identifier.collection") == "SECTION"]
            paper_items = [it for it in related
                           if _md(it, "dc.identifier.collection") in ("RULE", "NOTIFICATION")]

            n_sections = 0
            for sec_item in section_items:
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

            n_papers = 0
            for paper_item in paper_items:
                paper_type = _md(paper_item, "dc.identifier.collection")
                paper_title = paper_item.get("name") or ""
                paper_source_id = (_md(paper_item, "dc.identifier.rule_id")
                                   or _md(paper_item, "dc.identifier.notification_id"))
                ActPaper.objects.update_or_create(
                    source_uuid=paper_item["uuid"],
                    defaults=dict(
                        act=act,
                        paper_type=paper_type,
                        title=paper_title,
                        paper_date=parse_date(_md(paper_item, "dc.date.issued")),
                        pdf_url=client.pdf_url_for_item(paper_item["uuid"]),
                        source_paper_id=paper_source_id,
                    ),
                )
                n_papers += 1

            imported += 1
            self.stdout.write(f"  [{state_name}] {title} - {n_sections} section(s), {n_papers} paper(s)")

            if limit and imported >= limit:
                self.stdout.write(self.style.WARNING(f"\nStopping at --limit {limit}."))
                break

        self.stdout.write(self.style.SUCCESS(
            f"\nDone. {matched} act(s) matched the requested jurisdictions, "
            f"{imported} imported/refreshed, {skipped} already stored (skipped)."
        ))
