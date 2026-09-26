"""WhatsApp: finding contacts, drafts the assistant writes, and the yes that sends them."""

import json
from datetime import UTC, datetime

import pytest

from deskbuddy.services.agent_tools import ToolBox
from deskbuddy.services.memory import MemoryStore
from deskbuddy.services.whatsapp import Contact, Draft, clean_message, is_no, is_yes, match_contacts
from deskbuddy.ui.buddy_window import Buddy

RAVI = Contact("919876543210@s.whatsapp.net", "Ravi Kumar")
RAVI_T = Contact("919812340000@s.whatsapp.net", "Ravi Teja")
AMMA = Contact("919900011122@s.whatsapp.net", "Amma")
PRIYA = Contact("919811122233@s.whatsapp.net", "Priya (Office)")
CONTACTS = [RAVI, RAVI_T, AMMA, PRIYA]


@pytest.mark.parametrize("query, first", [
    ("Ravi Kumar", RAVI), ("ravi teja", RAVI_T), ("amma", AMMA), ("Amma.", AMMA),
    ("priya", PRIYA), ("Priyaa", PRIYA), ("9876543210", RAVI)])
def test_contacts_are_found_by_name_or_number(query, first):
    assert match_contacts(query, CONTACTS)[0] == first


def test_a_shared_first_name_gives_every_match():
    assert set(match_contacts("Ravi", CONTACTS)) == {RAVI, RAVI_T}


def test_unknown_names_find_nobody():
    assert match_contacts("Suresh", CONTACTS) == []
    assert match_contacts("", CONTACTS) == []


@pytest.mark.parametrize("text", ["Yes.", "Yeah, send it", "Send it!", "Go ahead", "OK", "yes please",
                                  "Avunu"])
def test_yes(text):
    assert is_yes(text) and not is_no(text)


@pytest.mark.parametrize("text", ["No.", "Cancel", "Don't send it", "never mind", "Wait"])
def test_no(text):
    assert is_no(text) and not is_yes(text)


@pytest.mark.parametrize("text", ["yes but change it to 6 pm", "send it to Amma instead", "no wait, say 7"])
def test_changes_are_neither_yes_nor_no(text):
    assert not is_yes(text) and not is_no(text)


def test_message_text_is_cleaned():
    assert clean_message('  "I\'ll be 10 minutes late"  ') == "I'll be 10 minutes late"


class FakeLink:
    def __init__(self, state="connected", contacts=CONTACTS):
        self.state, self.contacts, self.proposed, self.sent_drafts = state, contacts, [], []

    def propose(self, contact, text):
        self.proposed.append(Draft(contact, text))

    def send(self, draft):
        self.sent_drafts.append(draft)


def toolbox(link):
    return ToolBox(MemoryStore(":memory:"), lambda: [], whatsapp=link,
                   now=lambda: datetime(2026, 9, 27, 9, tzinfo=UTC))


def test_the_assistant_can_only_draft_never_send():
    link = FakeLink()
    box = toolbox(link)
    found = json.loads(box.run("find_whatsapp_contact", {"name": "amma"}).content)
    assert found == [{"key": AMMA.key, "name": "Amma"}]
    result = box.run("draft_whatsapp_message", {"contact_key": AMMA.key, "text": "Reached safely"})
    assert not result.is_error and "NOT sent" in result.content
    assert link.proposed == [Draft(AMMA, "Reached safely")]
    assert link.sent_drafts == []


def test_same_names_are_told_apart_by_the_end_of_the_number():
    found = json.loads(toolbox(FakeLink()).run("find_whatsapp_contact", {"name": "Ravi"}).content)
    assert all("number_ends" not in f for f in found)       # different names: no numbers needed
    twins = [RAVI, Contact("911111111111@s.whatsapp.net", "Ravi Kumar")]
    found = json.loads(toolbox(FakeLink(contacts=twins)).run("find_whatsapp_contact",
                                                             {"name": "Ravi"}).content)
    assert {f["number_ends"] for f in found} == {"3210", "1111"}


def test_a_made_up_contact_key_is_refused():
    link = FakeLink()
    result = toolbox(link).run("draft_whatsapp_message", {"contact_key": "deadbeef", "text": "hi"})
    assert result.is_error and link.proposed == []


def test_not_linked_tells_the_user_how_to_link():
    result = toolbox(FakeLink(state="not linked")).run("find_whatsapp_contact", {"name": "Amma"})
    assert result.is_error and "Link WhatsApp" in result.content


# ---- the confirm card in the real window

@pytest.fixture
def buddy(qtbot, monkeypatch):
    monkeypatch.setattr(Buddy, "sync_calendar", lambda self: None)
    b = Buddy()
    qtbot.addWidget(b)
    b.show()
    b.calendar_timer.stop()
    b.reminder_timer.stop()
    b.whatsapp.state = "connected"
    b.sent_drafts = []
    monkeypatch.setattr(b.whatsapp, "send", b.sent_drafts.append)
    return b


def test_a_draft_waits_on_the_card_until_you_say_yes(buddy):
    buddy.on_draft(Draft(AMMA, "Reached safely"))
    assert buddy.draft_card.isVisible() and buddy.sent_drafts == []
    assert buddy.chat.ask("Yes, send it", spoken=False)
    assert buddy.sent_drafts == [Draft(AMMA, "Reached safely")]
    buddy.on_sent(Draft(AMMA, "Reached safely"), True, "")
    assert not buddy.draft_card.isVisible()
    assert "Sent to Amma" in buddy.bubble.text


def test_no_cancels_the_draft(buddy):
    buddy.on_draft(Draft(AMMA, "Reached safely"))
    buddy.on_heard("No.")
    assert buddy.sent_drafts == [] and not buddy.draft_card.isVisible()


def test_saying_yes_out_loud_sends_it(buddy):
    buddy.on_draft(Draft(RAVI, "Running 10 minutes late"))
    buddy.on_heard("Yeah.")
    assert buddy.sent_drafts == [Draft(RAVI, "Running 10 minutes late")]


def test_yes_without_a_draft_is_just_a_message(buddy, monkeypatch):
    asked = []
    monkeypatch.setattr(buddy.chat, "quick_open", lambda text, spoken: asked.append(text) or True)
    buddy.chat.ask("yes")
    assert asked == ["yes"] and buddy.sent_drafts == []


def test_a_failed_send_is_reported(buddy):
    draft = Draft(AMMA, "hi")
    buddy.on_draft(draft)
    buddy.answer_draft(True)
    buddy.on_sent(draft, False, "WhatsApp isn't connected")
    assert "couldn't send" in buddy.bubble.text
