"""Reads docs/ICP.md, the single source of truth for qualification and PoC rules.

Nothing about the ICP is written in code: this module only *parses* the markdown tables and lists.
If the file is edited in a way the parser can't read, you get a clear `ICPFormatError`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from outbound import PROJECT_ROOT

ICP_PATH = PROJECT_ROOT / "docs" / "ICP.md"


class ICPFormatError(ValueError):
    """docs/ICP.md could not be parsed (a heading or table column is missing)."""


@dataclass(frozen=True)
class Row:
    id: str
    name: str
    max_points: int
    critical: bool


@dataclass(frozen=True)
class Rule:
    id: str
    row: str
    points: int
    kind: str  # "presence" | "absence"
    question: str


@dataclass(frozen=True)
class Disqualifier:
    id: str
    name: str
    python_check: str
    question: str


@dataclass(frozen=True)
class PocTier:
    min_employees: int | None  # None = any size
    max_employees: int | None
    priority: int
    title: str
    keywords: tuple[str, ...]


@dataclass(frozen=True)
class ICP:
    text: str
    rows: dict[str, Row]
    rules: list[Rule]
    disqualifiers: list[Disqualifier]
    competitors: list[str]
    customers: list[str]
    sprinto_domains: list[str]
    settings: dict[str, int]
    poc_tiers: list[PocTier]
    personas_text: str
    needs_readable_trust_page: tuple[str, ...] = ()
    must_earn_rows: tuple[str, ...] = ()  # rubric rows that need a verified 'yes' before 'qualified'

    @property
    def rules_by_id(self) -> dict[str, Rule]:
        return {r.id: r for r in self.rules}


# ---------------------------------------------------------------- markdown helpers
def _section(text: str, heading_starts: str) -> str:
    """Return the body of the first `## ` section whose title starts with `heading_starts`."""
    pattern = re.compile(r"^##\s+(.*)$", re.MULTILINE)
    matches = list(pattern.finditer(text))
    for i, m in enumerate(matches):
        if m.group(1).strip().lower().startswith(heading_starts.lower()):
            end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
            return text[m.end() : end]
    raise ICPFormatError(f"docs/ICP.md has no section starting with '## {heading_starts}'")


def _tables(section: str) -> list[list[list[str]]]:
    """All markdown tables in a section, as lists of rows (header row first, separator removed)."""
    tables: list[list[list[str]]] = []
    current: list[list[str]] = []
    for line in section.splitlines() + [""]:
        stripped = line.strip()
        if stripped.startswith("|") and stripped.endswith("|"):
            cells = [c.strip() for c in stripped.strip("|").split("|")]
            if all(re.fullmatch(r":?-{2,}:?", c) for c in cells):
                continue
            current.append(cells)
        elif current:
            tables.append(current)
            current = []
    return tables


def _table(section: str, index: int, min_cols: int, name: str) -> list[list[str]]:
    tables = _tables(section)
    if len(tables) <= index:
        raise ICPFormatError(f"docs/ICP.md: table '{name}' not found")
    body = tables[index][1:]
    for row in body:
        if len(row) < min_cols:
            raise ICPFormatError(f"docs/ICP.md: table '{name}' row has too few columns: {row}")
    return body


def _bullet_domains(section: str) -> list[str]:
    """`- getastra.com (note)` -> `getastra.com`"""
    domains = []
    for line in section.splitlines():
        m = re.match(r"^\s*[-*]\s+([a-z0-9.-]+\.[a-z]{2,})", line.strip().lower())
        if m:
            domains.append(m.group(1))
    return domains


def _int(value: str, where: str) -> int:
    try:
        return int(value.replace("−", "-").replace("+", "").strip())
    except ValueError as exc:
        raise ICPFormatError(f"docs/ICP.md: '{value}' in {where} is not a whole number") from exc


def _settings_table(section: str, index: int, name: str) -> dict[str, int]:
    return {r[0]: _int(r[1], name) for r in _table(section, index, 2, name)}


def normalise_title(text: str) -> str:
    """'Co-Founder & CTO' -> ' co founder cto ' (spaces at both ends make whole-word matching easy)."""
    return " " + re.sub(r"[^a-z0-9]+", " ", text.lower()).strip() + " "


def _size_band(band: str) -> tuple[int | None, int | None]:
    if band.strip().lower() == "any":
        return None, None
    m = re.fullmatch(r"\s*(\d+)\s*-\s*(\d+)\s*", band)
    if not m:
        raise ICPFormatError(f"docs/ICP.md: size band '{band}' must look like '1-149' or 'any'")
    return int(m.group(1)), int(m.group(2))


# ---------------------------------------------------------------- main loader
def parse_icp(text: str) -> ICP:
    """Parse the ICP markdown text into an `ICP` object."""
    rows = {}
    for r in _table(_section(text, "Rubric rows"), 0, 4, "Rubric rows"):
        rows[r[0]] = Row(r[0], r[1], _int(r[2], "Rubric rows"), r[3].lower().startswith("y"))

    rules = []
    for r in _table(_section(text, "Scoring rules"), 0, 5, "Scoring rules"):
        if r[1] not in rows:
            raise ICPFormatError(f"docs/ICP.md: rule {r[0]} uses unknown row '{r[1]}'")
        rules.append(Rule(r[0], r[1], _int(r[2], "Scoring rules"), r[3].lower(), r[4]))

    dqs = [Disqualifier(r[0], r[1], r[2], r[3]) for r in _table(_section(text, "Hard disqualifiers"), 0, 4, "Hard disqualifiers")]

    poc_section = _section(text, "Who is the right PoC")
    tiers = []
    for r in _table(poc_section, 0, 4, "PoC titles"):
        lo, hi = _size_band(r[0])
        keywords = tuple(k.strip().lower() for k in r[3].split(",") if k.strip())
        tiers.append(PocTier(lo, hi, _int(r[1], "PoC titles"), r[2], keywords))

    settings = _settings_table(_section(text, "Verdicts"), 0, "Verdicts")
    settings.update(_settings_table(poc_section, 1, "PoC settings"))

    try:  # optional section; older ICP files without it still load
        trust_rules = tuple(re.findall(r"^\s*[-*]\s+([A-Z][A-Z0-9_]+)\s*$", _section(text, "Rules that need a readable trust page"), re.MULTILINE))
    except ICPFormatError:
        trust_rules = ()
    known_ids = {r.id for r in rules}
    if unknown_ids := [r for r in trust_rules if r not in known_ids]:
        raise ICPFormatError(f"docs/ICP.md: 'Rules that need a readable trust page' lists unknown rule ids {unknown_ids}")

    try:  # optional section
        must_earn = tuple(re.findall(r"^\s*[-*]\s+([A-Z][A-Z0-9_]+)\s*$", _section(text, "Rows that must earn points"), re.MULTILINE))
    except ICPFormatError:
        must_earn = ()
    if unknown_rows := [r for r in must_earn if r not in rows]:
        raise ICPFormatError(f"docs/ICP.md: 'Rows that must earn points' lists unknown row ids {unknown_rows}")

    return ICP(
        text=text,
        rows=rows,
        rules=rules,
        disqualifiers=dqs,
        competitors=_bullet_domains(_section(text, "Known competitors")),
        customers=_bullet_domains(_section(text, "Known Sprinto customers")),
        sprinto_domains=_bullet_domains(_section(text, "Sprinto's own domains")),
        settings=settings,
        poc_tiers=tiers,
        personas_text=_section(text, "Typical pain points").strip(),
        needs_readable_trust_page=trust_rules,
        must_earn_rows=must_earn,
    )


@lru_cache(maxsize=1)
def load_icp(path: Path = ICP_PATH) -> ICP:
    """Read and parse docs/ICP.md once per process."""
    return parse_icp(Path(path).read_text(encoding="utf-8"))


def domain_in_list(domain: str, listed: list[str]) -> str | None:
    """Return the listed domain that `domain` equals or is a subdomain of (e.g. trust.vanta.com -> vanta.com)."""
    domain = domain.lower().strip(".")
    for item in listed:
        if domain == item or domain.endswith("." + item):
            return item
    return None
