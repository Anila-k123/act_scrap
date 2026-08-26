"""Scraped reference data imported from India Code (indiacode.gov.in).

This project owns ONLY the imported act text itself — Central + state bare
acts, their chapters and sections — via the import_acts management command.
It writes into the same Postgres database (advocate_db) the `ams` Django app
reads from, but is a deliberately separate codebase/process: this is a batch
import job (run manually or on a schedule), never a live server. `ams` will
eventually read these tables via its own read-only unmanaged models (the
same pattern it already uses for tables owned by something else) — it does
not import or run any code from this project.

Field notes from the spike (python manage.py spike_fetch_act):
  - enforcement_date is NOT reliably a clean date on the source side — some
    acts carry a full sentence here (e.g. "Ss. 4(1), 5(1)... and rest
    provisions on 120th day of its enactment."), so it's stored as text, not
    parsed into a DateField.
  - Section content/footnote carry light inline HTML from the source
    (<span>, <hr>, <i>, <sup> for footnote markers) - stored verbatim; the
    frontend sanitizes/renders it, this layer doesn't try to clean it up.
  - Some older/less-referenced acts have SECTION items with no content/
    footnote text at all (confirmed live against an 1876 act) - the source
    itself hasn't digitized full text for every act, only titles/numbers.
  - source_state_name + source_act_number + source_act_year matter for
    dedup because act TITLES collide across jurisdictions (the spike matched
    "The Right to Information Act, 2005" to a Rajasthan act, then a
    Maharashtra one, before the actual central Act) - title alone is not a
    safe unique key.
"""

from __future__ import annotations

from django.db import models


class Act(models.Model):
    title = models.CharField(max_length=512)
    long_title = models.TextField(blank=True)
    abstract = models.TextField(blank=True)          # dc.description.abstract
    preamble_html = models.TextField(blank=True)      # dc.identifier.preamble_description

    source_state_name = models.CharField(max_length=128)   # "CENTRAL" | "Tamil Nadu" | ...
    act_number = models.CharField(max_length=32, blank=True)
    act_year = models.IntegerField(null=True, blank=True)

    ministry_name = models.CharField(max_length=256, blank=True)
    department_name = models.CharField(max_length=256, blank=True)

    enact_date = models.DateField(null=True, blank=True)
    enforcement_date = models.TextField(blank=True)  # free text - see module docstring

    repealed = models.BooleanField(default=False)
    no_of_chapter = models.IntegerField(default=0)   # as reported by the source, informational
    no_of_section = models.IntegerField(default=0)

    pdf_url = models.URLField(max_length=1024, blank=True)

    # India Code provenance - used to re-sync/dedup instead of matching by title.
    source_uuid = models.CharField(max_length=64, unique=True)       # DSpace item UUID
    source_act_id = models.CharField(max_length=128, blank=True, db_index=True)
    source_state_id = models.CharField(max_length=32, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    last_synced_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=['source_state_name']),
            models.Index(fields=['title']),
        ]

    def __str__(self):
        return f'{self.title} ({self.source_state_name}, Act {self.act_number} of {self.act_year})'


class Chapter(models.Model):
    act = models.ForeignKey(Act, on_delete=models.CASCADE, related_name='chapters')
    number = models.CharField(max_length=32, blank=True)   # e.g. "I", "II" - source format varies
    title = models.CharField(max_length=512, blank=True)
    order = models.IntegerField(default=0)

    class Meta:
        ordering = ['order']

    def __str__(self):
        return f'Chapter {self.number}: {self.title}'


class Section(models.Model):
    act = models.ForeignKey(Act, on_delete=models.CASCADE, related_name='sections')
    chapter = models.ForeignKey(Chapter, on_delete=models.SET_NULL, null=True, blank=True,
                                related_name='sections')

    number = models.CharField(max_length=32)   # string, not int - sub-sections like "3A" exist
    title = models.CharField(max_length=512, blank=True)
    content = models.TextField(blank=True)      # dc.identifier.section_page_note, verbatim
    footnote = models.TextField(blank=True)     # dc.identifier.section_footnote, verbatim
    order_number = models.IntegerField(default=0)

    source_section_id = models.CharField(max_length=64, blank=True, db_index=True)

    class Meta:
        ordering = ['order_number']
        indexes = [models.Index(fields=['act', 'order_number'])]

    def __str__(self):
        return f'Section {self.number}: {self.title}'


class ActPaper(models.Model):
    """Subordinate delegated legislation tied to an act - the "Act Papers"
    tab. Confirmed live: RULE and NOTIFICATION items share the parent act's
    act_id (same reliable join key Section uses), each with its own title,
    date, and attached PDF - a real, distinct India Code item type, not
    something bundled into the ACT record itself."""
    act = models.ForeignKey(Act, on_delete=models.CASCADE, related_name='papers')
    paper_type = models.CharField(max_length=32)   # "RULE" | "NOTIFICATION"
    title = models.CharField(max_length=512, blank=True)
    paper_date = models.DateField(null=True, blank=True)
    pdf_url = models.URLField(max_length=1024, blank=True)

    source_uuid = models.CharField(max_length=64, unique=True)
    source_paper_id = models.CharField(max_length=128, blank=True)

    class Meta:
        ordering = ['-paper_date']

    def __str__(self):
        return f'{self.paper_type}: {self.title}'
