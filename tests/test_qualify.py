"""ICP parsing, evidence checks, deterministic scoring and the agent loop (LLM replaced by fakes)."""

import pytest

from outbound import llm, qualify as q
from outbound.config import Settings
from outbound.evidence import EvidenceCorpus, quote_in_text
from outbound.icp import ICPFormatError, load_icp, parse_icp
from outbound.models import AgentAction, Citation, CompanyProfile, DetectedSignal, Evidence, FieldEvidence, PageContent, ScrapeResult, Signal, SignalReport

ICP = load_icp()
HOME = "https://acme.io/"
HOME_TEXT = ("Acme Cloud automates approval workflows for enterprise finance teams. Book a demo. Enterprise plan with SAML SSO. "
             "We raised our Series B in March 2026.")


def ev(quote="Enterprise plan with SAML SSO", url=HOME) -> Evidence:
    return Evidence(claim="c", source_url=url, quote=quote)


def yes(rule_id, quote="Enterprise plan with SAML SSO") -> Signal:
    return Signal(rule_id=rule_id, value="yes", evidence=[ev(quote)])


# ---------------------------------------------------------------- ICP.md
def test_icp_rows_add_up_to_100():
    assert sum(r.max_points for r in ICP.rows.values() if r.id != "COMP") == 100


def test_icp_lists_and_settings_are_read_from_the_file():
    assert "getastra.com" in ICP.customers
    assert "vanta.com" in ICP.competitors
    assert ICP.sprinto_domains == ["sprinto.com"]
    assert ICP.settings["qualified_min_score"] == 60 and ICP.settings["borderline_min_score"] == 40


def test_icp_bad_format_gives_clear_error():
    with pytest.raises(ICPFormatError):
        parse_icp("# nothing useful here")


# ---------------------------------------------------------------- evidence
def test_quote_matching_is_forgiving_about_spacing_and_quotes_but_not_content():
    assert quote_in_text("Book  a demo.", HOME_TEXT)
    assert quote_in_text("enterprise plan with saml sso", HOME_TEXT)
    assert not quote_in_text("Series C funding of $50M", HOME_TEXT)


def test_corpus_rejects_unknown_urls_and_invented_quotes():
    corpus = EvidenceCorpus()
    corpus.add_page(PageContent(url=HOME, text=HOME_TEXT))
    assert corpus.verify(ev())
    assert not corpus.verify(ev(url="https://made-up.com/"))
    assert not corpus.verify(ev(quote="raised a Series B led by Sequoia"))
    assert corpus.verify(ev(quote="no trust page found"), absence=True)


def test_search_citations_become_citable_sources():
    corpus = EvidenceCorpus()
    corpus.add_search("acme funding", "Acme raised a Series B round in March 2025.", [Citation(url="https://news.example/acme", title="news")])
    assert corpus.verify(Evidence(claim="c", source_url="https://news.example/acme", quote="raised a Series B round in March 2025"))


# ---------------------------------------------------------------- scoring
def test_same_signals_always_give_same_score():
    signals = [yes("ENT_PLAN"), yes("ENT_DEMO"), yes("STAGE_SERIES"), yes("GEO_US")]
    assert q.score_signals(signals, ICP) == q.score_signals(list(reversed(signals)), ICP)
    assert q.score_signals(signals, ICP)[0] == 8 + 4 + 10 + 5


def test_unknown_no_and_unevidenced_yes_score_zero():
    signals = [Signal(rule_id="ENT_PLAN", value="unknown"), Signal(rule_id="ENT_DEMO", value="no"), Signal(rule_id="ENT_SSO", value="yes")]
    score, lines = q.score_signals(signals, ICP)
    assert score == 0
    assert any(ln.rule_id == "ENT_SSO" and "no verified evidence" in ln.reason for ln in lines)


def test_rows_are_capped_and_penalty_applies():
    gap = ["GAP_IN_PROGRESS", "GAP_NO_TRUST_PAGE", "GAP_ONE_FRAMEWORK", "GAP_GDPR_ONLY", "GAP_QUESTIONNAIRE"]  # 41 raw
    score, lines = q.score_signals([yes(r) for r in gap], ICP)
    assert score == 25 and any(ln.rule_id == "GAP_CAP" and ln.points == -16 for ln in lines)
    score, _ = q.score_signals([yes(r) for r in gap] + [yes("COMP_ON_COMPETITOR")], ICP)
    assert score == 5


def test_total_never_below_zero():
    assert q.score_signals([yes("COMP_ON_COMPETITOR")], ICP)[0] == 0


def test_max_possible_score_counts_unknowns_as_yes():
    signals = [Signal(rule_id=r.id, value="no") for r in ICP.rules]
    assert q.max_possible_score(signals, ICP) == 0
    assert q.max_possible_score([], ICP) == 100


def test_critical_unknowns():
    profile = CompanyProfile()
    missing = q.critical_unknowns([], profile, ICP)
    assert "B2B status" in missing and "Stage fit: Series A–C" in missing
    profile = CompanyProfile(is_b2b_software=True)
    assert q.critical_unknowns([yes("STAGE_SIZE"), Signal(rule_id="GAP_IN_PROGRESS", value="no")], profile, ICP) == []


@pytest.mark.parametrize("text, expected", [("51-200", 51), ("1,000+ employees", 1000), ("10k+", 10000), (None, None), ("small team", None)])
def test_employee_lower_bound(text, expected):
    assert q.employee_lower_bound(text) == expected


# ---------------------------------------------------------------- disqualifiers (Python, before any LLM call)
def scrape(domain="acme.io", signals=()) -> ScrapeResult:
    return ScrapeResult(domain=domain, base_url=f"https://{domain}", pages=[PageContent(url=HOME, text=HOME_TEXT)], detected_signals=list(signals))


def test_list_disqualifiers():
    assert q.list_disqualifier("vanta.com", scrape("vanta.com"), ICP)[0] == "DQ_COMPLIANCE_VENDOR"
    assert q.list_disqualifier("getastra.com", scrape("getastra.com"), ICP)[0] == "DQ_SPRINTO_CUSTOMER"
    link = DetectedSignal(signal="sprinto_link", source_url=HOME, snippet="link to app.sprinto.com Trust center")
    assert q.list_disqualifier("acme.io", scrape(signals=[link]), ICP)[0] == "DQ_SPRINTO_CUSTOMER"
    assert q.list_disqualifier("acme.io", scrape(), ICP) is None


def test_profile_disqualifiers():
    d2c = CompanyProfile(is_b2b_software=False, evidence=[FieldEvidence(field="is_b2b_software", claim="D2C apparel", source_url=HOME, quote="x")])
    assert q.profile_disqualifier(d2c, ICP)[0] == "DQ_NOT_B2B"
    assert q.profile_disqualifier(CompanyProfile(employee_range="10,000+"), ICP)[0] == "DQ_LATE_STAGE"
    assert q.profile_disqualifier(CompanyProfile(employee_range="51-200", is_b2b_software=True), ICP) is None


# ---------------------------------------------------------------- agent loop with a fake LLM
@pytest.fixture
def ctx():
    s = Settings(_env_file=None, gemini_api_key="x", gemini_model="fake", max_agent_iterations=3, max_llm_calls_per_domain=12)
    sc = scrape()
    corpus = EvidenceCorpus()
    for p in sc.pages:
        corpus.add_page(p)
    llm.start_domain("acme.io")
    return q.QualifyContext(domain="acme.io", scrape=sc, profile=CompanyProfile(name="Acme", is_b2b_software=True), corpus=corpus, settings=s)


def install_fake_llm(monkeypatch, signal_batches, actions=(), search=("", [])):
    """Each qualify call returns the next batch of signals; next_action returns the next action."""
    batches, acts = list(signal_batches), list(actions)
    calls = {"qualify": 0, "next_action": 0, "search": []}

    def fake_generate_json(prompt_name, variables, schema, temperature=0.0):
        llm._budget.spend(12)
        calls[prompt_name] += 1
        if prompt_name == "qualify":
            return SignalReport(signals=batches.pop(0) if len(batches) > 1 else batches[0], summary="fake")
        return acts.pop(0)

    def fake_search(question):
        llm._budget.spend(12)
        calls["search"].append(question)
        return search

    monkeypatch.setattr(llm, "generate_json", fake_generate_json)
    monkeypatch.setattr(llm, "research_with_search", fake_search)
    return calls


def strong_signals():
    ids = ["ENT_PLAN", "ENT_DEMO", "ENT_LOGOS", "ENT_SSO", "DATA_INTEGRATIONS", "DATA_SENSITIVE", "GAP_IN_PROGRESS", "STAGE_SIZE", "GEO_US"]
    return ([yes(i) for i in ids] + [yes("STAGE_SERIES", "We raised our Series B in March 2026")]
            + [Signal(rule_id="DQ_SPRINTO_CUSTOMER", value="no")])


def test_qualified_company_still_gets_sprinto_customer_check(monkeypatch, ctx):
    calls = install_fake_llm(monkeypatch, [strong_signals()])
    result = q.qualify(ctx)
    assert result.verdict == "qualified" and result.score >= 60
    assert calls["search"] == ["Acme Sprinto customer"]
    assert result.iterations[0].action.action == "web_search"


def no_gap_signals(gap_value="no"):
    """Strong generic B2B signals (>= 60 points) but the compliance-gap row has no 'yes'."""
    ids = ["ENT_PLAN", "ENT_DEMO", "ENT_LOGOS", "ENT_SSO", "DATA_INTEGRATIONS", "DATA_SENSITIVE", "DATA_DPA",
           "STAGE_SIZE", "GEO_US", "GEO_EU_UK", "HIRING_ENT_SALES"]
    gap_ids = [r.id for r in ICP.rules if r.row == "GAP"]
    return ([yes(i) for i in ids] + [yes("STAGE_SERIES", "We raised our Series B in March 2026")]
            + [Signal(rule_id=g, value=gap_value) for g in gap_ids] + [Signal(rule_id="DQ_SPRINTO_CUSTOMER", value="no")])


def test_icp_reads_rows_that_must_earn_points():
    assert ICP.must_earn_rows == ("GAP",)


def test_high_score_without_any_compliance_gap_is_rejected(monkeypatch, ctx):
    install_fake_llm(monkeypatch, [no_gap_signals("no")])
    result = q.qualify(ctx)
    assert q.score_signals(no_gap_signals("no"), ICP)[0] >= 60  # would have qualified before the rule
    assert result.verdict == "rejected"
    assert "no buying trigger" in result.reasoning


def test_high_score_with_gap_still_unknown_is_insufficient_data(monkeypatch, ctx):
    install_fake_llm(monkeypatch, [no_gap_signals("unknown")], actions=[AgentAction(action="decide", argument="", why="done")] * 3)
    monkeypatch.setattr(q, "fetch_extra_page", lambda url, domain, settings, fetcher=None: (PageContent(url=url, error="HTTP 404", status_code=404, quality="error"), []))
    result = q.qualify(ctx)
    assert result.verdict == "insufficient_data"
    assert any("Compliance gap" in u for u in result.critical_unknowns)


def test_list_disqualifier_needs_no_llm(monkeypatch, ctx):
    calls = install_fake_llm(monkeypatch, [[]])
    ctx.domain = "vanta.com"
    result = q.qualify(ctx)
    assert result.verdict == "rejected" and result.disqualifier == "DQ_COMPLIANCE_VENDOR"
    assert calls["qualify"] == 0


def test_borderline_runs_loop_and_terminates(monkeypatch, ctx):
    borderline = [yes(i) for i in ["ENT_PLAN", "ENT_DEMO", "ENT_LOGOS", "ENT_SSO", "STAGE_SIZE", "GEO_US", "DATA_INTEGRATIONS", "GAP_QUESTIONNAIRE"]]  # 45
    actions = [AgentAction(action="web_search", argument="Acme Series B funding 2025", why="stage unknown"),
               AgentAction(action="decide", argument="", why="enough")]
    calls = install_fake_llm(monkeypatch, [borderline], actions)
    result = q.qualify(ctx)
    assert result.verdict in ("rejected", "insufficient_data")
    assert 1 <= len(result.iterations) <= ctx.settings.max_agent_iterations
    assert calls["search"][0] == "Acme Sprinto customer"
    assert llm.calls_used() <= ctx.settings.max_llm_calls_per_domain


def test_search_quota_error_turns_search_off_for_the_run(monkeypatch, ctx):
    from google.genai import errors

    calls = []

    def no_quota(contents, config):
        calls.append(1)
        raise errors.ClientError(429, {"error": {"message": "You exceeded your current quota"}})

    monkeypatch.setattr(llm, "_call", no_quota)
    monkeypatch.setattr(llm, "_search_off_reason", "")
    monkeypatch.setattr(llm, "get_settings", lambda: ctx.settings)
    assert llm.research_with_search("acme funding") == ("", [])
    assert len(calls) == 1  # no retries on a quota error
    assert not llm.search_available()
    assert llm.research_with_search("acme hiring") == ("", []) and len(calls) == 1  # not even tried again
    blocked = q.validate_action(AgentAction(action="web_search", argument="acme SOC 2", why="?"), ctx)
    assert blocked.action == "decide"


@pytest.mark.parametrize("quote, value", [
    ("In August 2025, Attio announced a $52 million Series B funding round.", "yes"),   # 15 months
    ("Testsigma raised an $8.2 million Series A funding round announced on June 5/6, 2024.", "no"),  # 29 months
    ("GoComet closed a $7 million Series A funding round announced on February 9, 2022.", "no"),
    ("Raised a Series B from top investors.", "unknown"),  # no date: window can't be checked
    ("Series A in 2025", "yes"),  # bare year read as December 2025
])
def test_funding_window_is_checked_in_python(quote, value):
    from datetime import date

    sig = Signal(rule_id="STAGE_RECENT_FUNDING", value="yes", detail="", evidence=[ev(quote)])
    assert q.apply_funding_window(sig, 24, today=date(2026, 11, 19)).value == value


def test_funding_window_leaves_no_and_unknown_alone():
    sig = Signal(rule_id="STAGE_RECENT_FUNDING", value="no", evidence=[ev("Series A in 2025")])
    assert q.apply_funding_window(sig, 24).value == "no"


def test_giving_up_with_critical_unknowns_fetches_careers_first(ctx):
    ctx.scrape.candidate_links = ["https://acme.io/blog/post", "https://acme.io/company/about-us", "https://acme.io/jobs"]
    stop = AgentAction(action="decide", argument="", why="nothing else to do")
    action = q.fallback_if_giving_up(stop, ctx, ["Stage fit: Series A–C"])
    assert action.action == "fetch_page" and action.argument == "https://acme.io/careers"  # L4: careers is rung one
    assert "Python fallback" in action.why
    assert q.fallback_if_giving_up(stop, ctx, []).action == "decide"  # nothing critical missing: allowed to stop


def test_fetchable_pages_skips_fetched_and_adds_fallbacks(ctx):
    ctx.scrape.candidate_links = ["https://acme.io/", "https://acme.io/press"]
    pages = ctx.fetchable_pages()
    assert "https://acme.io/" not in pages  # already fetched
    assert pages[0] == "https://acme.io/press" and "https://acme.io/careers" in pages


def test_research_rounds_never_lose_verified_answers():
    prev = [yes("ENT_DEMO"), yes("GEO_US"), Signal(rule_id="STAGE_SIZE", value="unknown")]
    new = [Signal(rule_id="ENT_DEMO", value="unknown"),                       # forgot -> keep old yes
           Signal(rule_id="GEO_US", value="no", evidence=[ev("Book a demo")]),  # evidenced no -> overrides
           yes("STAGE_SIZE")]                                                  # newly found -> added
    merged = {s.rule_id: s.value for s in q.merge_signals(prev, new)}
    assert merged == {"ENT_DEMO": "yes", "GEO_US": "no", "STAGE_SIZE": "yes"}


def test_invalid_actions_become_decide(ctx):
    bad = q.validate_action(AgentAction(action="fetch_page", argument="https://evil.com/x", why="?"), ctx)
    assert bad.action == "decide"
    ok = q.validate_action(AgentAction(action="fetch_page", argument="/careers", why="hiring"), ctx)
    assert ok.action == "fetch_page" and ok.argument == "https://acme.io/careers"


def test_scraped_competitor_link_forces_penalty(ctx):
    ctx.scrape.detected_signals.append(DetectedSignal(signal="competitor_link:vanta.com", source_url=HOME, snippet="link to trust.vanta.com Trust Center"))
    signals = q.clean_signals([], ctx)
    comp = next(s for s in signals if s.rule_id == "COMP_ON_COMPETITOR")
    assert comp.value == "yes" and comp.evidence
    assert all(s.value == "unknown" for s in signals if s.rule_id != "COMP_ON_COMPETITOR")


def test_hallucinated_evidence_is_dropped(ctx):
    fake = Signal(rule_id="STAGE_SERIES", value="yes", evidence=[ev(quote="raised $40M Series B from Accel")])
    cleaned = {s.rule_id: s for s in q.clean_signals([fake], ctx)}
    assert cleaned["STAGE_SERIES"].evidence == []
    assert q.score_signals(list(cleaned.values()), ICP)[0] == 0
