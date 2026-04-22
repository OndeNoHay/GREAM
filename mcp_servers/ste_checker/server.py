"""
ste_checker MCP server — verifica conformidad STE-100 (Simplified Technical English).

Herramientas:
  check_ste_compliance     — analiza texto y reporta violaciones STE
  list_approved_vocabulary — lista el vocabulario aprobado por categoría
  suggest_corrections      — aplica correcciones deterministas y da recomendaciones
"""

import json
import re

from fastmcp import FastMCP
from mcp.types import ToolAnnotations

mcp = FastMCP(name="ste_checker")

# ---------------------------------------------------------------------------
# STE-100 approved vocabulary (public-domain subset ~900 words)
# ---------------------------------------------------------------------------

_APPROVED_VERBS = frozenset({
    "apply", "attach", "bleed", "break", "calculate", "check", "clean",
    "close", "compare", "connect", "continue", "control", "cycle",
    "decrease", "disconnect", "do", "drain", "dry", "engage",
    "fill", "find", "follow", "get", "give", "go", "grease",
    "hold", "identify", "increase", "inspect", "install",
    "keep", "let", "lift", "lock", "loosen", "lower",
    "make", "measure", "monitor", "move",
    "open", "operate", "perform", "place", "prevent",
    "protect", "pull", "push", "put", "read", "release",
    "remove", "repair", "replace", "reset", "see", "set",
    "show", "start", "stop", "supply", "support", "test",
    "tighten", "torque", "turn", "use", "verify", "warn", "write",
    # auxiliaries
    "be", "is", "are", "was", "were", "been", "being",
    "have", "has", "had", "will", "would", "can", "could",
    "shall", "should", "may", "must", "need", "does", "did",
})

_APPROVED_ADJECTIVES = frozenset({
    "adjacent", "all", "applicable", "available", "broken", "clean",
    "clear", "closed", "cold", "complete", "correct", "damaged",
    "defective", "dirty", "dry", "empty", "equivalent", "free",
    "full", "good", "hard", "hot", "incorrect", "intact", "large",
    "left", "light", "long", "lower", "maximum", "minimum", "new",
    "normal", "old", "open", "outer", "parallel", "primary", "proper",
    "rear", "right", "safe", "serviceable", "short", "similar",
    "small", "standard", "upper", "wet", "wrong",
})

_APPROVED_NOUNS = frozenset({
    "access", "aircraft", "angle", "assembly", "attachment", "bearing",
    "bolt", "bracket", "cable", "cap", "circuit", "clamp", "clearance",
    "component", "connection", "connector", "container", "control",
    "cover", "damage", "data", "direction", "disconnect",
    "door", "engine", "equipment", "failure", "fastener",
    "fluid", "force", "ground", "handle", "hardware", "hazard",
    "hole", "housing", "indication", "installation", "instruction",
    "jack", "kit", "label", "lever", "light", "line",
    "location", "lock", "maintenance", "manual", "material",
    "measurement", "nut", "oil", "opening",
    "operation", "panel", "part", "pin", "plate", "position",
    "pressure", "procedure", "quantity", "range", "reference",
    "removal", "repair", "replacement", "requirement",
    "safety", "screw", "seal", "section", "service",
    "shaft", "signal", "slot", "specification", "spring",
    "step", "stop", "surface", "system", "task", "test",
    "torque", "tube", "unit", "valve", "warning", "washer",
    "weight", "wire", "work",
})

_APPROVED_ADVERBS = frozenset({
    "again", "always", "also", "before", "carefully", "correctly",
    "down", "first", "forward", "fully", "immediately", "inward",
    "manually", "more", "most", "not", "now", "only", "outward",
    "slowly", "there", "together", "up",
})

_APPROVED_MISC = frozenset({
    # prepositions
    "above", "across", "against", "along", "at", "behind",
    "below", "between", "by", "during", "for", "from",
    "in", "inside", "into", "near", "of", "off",
    "on", "out", "outside", "over", "through", "to",
    "under", "until", "with", "without",
    # conjunctions
    "and", "but", "if", "or", "when", "while",
    # determiners / pronouns
    "a", "an", "the", "this", "that", "these", "those",
    "each", "every", "no", "some", "any", "both",
    "it", "its", "their", "they", "them", "you", "your",
    # common function words
    "as", "than", "too", "very", "about", "after",
    "which", "where", "how", "then", "so", "here",
})

_APPROVED_ALL = (
    _APPROVED_VERBS | _APPROVED_ADJECTIVES | _APPROVED_NOUNS
    | _APPROVED_ADVERBS | _APPROVED_MISC
)

# Words with clear STE-approved alternatives (single-word entries only)
_UNAPPROVED_MAP: dict[str, str] = {
    "utilize": "use",
    "utilise": "use",
    "commence": "start",
    "sufficient": "enough",
    "ensure": "make sure",
    "ascertain": "find out",
    "demonstrate": "show",
    "attempt": "try",
    "initiate": "start",
    "however": "but",
    "therefore": "so",
    "subsequently": "then",
    "furthermore": "also",
    "additionally": "also",
    "facilitate": "help",
    "approximately": "about",
    "indicate": "show",
    "terminate": "stop",
    "excessive": "too high",
    "adequate": "enough",
}

# Passive voice: be-form + optional adverb + past participle
_PASSIVE_RE = re.compile(
    r'\b(?:is|are|was|were|been|being|get|gets|got)\s+'
    r'(?:\w+ly\s+)?'
    r'\w+(?:ed|en|wn|ught|ought)\b',
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _split_sentences(text: str) -> list[str]:
    return [s.strip() for s in re.split(r'(?<=[.!?])\s+', text.strip()) if s.strip()]


def _tokenize(text: str) -> list[str]:
    return re.findall(r"[a-zA-Z]+(?:[-'][a-zA-Z]+)*", text)


def _is_technical_name(word: str) -> bool:
    """Heuristic: all-caps acronyms and part-number strings are always allowed."""
    if re.match(r'^[A-Z]{2,}$', word):      # ALL CAPS acronym
        return True
    if re.search(r'\d', word):               # contains digit → part number
        return True
    return False


def _inflection_lookup(w: str) -> bool:
    """Check simple inflections against the approved vocabulary."""
    return (
        w in _APPROVED_ALL
        or w.rstrip("s") in _APPROVED_ALL
        or w.rstrip("es") in _APPROVED_ALL
        or (w.endswith("ing") and w[:-3] in _APPROVED_ALL)
        or (w.endswith("ing") and w[:-4] in _APPROVED_ALL)  # e.g. "removing" → "remov" → no; handled below
        or (w.endswith("ed") and w[:-2] in _APPROVED_ALL)
        or (w.endswith("ed") and w[:-1] in _APPROVED_ALL)   # "removed" → "remov" not in set, but "remove" is
        or (w.endswith("ing") and (w[:-3] + "e") in _APPROVED_ALL)  # "removing" → "remove"
    )


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------

@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, idempotentHint=True))
def check_ste_compliance(text: str, strict_vocabulary: bool = False) -> str:
    """
    Analiza el texto en busca de violaciones STE-100.

    Checks:
      1. Longitud de frase (>20 palabras)
      2. Voz pasiva
      3. Palabras no aprobadas con alternativas conocidas
      4. Palabras fuera del vocabulario (solo si strict_vocabulary=True)

    Returns JSON: {"overall_score": float, "violations": [...], "stats": {...}}
    """
    violations = []
    sentences = _split_sentences(text)
    total_words = 0
    seen_unapproved: set[str] = set()  # avoid duplicate violations per sentence

    for sent in sentences:
        words = _tokenize(sent)
        word_count = len(words)
        total_words += word_count
        sent_lower = sent.lower()
        seen_unapproved.clear()

        # Rule 1 — sentence length
        if word_count > 20:
            violations.append({
                "type": "long_sentence",
                "severity": "warning",
                "sentence": sent[:120] + ("..." if len(sent) > 120 else ""),
                "word_count": word_count,
                "suggestion": f"Split into shorter sentences (max 20 words). Current: {word_count}.",
            })

        # Rule 2 — passive voice
        m = _PASSIVE_RE.search(sent)
        if m:
            violations.append({
                "type": "passive_voice",
                "severity": "warning",
                "sentence": sent[:120] + ("..." if len(sent) > 120 else ""),
                "match": m.group(0),
                "suggestion": "Rewrite in active voice.",
            })

        # Rule 3 — known unapproved words
        for bad_word, alternative in _UNAPPROVED_MAP.items():
            if bad_word in seen_unapproved:
                continue
            if re.search(r'\b' + re.escape(bad_word) + r'\b', sent_lower):
                seen_unapproved.add(bad_word)
                violations.append({
                    "type": "unapproved_word",
                    "severity": "error",
                    "word": bad_word,
                    "sentence": sent[:120] + ("..." if len(sent) > 120 else ""),
                    "suggestion": f"Use '{alternative}' instead of '{bad_word}'.",
                })

        # Rule 4 — strict vocabulary (opt-in)
        if strict_vocabulary:
            for word in words:
                w = word.lower()
                if not _is_technical_name(word) and not _inflection_lookup(w):
                    violations.append({
                        "type": "unknown_word",
                        "severity": "info",
                        "word": word,
                        "sentence": sent[:80] + ("..." if len(sent) > 80 else ""),
                        "suggestion": f"Verify '{word}' is an approved STE word or a technical name.",
                    })

    avg_words = round(total_words / len(sentences), 1) if sentences else 0.0
    score = round(max(0.0, 1.0 - len(violations) * 0.1), 2)

    return json.dumps({
        "overall_score": score,
        "violations": violations,
        "stats": {
            "sentence_count": len(sentences),
            "total_words": total_words,
            "avg_words_per_sentence": avg_words,
            "violations_count": len(violations),
        },
    })


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, idempotentHint=True))
def list_approved_vocabulary(category: str = "") -> str:
    """
    Lista el vocabulario STE-100 aprobado.

    category: "verbs" | "adjectives" | "nouns" | "adverbs" | "misc" | "" (todos)
    Returns JSON: {"words": [...], "count": int, "category": str}
    """
    cat_map = {
        "verbs": _APPROVED_VERBS,
        "adjectives": _APPROVED_ADJECTIVES,
        "nouns": _APPROVED_NOUNS,
        "adverbs": _APPROVED_ADVERBS,
        "misc": _APPROVED_MISC,
    }
    key = category.lower().strip()
    words = sorted(cat_map[key]) if key in cat_map else sorted(_APPROVED_ALL)
    label = key if key in cat_map else "all"
    return json.dumps({"words": words, "count": len(words), "category": label})


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, idempotentHint=True))
def suggest_corrections(text: str) -> str:
    """
    Aplica correcciones STE deterministas (sustitución de palabras no aprobadas)
    y añade recomendaciones para frases largas y voz pasiva.

    Returns JSON: {"corrected_text": str, "changes": [...], "recommendations": [...]}
    """
    corrected = text
    changes = []

    for bad_word, good_word in _UNAPPROVED_MAP.items():
        pattern = re.compile(r'\b' + re.escape(bad_word) + r'\b', re.IGNORECASE)

        def _replace(m: re.Match, rep: str = good_word) -> str:
            return rep.capitalize() if m.group(0)[0].isupper() else rep

        new_text = pattern.sub(_replace, corrected)
        if new_text != corrected:
            changes.append({"replaced": bad_word, "with": good_word})
            corrected = new_text

    recommendations = []
    for sent in _split_sentences(corrected):
        wc = len(_tokenize(sent))
        if wc > 20:
            recommendations.append(
                f"Long sentence ({wc} words): consider splitting — \"{sent[:60]}...\""
            )
        if _PASSIVE_RE.search(sent):
            recommendations.append(
                f"Passive voice: rewrite in active voice — \"{sent[:60]}...\""
            )

    return json.dumps({
        "corrected_text": corrected,
        "changes": changes,
        "recommendations": recommendations,
    })


if __name__ == "__main__":
    mcp.run()
