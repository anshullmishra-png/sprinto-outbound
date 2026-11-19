"""Step 3: qualify a company against docs/ICP.md, with an agent loop that fetches more evidence.

Who does what (this split is the core of the design):
- Python checks the hard disqualifier LISTS first (competitors, known Sprinto customers, a link to a
  Sprinto-hosted trust center, not-B2B / too-big from the profile). No LLM call is needed for those.
- The LLM only answers each ICP question with yes / no / unknown + evidence (prompts/qualify.md).
- Python verifies every quote, applies the fixed points from ICP.md, caps rows and total, and decides.
  `score_signals()` is a pure function: the same signals always give the same score.
- Agent loop: while the company is borderline, has critical unknowns, or still needs the
  "<company> Sprinto customer" check, the LLM picks ONE action (fetch_page / web_search / decide),
  we run it, add the new evidence, and re-score. Max MAX_AGENT_ITERATIONS, and it stops early if the
  LLM budget runs low (3 calls are always kept for the pain-point and email steps).
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import date
from urllib.parse import urlparse

from outbound import llm
from outbound.config import Settings
from outbound.evidence import EvidenceCorpus
from outbound.icp import ICP, domain_in_list, load_icp
from outbound.models import (
    AgentAction,
    AgentIteration,
    CompanyProfile,
    Evidence,
    PageContent,
    QualificationResult,
    ScoreLine,
    ScrapeResult,
    Signal,
    SignalReport,
)
from outbound.scrape import FALLBACK_PATHS, Fetcher, clean_url, fetch_extra_page, same_site

log = logging.getLogger(__name__)

CALLS_PER_ITERATION = 3  # choose action + web search + re-extract signals
RESERVED_CALLS = 3  # pain points (1) + email draft (up to 2)
ICP_SOURCE = "docs/ICP.md"


@dataclass
class QualifyContext:
    domain: str
    scrape: ScrapeResult
    profile: CompanyProfile
    corpus: EvidenceCorpus
    settings: Settings
    icp: ICP = field(default_factory=load_icp)
    fetcher: Fetcher | None = None
    searches: list[str] = field(default_factory=list)
    extra_pages: list[str] = field(default_factory=list)
    extra_page_records: list[PageContent] = field(default_factory=list)  # pages the agent loop fetched

    @property
    def company(self) -> str:
        return self.profile.name or self.domain

    def unreadable_pages(self) -> list[PageContent]:
        """Pages that exist (or may exist) but whose content we could not read. 404/410 = really missing, so excluded."""
        return [p for p in self.scrape.pages + self.extra_page_records
                if not p.readable and p.status_code not in (404, 410)]

    def trust_page_blockers(self) -> list[PageContent]:
        """Unreadable pages that would hold certifications (trust / security / compliance)."""
        return [p for p in self.unreadable_pages()
                if any(k in (urlparse(p.url).hostname or "") + urlparse(p.url).path.lower() for k in TRUST_PAGE_HINTS)]

    def fetchable_pages(self) -> list[str]:
        """Same-site pages not fetched yet: links found by the scraper, then common fallback paths."""
        done = {clean_url(p.url) for p in self.scrape.pages} | {clean_url(u) for u in self.extra_pages}
        home = next((p.url for p in self.scrape.pages if not p.error), f"https://{self.domain}/")
        root = f"{urlparse(home).scheme}://{urlparse(home).netloc}"
        urls = list(self.scrape.candidate_links) + [root + path for path in FALLBACK_PATHS]
        out: list[str] = []
        for u in urls:
            cu = clean_url(u)
            if cu not in done and cu not in out:
                out.append(cu)
        return out


# Pages most likely to state headcount / funding (used by the Python fallback below).
STAGE_PAGE_HINTS = ("careers", "jobs", "about", "company", "team", "press", "news", "funding", "investors", "announc")
# Where certifications live. If one of these pages exists but can't be read, "no SOC 2 found" proves nothing.
TRUST_PAGE_HINTS = ("trust", "security", "compliance")


def page_problem(p: PageContent) -> str:
    return p.error or p.quality_note or p.quality


# ---------------------------------------------------------------- hard disqualifiers (Python)
def list_disqualifier(domain: str, scrape: ScrapeResult, icp: ICP) -> tuple[str, str, list[Evidence]] | None:
    """Checks that need no LLM: ICP.md lists and a Sprinto-hosted trust center link."""
    if hit := domain_in_list(domain, icp.competitors):
        return ("DQ_COMPLIANCE_VENDOR", f"{domain} is in the Known competitors list in {ICP_SOURCE}",
                [Evidence(claim=f"{domain} is a compliance-automation vendor (or Sprinto itself)", source_url=ICP_SOURCE, quote=f"- {hit}")])
    if hit := domain_in_list(domain, icp.customers):
        return ("DQ_SPRINTO_CUSTOMER", f"{domain} is in the Known Sprinto customers list in {ICP_SOURCE}",
                [Evidence(claim=f"{domain} is an existing Sprinto customer", source_url=ICP_SOURCE, quote=f"- {hit}")])
    sprinto_links = [s for s in scrape.detected_signals if s.signal == "sprinto_link"]
    if sprinto_links:
        s = sprinto_links[0]
        return ("DQ_SPRINTO_CUSTOMER", "the website links to a trust center hosted by Sprinto, so it is already a Sprinto customer",
                [Evidence(claim="Site links to a Sprinto-hosted trust center", source_url=s.source_url, quote=s.snippet)])
    return None


def employee_lower_bound(text: str | None) -> int | None:
    """'51-200' -> 51, '1,000+ employees' -> 1000, '10k+' -> 10000, 'about 300' -> 300."""
    if not text:
        return None
    numbers = []
    for num, k in re.findall(r"(\d[\d,]*(?:\.\d+)?)\s*(k)?\b", text.lower()):
        value = float(num.replace(",", ""))
        numbers.append(int(value * 1000) if k else int(value))
    return min(numbers) if numbers else None


def profile_disqualifier(profile: CompanyProfile, icp: ICP) -> tuple[str, str, list[Evidence]] | None:
    """Disqualifiers visible in the evidence-backed profile (still no LLM call)."""
    if profile.is_b2b_software is False:
        ev = [e for e in profile.evidence if e.field == "is_b2b_software"]
        return ("DQ_NOT_B2B", "the website shows it is not a B2B software / cloud product", list(ev))
    lower = employee_lower_bound(profile.employee_range)
    limit = icp.settings["late_stage_min_employees"]
    if lower is not None and lower > limit:
        ev = [e for e in profile.evidence if e.field == "employee_range"]
        return ("DQ_LATE_STAGE", f"employee count starts at {lower}, above {limit}", list(ev))
    return None


def signal_disqualifier(signals: list[Signal]) -> tuple[str, str, list[Evidence]] | None:
    """A DQ_ question answered 'yes' with verified evidence."""
    for s in signals:
        if s.rule_id.startswith("DQ_") and s.value == "yes" and s.evidence:
            return (s.rule_id, s.detail or s.evidence[0].claim, s.evidence)
    return None


# ---------------------------------------------------------------- deterministic scoring
def score_signals(signals: list[Signal], icp: ICP) -> tuple[int, list[ScoreLine]]:
    """Fixed points from ICP.md. yes + verified evidence = points; anything else = 0. Rows and total capped."""
    by_id = {s.rule_id: s for s in signals}
    lines: list[ScoreLine] = []
    total = 0
    for row in icp.rows.values():
        row_sum = 0
        for rule in (r for r in icp.rules if r.row == row.id):
            sig = by_id.get(rule.id)
            if sig is None or sig.value == "unknown":
                points, reason = 0, "unknown"
            elif sig.value == "no":
                points, reason = 0, "no"
            elif not sig.evidence:
                points, reason = 0, "said yes, but no verified evidence"
            else:
                points, reason = rule.points, f"yes: {sig.detail or sig.evidence[0].claim}"
            row_sum += points
            lines.append(ScoreLine(rule_id=rule.id, rubric_row=row.name, points=points, max_points=rule.points, reason=reason))
        capped = max(-row.max_points, min(row.max_points, row_sum))
        if capped != row_sum:
            lines.append(ScoreLine(rule_id=f"{row.id}_CAP", rubric_row=row.name, points=capped - row_sum, max_points=0,
                                   reason=f"row capped at {'+' if capped > 0 else ''}{capped}"))
        total += capped
    return max(0, min(100, total)), lines


def max_possible_score(signals: list[Signal], icp: ICP) -> int:
    """The score if every positive 'unknown' rule turned out to be 'yes' (used to reject hopeless cases)."""
    optimistic = []
    known = {s.rule_id: s for s in signals}
    for rule in icp.rules:
        sig = known.get(rule.id)
        if (sig is None or sig.value == "unknown") and rule.points > 0:
            optimistic.append(Signal(rule_id=rule.id, value="yes", evidence=[Evidence(claim="hypothetical", source_url="-", quote="-")]))
        elif sig is not None:
            optimistic.append(sig)
    return score_signals(optimistic, icp)[0]


def critical_unknowns(signals: list[Signal], profile: CompanyProfile, icp: ICP) -> list[str]:
    """Key facts still unknown: B2B status, and any critical rubric row where every rule is unknown."""
    by_id = {s.rule_id: s for s in signals}
    missing = []
    b2b_signal = by_id.get("DQ_NOT_B2B")
    if profile.is_b2b_software is None and (b2b_signal is None or b2b_signal.value == "unknown"):
        missing.append("B2B status")
    for row in icp.rows.values():
        if not row.critical:
            continue
        rules = [r for r in icp.rules if r.row == row.id]
        if all(by_id.get(r.id) is None or by_id[r.id].value == "unknown" for r in rules):
            missing.append(row.name)
    return missing


def required_rows_status(signals: list[Signal], icp: ICP) -> tuple[list[str], list[str]]:
    """Rows from ICP.md 'Rows that must earn points' that have not earned any points yet.

    Returns (failed, open): `failed` = every rule in the row is a plain 'no' (it can never earn points);
    `open` = no verified 'yes' yet, but at least one rule is still unknown (more research might find it).
    """
    by_id = {s.rule_id: s for s in signals}
    failed, still_open = [], []
    for row_id in icp.must_earn_rows:
        rules = [r for r in icp.rules if r.row == row_id and r.points > 0]
        sigs = [by_id.get(r.id) for r in rules]
        if any(s is not None and s.value == "yes" and s.evidence for s in sigs):
            continue
        name = icp.rows[row_id].name
        if all(s is not None and s.value == "no" for s in sigs):
            failed.append(name)
        else:
            still_open.append(name)
    return failed, still_open


def top_reasons(lines: list[ScoreLine], n: int = 2) -> list[str]:
    best = sorted((ln for ln in lines if ln.points > 0), key=lambda ln: -ln.points)[:n]
    return [f"{ln.rule_id} +{ln.points}: {ln.reason.removeprefix('yes: ')}" for ln in best]


# ---------------------------------------------------------------- LLM: signals + next action
def _questions(icp: ICP) -> str:
    lines = [f"- {d.id} (disqualifier): {d.question}" for d in icp.disqualifiers]
    lines += [f"- {r.id} (kind: {r.kind}): {r.question}" for r in icp.rules]
    return "\n".join(lines)


def _signals_text(ctx: QualifyContext) -> str:
    sigs = ctx.scrape.detected_signals
    return "\n".join(f"- {s.signal} | {s.source_url} | {s.snippet}" for s in sigs) or "(none)"


def extract_signals(ctx: QualifyContext) -> tuple[list[Signal], str]:
    """One LLM call: answer every ICP question. Python then verifies evidence and fills gaps."""
    report = llm.generate_json(
        "qualify",
        {
            "icp": ctx.icp.text,
            "questions": _questions(ctx.icp),
            "profile": ctx.profile.model_dump_json(indent=1, exclude={"evidence"}),
            "content": ctx.corpus.as_prompt_block(ctx.settings.max_chars_per_page),
            "signals": _signals_text(ctx),
            "unreadable": "\n".join(f"- {p.url}: {page_problem(p)}" for p in ctx.unreadable_pages()) or "(none)",
        },
        SignalReport,
    )
    return clean_signals(report.signals, ctx), report.summary


def merge_signals(previous: list[Signal], new: list[Signal]) -> list[Signal]:
    """Research rounds may only ADD verified knowledge, never silently lose it.

    Each round re-asks every ICP question, and the model sometimes answers "unknown" to something it proved last
    round. Fixed rule per rule id: an answer backed by verified evidence beats one without; between two evidenced
    answers, the newer one wins (so new evidence CAN overturn an old "yes", but only with its own quote).
    """
    prev = {s.rule_id: s for s in previous}
    merged = []
    for s in new:
        old = prev.get(s.rule_id)
        old_backed = old is not None and old.value != "unknown" and bool(old.evidence)
        new_backed = s.value != "unknown" and bool(s.evidence)
        merged.append(old if old_backed and not new_backed else s)
    seen = {s.rule_id for s in new}
    merged += [s for s in previous if s.rule_id not in seen]
    return merged


MONTHS = {m: i + 1 for i, m in enumerate(("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"))}


def latest_round_date(texts: list[str]) -> tuple[int, int] | None:
    """Newest (year, month) mentioned in the texts. 'June 2024' -> (2024, 6); a bare '2024' -> (2024, 12), the most
    favourable reading for the company. None if no date is mentioned."""
    found: list[tuple[int, int]] = []
    for text in texts:
        low = text.lower()
        for mon, year in re.findall(r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+(?:\d{1,2}(?:[/-]\d{1,2})?(?:st|nd|rd|th)?,?\s+)?((?:19|20)\d{2})\b", low):
            found.append((int(year), MONTHS[mon]))
        if not found:
            found += [(int(y), 12) for y in re.findall(r"\b((?:19|20)\d{2})\b", low)]
    return max(found) if found else None


def apply_funding_window(sig: Signal, window_months: int, today: date | None = None) -> Signal:
    """Fixed rule from ICP.md: a 'yes' only stands if the round's date is visible and within the window."""
    if sig.value != "yes" or not sig.evidence:
        return sig
    texts = [sig.detail] + [e.quote for e in sig.evidence] + [e.claim for e in sig.evidence]
    when = latest_round_date(texts)
    today = today or date.today()
    if when is None:
        return Signal(rule_id=sig.rule_id, value="unknown", evidence=sig.evidence,
                      detail=f"round date not shown, so the {window_months}-month window can't be checked ({sig.detail})")
    age = (today.year - when[0]) * 12 + (today.month - when[1])
    if age > window_months:
        return Signal(rule_id=sig.rule_id, value="no", evidence=sig.evidence,
                      detail=f"round dated {when[1]:02d}/{when[0]} is {age} months old (> {window_months}-month window): {sig.detail}")
    return Signal(rule_id=sig.rule_id, value="yes", evidence=sig.evidence,
                  detail=f"{sig.detail} ({age} months ago, inside the {window_months}-month window)")


def clean_signals(raw: list[Signal], ctx: QualifyContext) -> list[Signal]:
    """Keep known rule ids, verify evidence, add 'unknown' for missing rules, add Python-detected facts."""
    kinds = {r.id: r.kind for r in ctx.icp.rules} | {d.id: "presence" for d in ctx.icp.disqualifiers}
    by_id: dict[str, Signal] = {}
    for s in raw:
        if s.rule_id in kinds and s.rule_id not in by_id:
            s.evidence = ctx.corpus.filter(s.evidence, absence=kinds[s.rule_id] == "absence")
            by_id[s.rule_id] = s
    for rule_id in kinds:
        by_id.setdefault(rule_id, Signal(rule_id=rule_id, value="unknown"))

    # "Not found" only counts if we could read where it would be (ICP.md: Rules that need a readable trust page).
    if blockers := ctx.trust_page_blockers():
        b = blockers[0]
        for rule_id in ctx.icp.needs_readable_trust_page:
            sig = by_id.get(rule_id)
            if sig and sig.value == "yes":
                by_id[rule_id] = Signal(rule_id=rule_id, value="unknown",
                                        detail=f"can't confirm: {b.url} exists but could not be read ({page_problem(b)})")

    # Deterministic: the funding round must be inside the ICP.md window (the model reads "about 24 months" loosely).
    if "STAGE_RECENT_FUNDING" in by_id and "funding_window_months" in ctx.icp.settings:
        by_id["STAGE_RECENT_FUNDING"] = apply_funding_window(by_id["STAGE_RECENT_FUNDING"], ctx.icp.settings["funding_window_months"])

    # Deterministic: a competitor link/badge found by the scraper always counts for the penalty rule.
    comp_hits = [d for d in ctx.scrape.detected_signals if d.signal.startswith(("competitor_link:", "competitor_text:"))]
    if comp_hits and "COMP_ON_COMPETITOR" in by_id:
        d = comp_hits[0]
        by_id["COMP_ON_COMPETITOR"] = Signal(
            rule_id="COMP_ON_COMPETITOR", value="yes", detail=d.signal.split(":", 1)[1],
            evidence=[Evidence(claim=f"Site uses {d.signal.split(':', 1)[1]}", source_url=d.source_url, quote=d.snippet)],
        )
    return list(by_id.values())


def choose_action(ctx: QualifyContext, signals: list[Signal], score: int, unknowns: list[str], iteration: int) -> AgentAction:
    """Ask the LLM for ONE next action. Invalid choices become `decide` (guardrail)."""
    rules = {r.id: r for r in ctx.icp.rules}
    open_q = [f"- {s.rule_id}: {rules[s.rule_id].question} (+{rules[s.rule_id].points})"
              for s in signals if s.value == "unknown" and s.rule_id in rules and rules[s.rule_id].points > 0]
    open_q += [f"- {s.rule_id}: (disqualifier) {d.question}" for s in signals for d in ctx.icp.disqualifiers
               if d.id == s.rule_id and s.value == "unknown"]
    fetched = [p.url for p in ctx.scrape.pages if not p.error] + ctx.extra_pages
    action = llm.generate_json(
        "next_action",
        {
            "domain": ctx.domain,
            "company": ctx.company,
            "score": score,
            "qualified_min": ctx.icp.settings["qualified_min_score"],
            "borderline_min": ctx.icp.settings["borderline_min_score"],
            "critical_unknowns": ", ".join(unknowns) or "none",
            "iteration": iteration,
            "max_iterations": ctx.settings.max_agent_iterations,
            "open_questions": "\n".join(open_q) or "(none)",
            "fetched": "\n".join(f"- {u}" for u in fetched) or "(none)",
            "searches": "\n".join(f"- {q}" for q in ctx.searches) or "(none)",
            "search_note": "" if llm.search_available() else
            "IMPORTANT: `web_search` is NOT available right now. Use `fetch_page` with one of the pages listed below.",
            "candidates": "\n".join(f"- {u}" for u in ctx.fetchable_pages()[:20]) or "(none left)",
        },
        AgentAction,
    )
    return fallback_if_giving_up(validate_action(action, ctx), ctx, unknowns)


def fallback_if_giving_up(action: AgentAction, ctx: QualifyContext, unknowns: list[str]) -> AgentAction:
    """Fixed rule: don't stop while a critical fact is unknown and a likely page (about/careers/press...) is unread.

    Small models tend to give up early. If the LLM picks `decide` in that situation, Python fetches the
    most promising unread page instead, and says so in `why`.
    """
    if action.action != "decide" or not unknowns:
        return action
    def rung(url: str) -> int:  # L4 evidence map: careers is rung one for stage (headcount, roles, often funding)
        path = urlparse(url).path.lower()
        return min((i for i, h in enumerate(STAGE_PAGE_HINTS) if h in path), default=len(STAGE_PAGE_HINTS))

    likely = sorted((u for u in ctx.fetchable_pages() if rung(u) < len(STAGE_PAGE_HINTS)), key=rung)
    if not likely:
        return action
    return AgentAction(action="fetch_page", argument=likely[0],
                       why=f"Python fallback: the LLM chose to stop, but {', '.join(unknowns)} is still unknown and "
                           f"{likely[0]} has not been read yet (LLM's reason: {action.why})")


def validate_action(action: AgentAction, ctx: QualifyContext) -> AgentAction:
    """Enforce the fixed action rules. Anything not allowed becomes `decide`."""
    arg = action.argument.strip()
    if action.action == "fetch_page":
        url = arg if "://" in arg else f"https://{ctx.domain}/{arg.lstrip('/')}"
        if not same_site(url, ctx.domain):
            return AgentAction(action="decide", argument="", why=f"refused fetch_page outside {ctx.domain}: {arg}")
        return AgentAction(action="fetch_page", argument=url, why=action.why)
    if action.action == "web_search":
        if not llm.search_available():
            return AgentAction(action="decide", argument="", why="web search is unavailable on this API key")
        if not arg or arg in ctx.searches:
            return AgentAction(action="decide", argument="", why="empty or repeated search query")
        return AgentAction(action="web_search", argument=arg[:200], why=action.why)
    return AgentAction(action="decide", argument="", why=action.why)


def execute_action(action: AgentAction, ctx: QualifyContext) -> tuple[str, bool]:
    """Run the action and add whatever it found to the evidence corpus.

    Returns ('what we learned', added_new_sources). If nothing new was added, the caller skips the
    re-scoring LLM call (nothing could have changed).
    """
    if action.action == "fetch_page":
        page, sigs = fetch_extra_page(action.argument, ctx.domain, ctx.settings, ctx.fetcher)
        ctx.extra_pages.append(page.url)
        ctx.extra_page_records.append(page)
        if page.error:
            return f"could not fetch {action.argument}: {page.error}", False
        if not page.readable and not sigs and len(page.text.split()) < 20:
            return f"fetched {page.url} but it is not readable ({page_problem(page)})", False
        ctx.scrape.detected_signals.extend(sigs)
        ctx.corpus.add_page(page, sigs)
        return f"fetched {page.url} ({len(page.text)} characters of text, {len(sigs)} cheap signals)", True
    if action.action == "web_search":
        ctx.searches.append(action.argument)
        text, citations = llm.research_with_search(action.argument)
        if not text:
            return "web search unavailable or returned nothing", False
        if not citations:
            return "search answered without citations, so it cannot be used as evidence", False
        ctx.corpus.add_search(action.argument, text, citations)
        return f"search found {len(citations)} source(s): " + " ".join(text.split())[:300], True
    return "decided to stop researching", False


# ---------------------------------------------------------------- main entry point
def qualify(ctx: QualifyContext) -> QualificationResult:
    """Step 3 for one domain."""
    icp = ctx.icp
    dq = list_disqualifier(ctx.domain, ctx.scrape, icp) or profile_disqualifier(ctx.profile, icp)
    if dq:
        return _rejected(dq, signals=[], lines=[], iterations=[], note="decided by Python before any scoring call")

    signals, summary = extract_signals(ctx)
    iterations: list[AgentIteration] = []
    customer_check_done = False
    qualified_min, borderline_min = icp.settings["qualified_min_score"], icp.settings["borderline_min_score"]

    while True:
        if dq := signal_disqualifier(signals):
            break
        score, lines = score_signals(signals, icp)
        unknowns = critical_unknowns(signals, ctx.profile, icp)
        req_failed, req_open = required_rows_status(signals, icp)
        unknowns += [f"{name} (no verified evidence yet)" for name in req_open if name not in unknowns]
        borderline = borderline_min <= score < qualified_min
        needs_customer_check = score >= borderline_min and not customer_check_done and llm.search_available()
        hopeless = max_possible_score(signals, icp) < qualified_min or bool(req_failed)
        if hopeless or not (borderline or unknowns or needs_customer_check):
            break
        if len(iterations) >= ctx.settings.max_agent_iterations:
            log.info("[%s] agent loop: reached MAX_AGENT_ITERATIONS", ctx.domain)
            break
        per_iteration = CALLS_PER_ITERATION if llm.search_available() else CALLS_PER_ITERATION - 1  # no search call
        if llm.calls_left() < per_iteration + RESERVED_CALLS:
            log.info("[%s] agent loop: stopping, LLM budget low (%d left)", ctx.domain, llm.calls_left())
            break

        if needs_customer_check:
            customer_check_done = True
            action = AgentAction(action="web_search", argument=f"{ctx.company} Sprinto customer",
                                 why="secondary check: is this company already a Sprinto customer (disqualifier)?")
        else:
            action = choose_action(ctx, signals, score, unknowns, len(iterations) + 1)
        if action.action == "decide":
            iterations.append(AgentIteration(n=len(iterations) + 1, action=action, learned="agent chose to stop", score_before=score, score_after=score))
            break

        learned, added = execute_action(action, ctx)
        if not added:  # nothing new to read, so don't spend a call re-asking the same questions
            iterations.append(AgentIteration(n=len(iterations) + 1, action=action, learned=learned, score_before=score, score_after=score))
            log.info("[%s] agent loop %d: %s(%s) -> nothing new", ctx.domain, len(iterations), action.action, action.argument)
            continue
        before_ids = {(e.source_url, e.quote) for s in signals for e in s.evidence}
        new_signals, summary = extract_signals(ctx)
        signals = merge_signals(signals, new_signals)
        new_ev = [e for s in signals for e in s.evidence if (e.source_url, e.quote) not in before_ids]
        after, _ = score_signals(signals, icp)
        iterations.append(AgentIteration(n=len(iterations) + 1, action=action, learned=learned, new_evidence=new_ev, score_before=score, score_after=after))
        log.info("[%s] agent loop %d: %s(%s) -> score %d -> %d", ctx.domain, len(iterations), action.action, action.argument, score, after)

    if dq:
        score, lines = score_signals(signals, icp)
        return _rejected(dq, signals, lines, iterations, note=summary)

    score, lines = score_signals(signals, icp)
    unknowns = critical_unknowns(signals, ctx.profile, icp)
    req_failed, req_open = required_rows_status(signals, icp)
    possible = max_possible_score(signals, icp)
    evidence = [e for s in signals if s.value == "yes" for e in s.evidence]
    if possible < qualified_min:
        verdict, why = "rejected", f"score {score}; even if every unknown were 'yes' the maximum is {possible} (< {qualified_min})"
    elif req_failed:
        verdict, why = "rejected", (f"score {score}, but no buying trigger: every rule in {', '.join(req_failed)} was answered 'no' "
                                    "(ICP.md: Rows that must earn points)")
    elif req_open and score >= qualified_min:
        unknowns += [f"{name} (no verified evidence yet)" for name in req_open if name not in unknowns]
        verdict, why = "insufficient_data", (f"score {score}, but {', '.join(req_open)} has no verified evidence yet "
                                             "(ICP.md: Rows that must earn points)")
    elif unknowns:
        verdict, why = "insufficient_data", f"score {score}, but still unknown after research: {', '.join(unknowns)}"
    elif score >= qualified_min:
        verdict, why = "qualified", f"score {score} ≥ {qualified_min} with verified evidence for every point"
    else:
        verdict, why = "rejected", f"score {score} < {qualified_min} after {len(iterations)} research iteration(s)"
    return QualificationResult(
        verdict=verdict, score=score, max_possible_score=possible, critical_unknowns=unknowns,
        top_reasons=top_reasons(lines), score_breakdown=lines, signals=signals, disqualifier=None,
        reasoning=f"{verdict.upper()}: {why}. {summary}".strip(), evidence=evidence, iterations=iterations,
    )


def _rejected(dq, signals, lines, iterations, note: str) -> QualificationResult:
    rule_id, reason, evidence = dq
    score = score_signals(signals, load_icp())[0] if signals else 0
    return QualificationResult(
        verdict="rejected", score=score, disqualifier=rule_id, top_reasons=[f"{rule_id}: {reason}"],
        score_breakdown=lines, signals=signals, reasoning=f"REJECTED by hard disqualifier {rule_id}: {reason}. {note}".strip(),
        evidence=list(evidence), iterations=iterations,
    )
