"""The publish draft: one Markdown document for a whole publication."""

import pytest

from jstdata.investigation import Investigation
from jstdata.investigation.model import Query, QueryNote
from jstdata.publish_draft import (
    PLACEHOLDER_NOTE,
    PLACEHOLDER_OVERVIEW,
    DraftError,
    apply_draft,
    build_draft,
)


@pytest.fixture
def inv() -> Investigation:
    i = Investigation.new("g7-debt", title="G7 government debt")
    i.queries[1] = Query(id=1, name="Debt, last 10 years", metric=["general-government-gross-debt"],
                         entity=["japan", "italy"], frequency="Annual", tail=10)
    i.queries[2] = Query(id=2, metric=["gdp"], entity=["japan"], tail=4)
    return i


def test_draft_lays_out_the_page(inv):
    draft = build_draft(inv)
    assert "# G7 government debt" in draft
    assert "## Query 1: Debt, last 10 years" in draft
    assert "## Query 2: Query 2" in draft
    # Each query says what its charts will show.
    assert "metrics: general-government-gross-debt · entities: japan, italy · annual · last 10 observations" in draft
    assert PLACEHOLDER_OVERVIEW in draft and PLACEHOLDER_NOTE in draft


def test_untouched_draft_changes_nothing(inv):
    result, changes = apply_draft(inv, build_draft(inv))
    assert not changes
    assert result.to_dict() == inv.to_dict()


def test_filled_in_draft_updates_every_field(inv):
    draft = build_draft(inv)
    draft = draft.replace("> One-line description shown under the title", "> Who carries the most debt?")
    draft = draft.replace(PLACEHOLDER_OVERVIEW, "## Question\nIs Japan still rising?\n\n- **Japan** leads.", 1)
    draft = draft.replace("## Query 2: Query 2", "## Query 2: Japan GDP")
    draft = draft.replace(PLACEHOLDER_NOTE, "Japan and Italy lead.", 1)
    draft = draft.replace("# G7 government debt", "# G7 debt, 2015–2024")

    result, changes = apply_draft(inv, draft)
    assert result.title == "G7 debt, 2015–2024"
    assert result.notes == "Who carries the most debt?"
    # A "## " heading inside the overview stays in the overview.
    assert result.annotations == "## Question\nIs Japan still rising?\n\n- **Japan** leads."
    assert result.queries[1].annotations == "Japan and Italy lead."
    assert result.queries[1].name == "Debt, last 10 years"
    assert result.queries[2].name == "Japan GDP"
    assert result.queries[2].annotations is None  # placeholder left in place
    assert set(changes.fields) == {"title", "description", "overview", "query 1 note", "query 2 name"}
    # The original is untouched until the caller saves.
    assert inv.title == "G7 government debt"



def test_draft_keeps_series_and_time_slice_notes(inv):
    inv.queries[1].notes_on = [QueryNote(series="s1", text="Restated."), QueryNote(from_="2020-01-01", text="Since 2020.")]
    draft = build_draft(inv).replace(PLACEHOLDER_NOTE, "Japan and Italy lead.", 1)
    result, _ = apply_draft(inv, draft)
    assert result.queries[1].notes_on == inv.queries[1].notes_on
    assert result.to_dict()["queries"][0]["notes_on"] == inv.to_dict()["queries"][0]["notes_on"]

def test_comments_are_ignored(inv):
    draft = build_draft(inv).replace(PLACEHOLDER_OVERVIEW, "Overview <!-- not this --> text", 1)
    result, _ = apply_draft(inv, draft)
    assert result.annotations == "Overview  text"


def test_deleting_a_query_section_leaves_it_alone(inv):
    inv.queries[2].annotations = "Keep me"
    draft = build_draft(inv)
    draft = draft[: draft.index("## Query 2:")]
    result, changes = apply_draft(inv, draft)
    assert result.queries[2].annotations == "Keep me"
    assert 2 in result.queries
    assert not changes


def test_unknown_query_number_is_an_error(inv):
    draft = build_draft(inv) + "\n## Query 9: Typo\nNote\n"
    with pytest.raises(DraftError, match="Query 9.*queries are 1, 2"):
        apply_draft(inv, draft)


@pytest.mark.parametrize("draft,message", [
    ("", "empty"),
    ("<!-- only a comment -->\n", "empty"),
    ("No title here\n", "title"),
])
def test_unusable_drafts(inv, draft, message):
    with pytest.raises(DraftError, match=message):
        apply_draft(inv, draft)


def test_existing_text_appears_in_the_draft(inv):
    inv.notes = "Short description"
    inv.annotations = "Existing overview"
    inv.queries[1].annotations = "Existing note"
    draft = build_draft(inv)
    assert "> Short description" in draft
    assert "Existing overview" in draft and PLACEHOLDER_OVERVIEW not in draft
    assert "Existing note" in draft
