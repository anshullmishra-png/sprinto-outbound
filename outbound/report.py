"""Builds summary.csv and decision_report.md for a run from its saved artifacts.

The report answers, per domain: why qualified / rejected (score table + evidence links),
why this person (vs the alternatives), and why this email (hook, pain point, sources).
"""

from __future__ import annotations

import csv
import io

from outbound.pipeline import STEP_FILES
from outbound.storage import Store

SUMMARY_COLUMNS = ["domain", "outcome", "last_step", "status", "verdict", "score", "disqualifier", "top_reasons", "poc_name", "poc_title",
                   "email_subject", "email_flagged", "send_status", "llm_calls", "hunter_credits", "pages_readable", "pages_unreadable"]


def _page_counts(arts: dict) -> tuple[str, str]:
    pages = ((arts.get("01_scrape") or {}).get("output") or {}).get("pages") or []
    if not pages:
        return "", ""
    readable = [p for p in pages if not p.get("error") and p.get("quality", "ok") == "ok"]
    return str(len(readable)), str(len(pages) - len(readable))


def review_queue(run: dict[str, dict[str, dict]]) -> list[str]:
    """Domains a person should look at: the designed hand-off when the system can't decide on its own."""
    items = []
    for domain, arts in run.items():
        q = (arts.get("03_qualify") or {}).get("output") or {}
        draft = (arts.get("06_email") or {}).get("output") or {}
        scrape = (arts.get("01_scrape") or {})
        if scrape and scrape.get("status") != "done":
            pages = (scrape.get("output") or {}).get("pages") or []
            reason = next((p.get("error") for p in pages if p.get("error")), None)
            why = f" ({reason})" if reason else ""
            items.append(f"- **{domain}**: no page could be read{why}. Stopped, no retries. Check it by hand or skip it.")
        if q.get("verdict") == "insufficient_data":
            items.append(f"- **{domain}**: insufficient data after research ({', '.join(q.get('critical_unknowns') or []) or 'see reasoning'}). "
                         f"A 2-minute manual look decides it.")
        unconfirmed = [s for s in q.get("signals") or [] if (s.get("detail") or "").startswith("can't confirm:")]
        if unconfirmed:
            items.append(f"- **{domain}**: {unconfirmed[0]['detail']}. Gap rules {', '.join(s['rule_id'] for s in unconfirmed)} "
                         f"scored 0; open that page in a browser to check certifications.")
        if draft.get("flagged"):
            items.append(f"- **{domain}**: email draft flagged ({'; '.join(draft.get('problems') or []) or 'see 06_email'}). Not sendable as is.")
    return items


def _last(arts: dict) -> tuple[str, dict]:
    for name in reversed(list(STEP_FILES.values())):
        if name in arts:
            return name, arts[name]
    return "", {}


def summary_rows(run: dict[str, dict[str, dict]]) -> list[dict]:
    rows = []
    for domain, arts in run.items():
        last_name, last = _last(arts)
        q = (arts.get("03_qualify") or {}).get("output") or {}
        poc = ((arts.get("04_poc") or {}).get("output") or {})
        chosen = poc.get("chosen") or {}
        draft = (arts.get("06_email") or {}).get("output") or {}
        send = (arts.get("07_send") or {}).get("output") or {}
        rows.append({
            "domain": domain,
            "outcome": domain_status(arts),
            "last_step": last_name,
            "status": last.get("status", ""),
            "verdict": q.get("verdict", ""),
            "score": q.get("score", ""),
            "disqualifier": q.get("disqualifier") or "",
            "top_reasons": " | ".join(q.get("top_reasons", [])),
            "poc_name": " ".join(p for p in (chosen.get("first_name"), chosen.get("last_name")) if p),
            "poc_title": chosen.get("position") or "",
            "email_subject": (draft.get("draft") or {}).get("subject", ""),
            "email_flagged": draft.get("flagged", ""),
            "send_status": send.get("status", ""),
            "llm_calls": last.get("llm_calls_used", ""),
            "hunter_credits": poc.get("hunter_credits_used", ""),
            "pages_readable": _page_counts(arts)[0],
            "pages_unreadable": _page_counts(arts)[1],
        })
    return rows


def summary_csv(run: dict[str, dict[str, dict]]) -> str:
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=SUMMARY_COLUMNS)
    writer.writeheader()
    writer.writerows(summary_rows(run))
    return buf.getvalue()


def _link(url: str) -> str:
    return f"[{url}]({url})" if url.startswith("http") else f"`{url}`"


def _evidence_lines(evidence: list[dict], limit: int = 6) -> list[str]:
    return [f"  - {e['claim']}: \"{e['quote']}\" ({_link(e['source_url'])})" for e in evidence[:limit]]


def domain_status(arts: dict) -> str:
    """One of: qualified | rejected | review (needs a human) | failed (research failed) | pending.

    insufficient_data is NOT merged into rejected: it means "we couldn't tell", which is a job for a person.
    """
    if any((arts.get(s) or {}).get("status") == "error" for s in ("01_scrape", "02_extract", "03_qualify")):
        return "failed"
    if (arts.get("01_scrape") or {}).get("status") == "stopped":
        return "failed"
    q = (arts.get("03_qualify") or {}).get("output") or {}
    verdict = q.get("verdict")
    if verdict == "insufficient_data":
        return "review"
    if verdict == "rejected":
        return "rejected"
    if verdict == "qualified":
        mail = (arts.get("06_email") or {}).get("output") or {}
        poc = (arts.get("04_poc") or {}).get("output") or {}
        if mail.get("flagged") or poc.get("status") == "no_poc":
            return "review"
        return "qualified"
    return "pending"


def _tier_from_reasoning(text: str) -> tuple[str, int | None]:
    import re

    m = re.search(r"picks '([^']+)' \(priority (\d+)\)", text or "")
    return (m.group(1), int(m.group(2))) if m else ("", None)


def decision_brief(arts: dict) -> dict:
    """The five questions a Growth Lead asks, answered from saved artifacts (used by the report and the website):
    who to contact, why now, what evidence, what to say, what's still uncertain."""
    q = (arts.get("03_qualify") or {}).get("output") or {}
    poc_art = arts.get("04_poc") or {}
    poc = poc_art.get("output") or {}
    pains = (arts.get("05_pain") or {}).get("output") or []
    mail = (arts.get("06_email") or {}).get("output") or {}
    chosen = poc.get("chosen") or {}

    if chosen:
        who = f"{chosen.get('first_name', '')} {chosen.get('last_name') or ''}".strip() + f", {chosen.get('position') or 'no title'}"
    elif poc_art:
        who = f"No contact: {poc.get('reasoning', '')[:160]}"
    elif q.get("verdict") == "qualified":
        who = "Not looked up yet (step 4)"
    else:
        who = "Nobody: not a qualified company"

    why_now = list(q.get("top_reasons") or [])
    if (mail.get("draft") or {}).get("hook"):
        why_now.append(f"Hook: {mail['draft']['hook']}")

    uncertain: list[str] = [f"Still unknown: {u}" for u in q.get("critical_unknowns") or []]
    for it in q.get("iterations") or []:
        if "sprinto" in (it.get("action") or {}).get("argument", "").lower() and "cannot be used" in it.get("learned", ""):
            uncertain.append("Sprinto-customer search gave no citable answer (not confirmed either way)")
    for s in q.get("signals") or []:
        if (s.get("detail") or "").startswith("can't confirm:"):
            uncertain.append(s["detail"])
            break
    if "fallback" in (poc.get("reasoning") or "").lower() or "Any executive" in (poc.get("reasoning") or ""):
        uncertain.append("Contact chosen from a fallback tier: confirm they own security/compliance purchases")
    for p in pains:
        if p.get("kind", "inferred") == "inferred":
            uncertain.append(f"Pain point is inferred, not stated by them: {p.get('title')}")
    if mail.get("flagged"):
        uncertain.append("Email draft failed checks: " + "; ".join(mail.get("problems") or []))

    profile = (arts.get("02_extract") or {}).get("output") or {}
    tier, priority = _tier_from_reasoning(poc.get("reasoning", ""))
    sources = {e.get("source_url") for e in (q.get("evidence") or [])} | {e.get("source_url") for p in pains for e in p.get("evidence", [])}
    contact = None
    if chosen:
        contact = {
            "name": f"{chosen.get('first_name', '')} {chosen.get('last_name') or ''}".strip(),
            "title": chosen.get("position") or "",
            "department": chosen.get("department") or "",
            "email": chosen.get("email") or "",
            "confidence": chosen.get("confidence"),
            "tier": tier,
            "priority": priority,
            # relevance (does this role own the purchase?) is separate from Hunter's email confidence
            "relevance": ("primary buyer" if priority is not None and priority <= 3 else
                          "plausible buyer" if priority is not None and priority <= 5 else "fallback: verify"),
        }
    return {
        "status": domain_status(arts),
        "company": profile.get("name") or "",
        "description": profile.get("description") or "",
        "employees": profile.get("employee_range") or "",
        "verdict": q.get("verdict") or "",
        "score": q.get("score"),
        "disqualifier": q.get("disqualifier") or "",
        "sources": len({s for s in sources if s}),
        "contact": contact,
        "who": who,
        "why_now": why_now[:4],
        "evidence": [{"claim": e["claim"], "source_url": e["source_url"]} for e in (q.get("evidence") or [])[:3]],
        "what_to_say": (mail.get("draft") or {}).get("subject", ""),
        "uncertain": uncertain[:6],
    }


def _brief_md(b: dict) -> str:
    lines = ["**Decision brief**", f"- **Who to contact:** {b['who']}",
             f"- **Why now:** {'; '.join(b['why_now']) or '-'}"]
    if b["evidence"]:
        lines.append("- **Evidence:** " + "; ".join(f"{e['claim']} ({_link(e['source_url'])})" for e in b["evidence"]))
    if b["what_to_say"]:
        lines.append(f"- **What to say:** subject \"{b['what_to_say']}\" (full draft below)")
    lines.append(f"- **Still uncertain:** {'; '.join(b['uncertain']) or 'nothing flagged'}")
    return "\n".join(lines)


def domain_section(domain: str, arts: dict) -> str:
    out = [f"## {domain}"]
    if (arts.get("03_qualify") or {}).get("output"):
        out.append(_brief_md(decision_brief(arts)))
    scrape = (arts.get("01_scrape") or {})
    out.append(f"**Scrape:** {scrape.get('reasoning', 'not run')}")
    q_art = arts.get("03_qualify")
    if not q_art:
        out.append(f"**Stopped before qualification:** {_last(arts)[1].get('reasoning', '')}")
        return "\n\n".join(out)
    q = q_art.get("output") or {}
    if not q:
        out.append(f"**Qualification error:** {q_art.get('reasoning')}")
        return "\n\n".join(out)

    out.append(f"### Why {q['verdict']} (score {q['score']}/100)\n{q.get('reasoning', '')}")
    if q.get("score_breakdown"):
        table = ["| Rule | Row | Points | Max | Reason |", "|---|---|---|---|---|"]
        for ln in q["score_breakdown"]:
            if ln["points"] or ln["reason"] not in ("unknown", "no"):
                table.append(f"| {ln['rule_id']} | {ln['rubric_row']} | {ln['points']} | {ln['max_points']} | {ln['reason'].replace('|', '/')} |")
        out.append("\n".join(table))
    if q.get("evidence"):
        out.append("**Evidence:**\n" + "\n".join(_evidence_lines(q["evidence"], 10)))
    if q.get("iterations"):
        its = [f"{i['n']}. `{i['action']['action']}` {i['action']['argument']}: {i['action']['why']} → {i['learned'][:200]} (score {i['score_before']} → {i['score_after']})"
               for i in q["iterations"]]
        out.append("**Agent loop:**\n" + "\n".join(its))

    poc_art = arts.get("04_poc")
    if poc_art and poc_art.get("output"):
        out.append(f"### Why this person\n{poc_art['output'].get('reasoning', '')}")
    pain_art = arts.get("05_pain")
    if pain_art and pain_art.get("output"):
        lines = []
        for p in pain_art["output"]:
            lines.append(f"- **{p['title']}** ({p.get('kind', 'inferred')}): {p['why_this_person']}")
            lines += _evidence_lines(p.get("evidence", []), 2)
        out.append("### Pain points\n" + "\n".join(lines))
    email_art = arts.get("06_email")
    if email_art and email_art.get("output"):
        d = email_art["output"]
        out.append(f"### Why this email\n{email_art.get('reasoning', '')}\n\n**Subject:** {d['draft']['subject']}\n\n```\n{d['full_body']}\n```")
    send_art = arts.get("07_send")
    if send_art and send_art.get("output"):
        s = send_art["output"]
        out.append(f"### Send\n`{s['status']}`: {s['reason']}")
    return "\n\n".join(out)


def decision_report(run_id: str, run: dict[str, dict[str, dict]]) -> str:
    rows = summary_rows(run)
    head = [f"# Decision report: run {run_id}", "", "| Domain | Verdict | Score | Top reasons | PoC | Send |", "|---|---|---|---|---|---|"]
    for r in rows:
        head.append(f"| {r['domain']} | {r['verdict'] or r['status']} | {r['score']} | {r['top_reasons'].replace('|', '/') or r['disqualifier']} | {r['poc_name']} {('(' + r['poc_title'] + ')') if r['poc_title'] else ''} | {r['send_status']} |")
    queue = review_queue(run)
    head += ["", "## Needs a human", *(queue or ["- Nothing: every domain got a clear decision."])]
    sections = [domain_section(d, arts) for d, arts in run.items()]
    return "\n".join(head) + "\n\n" + "\n\n---\n\n".join(sections) + "\n"


def write_reports(run_id: str, store: Store) -> tuple[str, str]:
    """Build both reports from the store and save them as run files. Returns (csv, markdown)."""
    run = store.load_run(run_id)
    csv_text, md = summary_csv(run), decision_report(run_id, run)
    store.save_run_file(run_id, "summary.csv", csv_text)
    store.save_run_file(run_id, "decision_report.md", md)
    return csv_text, md
