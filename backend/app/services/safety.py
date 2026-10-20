"""Rule-based safety signals. They need no model, give the same answer every time, and can only
ever make the assistant more careful (never less).
"""

from __future__ import annotations

import re

# Text that tries to steer the assistant rather than ask the bank something.
_INJECTION = [
    r"\bignore (?:all |any |the )?(?:previous|prior|above|earlier) (?:instructions|rules|prompts?)\b",
    r"\bdisregard (?:all |any |the )?(?:previous|prior|above|your) (?:instructions|rules)\b",
    r"\b(?:system|developer) prompt\b",
    r"\byou are now\b",
    r"\bact as (?:an?|the) (?:admin|administrator|developer|bank manager|system)\b",
    r"\bnew instructions?\s*:",
    r"\bdo not (?:tell|inform) (?:the )?(?:staff|officers?|humans?|anyone)\b",
    r"\b(?:reveal|print|show|repeat) (?:your|the) (?:instructions|prompt|rules|configuration)\b",
    r"\bjailbreak\b",
    r"<\s*/?\s*(?:system|assistant|instructions?)\s*>",
    r"\[\s*(?:system|inst)\s*\]",
    r"\bapprove (?:this|the) (?:refund|transfer|reversal)\b",
    r"\b(?:transfer|send|move) (?:ghs|ghc|usd|\$|¢)?\s?\d[\d,.]*\s+(?:to|into) (?:account|acct)\b",
]
_INJECTION_RE = [re.compile(p, re.IGNORECASE) for p in _INJECTION]

# Signs of an urgent security problem; these route a case to restricted urgent handling.
_URGENT = [
    r"\b(?:fraud|fraudulent|scam(?:med)?|phishing)\b",
    r"\b(?:hacked|compromised|account takeover)\b",
    r"\bunauthori[sz]ed (?:transaction|debit|withdrawal|access|transfer|login|payment)s?\b",
    r"\b(?:stolen|lost) (?:card|phone|sim)\b",
    r"\b(?:did not|didn't|never) (?:make|authori[sz]e) (?:this|these|the) "
    r"(?:transaction|transfer|payment|withdrawal)s?\b",
    r"\b(?:someone|somebody) (?:has |have )?(?:accessed|used|withdrew|transferred)\b",
    r"\b(?:sim swap|otp (?:sent|received) (?:without|that i did not))\b",
    # Gave away or was asked for a secret: possible social engineering in progress.
    r"\b(?:gave|shared|told|sent) (?:them |him |her |someone |the caller )?(?:my |the )?"
    r"(?:otp|pin|password|one[- ]time (?:code|password)|cvv)\b",
    r"\basked (?:me )?(?:for|to (?:give|share|send)) (?:my |the )?(?:otp|pin|password|cvv)\b",
    # Locked out without having done it themselves.
    r"\b(?:locked|blocked|suspended)\b.{0,60}\b(?:i did not|i didn't|not me)\b",
    # Twi: "my card is lost" (ayera = lost).
    r"\bcard\b.{0,20}\bayera\b",
]
_URGENT_RE = [re.compile(p, re.IGNORECASE) for p in _URGENT]

# Twi / Akan: special letters, and words unlikely to appear in English text.
_TWI_LETTERS = re.compile(r"[ɛɔƐƆ]")
# Words that are also English ("me", "den", "ben") are left out on purpose.
_TWI_WORDS = {
    "medaase",
    "meda",
    "wo",
    "mepa",
    "kyew",
    "kyɛw",
    "akwaaba",
    "yɛ",
    "deɛn",
    "ɛte",
    "sɛn",
    "nti",
    "mente",
    "aseɛ",
    "sika",
    "nnipa",
    "ɛnnɛ",
    "ɔkwan",
    "anadwo",
    "adwuma",
    "mesrɛ",
    "mesre",
    "wɔ",
    "biara",
    "bɛn",
    "ɛhe",
    "ɛyɛ",
    "mmom",
    "ampa",
    "ɛfiri",
    "efiri",
    "nanso",
    "ɛno",
    "saa",
}
_TWI_STRONG = {
    "medaase",
    "mepa",
    "kyew",
    "kyɛw",
    "akwaaba",
    "deɛn",
    "mesrɛ",
    "mesre",
    "ɛnnɛ",
    "biara",
    "mente",
}
_WORD = re.compile(r"[a-zɛɔ]+", re.IGNORECASE)

# Things an answer to a customer must never contain or ask for.
_CARD = re.compile(r"\b(?:\d[ -]?){13,19}\b")
_ACCOUNT = re.compile(r"\b\d{8,}\b")
_ASKS_SECRET = re.compile(
    r"\b(?:send|share|provide|give|tell|confirm|reply with|email)\b[^.\n]{0,60}"
    r"\b(?:pin|password|passcode|otp|one[- ]time (?:password|code)|cvv|cvc|security code|full card number)\b",
    re.IGNORECASE,
)
_COMMITMENT = re.compile(
    r"\b(?:we have (?:refunded|reversed|credited|unblocked|reset|resolved)"
    r"|has been (?:refunded|reversed|credited|resolved)"
    r"|we (?:guarantee|promise)|guaranteed|we will (?:refund|reverse|credit|compensate)"
    r"|you will (?:receive|be refunded|be credited))\b",
    re.IGNORECASE,
)


def injection_signals(text: str) -> list[str]:
    return [m.group(0)[:80] for r in _INJECTION_RE if (m := r.search(text))]


def urgent_signals(text: str) -> list[str]:
    return [m.group(0)[:80] for r in _URGENT_RE if (m := r.search(text))]


def detect_language(text: str) -> str:
    """'en', 'tw' (Twi/Akan), 'mixed', or 'other'. Rough by design: unsure means a person decides."""
    words = [w.lower() for w in _WORD.findall(text)]
    if not words:
        return "en"
    twi = sum(1 for w in words if w in _TWI_WORDS)
    strong = sum(1 for w in words if w in _TWI_STRONG) + len(_TWI_LETTERS.findall(text))
    if strong == 0 and twi < 3:
        return "en"
    share = twi / len(words)
    return "tw" if share > 0.4 else "mixed"


def unsafe_answer_content(answer: str, evidence_text: str) -> list[str]:
    """Problems in a draft answer: secrets asked for, numbers not from the sources, promises."""
    problems: list[str] = []
    if _ASKS_SECRET.search(answer):
        problems.append("asks the customer for a PIN, password, OTP or card security details")
    digits_in_evidence = set(re.findall(r"\d+", evidence_text))
    for m in list(_CARD.finditer(answer)) + list(_ACCOUNT.finditer(answer)):
        if re.sub(r"\D", "", m.group(0)) not in digits_in_evidence:
            problems.append("contains an account or card-like number that is not in the sources")
            break
    if m := _COMMITMENT.search(answer):
        problems.append(f"makes a commitment the bank has not approved (“{m.group(0)}”)")
    return problems
