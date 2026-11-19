# Owner checklist: the things only you can do

Everything else is built and tested offline (`pytest`: all green). These steps need your accounts or
your judgement. Do them in order; each has a "good output" so you know it worked.

## A. Keys on your laptop (10 min)
1. Allow the venv to activate (once): `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` → `Y`
2. Open `.env` in VS Code and fill in: `OWNER_EMAIL`, `GEMINI_API_KEY`, `HUNTER_API_KEY`
   (see `docs/SETUP_WINDOWS.md` steps 5–6).
3. Run:
   ```powershell
   cd "E:\AI by Mihir Term 2\Case Study 2\sprinto-outbound"
   .\.venv\Scripts\Activate.ps1
   python scripts\list_models.py
   ```
   Copy a `<- flash` model name into `GEMINI_MODEL=` in `.env`.
4. `python scripts\check_setup.py` → ✅ for owner, Gemini (incl. ping) and Hunter (shows searches left).

## B. Review the ICP scoring table (your decision #4)
Open `docs/ICP.md` → *Rubric rows* and *Scoring rules*. The point values are a first draft; change any number,
then run `pytest` (one test checks the rows still add up to 100). The code reads the file directly, so no code change is needed.

## C. First real runs (no Hunter, no email yet)
```powershell
python -m outbound.cli --only rocketlane.com --steps 1-2     # writes runs\<id>\rocketlane.com\01_scrape.json, 02_extract.json
python -m outbound.cli --steps 1-3                           # qualify all 10 seed domains (uses ~2-11 Gemini calls each)
```
Good output: a table with a verdict per domain. Expected from the scrape alone:
- `rocketlane.com` → **rejected, DQ_SPRINTO_CUSTOMER**: its own "Trust center" link points to app.sprinto.com
  (your seed list guessed it was a fit; the evidence says it's already a customer. Good defense material.)
- `getastra.com` → rejected (Known Sprinto customers list) · `vanta.com` → rejected (competitor)
- `thesouledstore.com` → rejected (not B2B) · `salesforce.com`, `stripe.com` → rejected (late stage) or low score
Open `runs\<id>\decision_report.md` and check you agree with each reason.

## D. Find contacts + draft emails (spends Hunter credits only for qualified companies)
```powershell
python -m outbound.cli --steps 4-6        # continues the latest run; 1 Hunter search per qualified company
python -m outbound.cli --steps 4-6        # run again: "using cached Hunter result (0 credits)"
```
Read `runs\<id>\<domain>\06_email.txt`. Would a human reply? If not, edit `prompts\email.md` and re-run `--steps 6`.

## E. Gmail (send one real email to yourself)
1. `docs/SETUP_WINDOWS.md` step 7 → `credentials.json` in the project folder.
2. `python scripts\gmail_auth.py` → browser → allow "send email" → `token.json` appears.
3. In `.env`: `SEND_ENABLED=true`
4. `python -m outbound.cli --only <a qualified domain> --steps 7 --send`
   Good output: an email in your inbox with subject `[TEST → person@company.com] ...`.

## F. Website (Vercel + Neon)
Follow `docs/SETUP_WINDOWS.md` step 9. Then open your Vercel link on another laptop/phone, paste 2–3 domains,
click **Run pipeline (dry-run)**. Send me the link and I'll add it to the README.

## G. Before the defense
Read `docs/DEFENSE_NOTES.md`. After your real run, replace the `‹fill after run›` placeholders with your numbers
(or ask Claude Code to do it from `runs\<id>\summary.csv`).

## H. Two decisions from the L4 lecture (your call; nothing breaks if you leave them)
Read `docs/EVIDENCE_MAP.md` first (5 min). Then decide:
1. **Unknown stage: `insufficient_data` (today) or "qualify-and-flag" (lecture's suggestion)?** Today a company with
   a clear compliance gap but no visible funding/headcount is *not* qualified, so it costs no Hunter credit and lands
   in "Needs a human". Qualify-and-flag would draft an email for it with a ⚠ marker. Tell Claude Code which you want.
2. **Check the "Needs a human" list after your first real run** (`runs\<id>\decision_report.md`, near the top). For
   each domain named there, open the page it mentions in a browser and see whether a person would agree with the
   verdict. That's your accuracy number for the kill criterion ("below 60% → shut it down").
