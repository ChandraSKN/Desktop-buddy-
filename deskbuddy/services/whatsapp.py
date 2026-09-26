"""WhatsApp messages the user asks for: finding the contact, and the yes/no that sends them.

Buddy is linked to the user's WhatsApp as a companion device (like WhatsApp Web) by a
helper process, services/whatsapp_worker.py, which the UI runs (ui/whatsapp.py). The
assistant can only *draft* a message; it goes out when the user confirms it (Send on the
card, or "yes" out loud). So a misheard name, or text in a meeting invite that tries to
instruct the assistant, can never send anything by itself.

Nothing here talks to WhatsApp, so it's testable without an account."""

import hashlib
import re
from dataclasses import dataclass
from difflib import SequenceMatcher


@dataclass(frozen=True)
class Contact:
    jid: str                  # 919876543210@s.whatsapp.net
    name: str                 # as saved in the phone's address book (or their own name)

    @property
    def key(self):
        """What the assistant sees instead of the number, so it can't make one up."""
        return hashlib.sha1(self.jid.encode()).hexdigest()[:8]

    @property
    def phone(self):
        return "+" + self.jid.split("@")[0] if self.jid.endswith("@s.whatsapp.net") else ""


@dataclass(frozen=True)
class Draft:
    contact: Contact
    text: str


def _words(text):
    return re.findall(r"[a-z0-9ఀ-౿]+", text.lower())


def match_contacts(query, contacts, limit=5):
    """Contacts whose name fits `query` ("Ravi", "ravi kumar", "Amma"), best first. Whole
    words and word starts count most; close spellings (speech recognition) still match."""
    q = _words(query)
    if not q:
        return []
    digits = re.sub(r"\D", "", query)
    scored = []
    for c in contacts:
        if len(digits) >= 6 and digits in c.jid:
            scored.append((2.0, c))
            continue
        names = _words(c.name)
        if not names:
            continue
        per_word = []
        for w in q:
            best = 0.0
            for n in names:
                if n == w:
                    best = 1.0
                elif n.startswith(w) and len(w) >= 3:
                    best = max(best, 0.9)
                else:
                    best = max(best, SequenceMatcher(None, w, n).ratio() * 0.85)
            per_word.append(best)
        score = sum(per_word) / len(per_word)
        if score >= 0.7:
            scored.append((score, c))
    scored.sort(key=lambda sc: (-sc[0], sc[1].name.lower()))
    return [c for _, c in scored[:limit]]


_YES = re.compile(r"^(yes|yeah|yep|yup|ya|haa?|han|avunu|sure|ok|okay|confirm|correct|right|"
                  r"send|send it|send that|please send|yes send|yes please|go ahead|do it)"
                  r"( (it|that|now|please|buddy|send it|go ahead))*$")
_NO = re.compile(r"^(no|nope|nah|cancel|stop|dont|dont send|dont send it|do not send|"
                 r"cancel it|never mind|nevermind|leave it|wait)( (it|that|buddy|please))*$")


def _plain(text):
    return " ".join(_words(text.replace("'", "").replace("’", "")))


def is_yes(text):
    return bool(_YES.match(_plain(text)))


def is_no(text):
    return bool(_NO.match(_plain(text)))


def clean_message(text):
    """The message body as it'll be sent: trimmed, no quotes around the whole thing."""
    text = text.strip()
    if len(text) >= 2 and text[0] in "\"'“‘" and text[-1] in "\"'”’":
        text = text[1:-1].strip()
    return text
