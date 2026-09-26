"""Telugu script → Latin letters, and a sound key for matching names across scripts.

English words spoken in Telugu often come back from speech recognition in Telugu script:
"Firefox" as ఫైర్‌ఫాక్స్, "calculator" as కాలిక్యులేటర్. Telugu spelling is phonetic, so
spelling it out ("phairphaaks") and comparing consonant skeletons ("frfks") finds the
app without any translation service."""

import re

_CONSONANTS = {
    "క": "k", "ఖ": "kh", "గ": "g", "ఘ": "gh", "ఙ": "n", "చ": "ch", "ఛ": "chh", "జ": "j", "ఝ": "jh",
    "ఞ": "n", "ట": "t", "ఠ": "th", "డ": "d", "ఢ": "dh", "ణ": "n", "త": "t", "థ": "th", "ద": "d",
    "ధ": "dh", "న": "n", "ప": "p", "ఫ": "ph", "బ": "b", "భ": "bh", "మ": "m", "య": "y", "ర": "r",
    "ఱ": "r", "ల": "l", "ళ": "l", "వ": "v", "శ": "sh", "ష": "sh", "స": "s", "హ": "h",
}
_VOWELS = {"అ": "a", "ఆ": "aa", "ఇ": "i", "ఈ": "ii", "ఉ": "u", "ఊ": "uu", "ఋ": "ru", "ఎ": "e",
           "ఏ": "ee", "ఐ": "ai", "ఒ": "o", "ఓ": "oo", "ఔ": "au"}
_SIGNS = {"ా": "aa", "ి": "i", "ీ": "ii", "ు": "u", "ూ": "uu", "ృ": "ru", "ె": "e", "ే": "ee",
          "ై": "ai", "ొ": "o", "ో": "oo", "ౌ": "au", "ః": "h"}
_ANUSVARA = "ం"                                     # a nasal: "m" before p/b/m, else "n"
_VIRAMA = "్"


def has_telugu(text):
    return any("ఀ" <= c <= "౿" for c in text)


def to_latin(text):
    """ఫైర్‌ఫాక్స్ → phairphaaks (approximate, for matching only)."""
    out, chars = [], list(text)
    for i, c in enumerate(chars):
        nxt = chars[i + 1] if i + 1 < len(chars) else ""
        if c in _CONSONANTS:
            out.append(_CONSONANTS[c])
            if nxt not in _SIGNS and nxt != _VIRAMA:
                out.append("a")                     # inherent vowel
        elif c in _VOWELS:
            out.append(_VOWELS[c])
        elif c in _SIGNS:
            out.append(_SIGNS[c])
        elif c == _ANUSVARA:
            out.append("m" if _CONSONANTS.get(nxt, "")[:1] in ("p", "b", "m") else "n")
        elif c in (_VIRAMA, "‌", "‍"):
            continue
        else:
            out.append(c)
    return "".join(out)


def sound_key(text):
    """Consonant skeleton that survives spelling differences: firefox / phairphaaks → frfks."""
    t = to_latin(text).lower() if has_telugu(text) else text.lower()
    t = re.sub(r"(?<=[aeiou])w", "", t)             # "down", "saw": the w is part of the vowel
    for a, b in (("chr", "kr"), ("ph", "f"), ("x", "ks"), ("ck", "k"), ("qu", "kw"), ("c", "k"),
                 ("q", "k"), ("w", "v"), ("z", "s"), ("th", "t"), ("dh", "d"), ("bh", "b"),
                 ("kh", "k"), ("gh", "g"), ("sh", "s")):
        t = t.replace(a, b)
    t = re.sub(r"[^a-z]", "", t)
    t = re.sub(r"[aeiouy]", "", t)
    return re.sub(r"(.)\1+", r"\1", t)
