# PROJECT: Invoice Processing Decision Engine (Zamp ASA Case Study — PS-1)

## What we're building
An automated process that takes vendor invoice PDFs as input, extracts structured
data from them, matches them against a Purchase Order (PO) dataset, checks for
anomalies/fraud signals, and produces a clear decision: APPROVE, FLAG, or ESCALATE
— with full reasoning visible at every stage.

This mirrors a real Accounts Payable (AP) workflow. The core design principle:
**the AI extracts data and explains decisions in plain English, but the actual
approve/flag/escalate decision is deterministic rule-based code — not an LLM
judgment call.** This keeps the decision auditable and consistent.

## Tech stack
- Backend: Python, FastAPI
- Frontend: simple, clean web UI (plain HTML/CSS/JS is fine, or a minimal
  React/Next.js if easier — doesn't need to be fancy, needs to be clear)
- PDF extraction: Anthropic Claude API (model: claude-sonnet-4-6) using its
  vision capability to read invoice PDFs/images directly and return structured
  JSON. Do NOT build custom OCR — send the PDF/image to Claude and have it
  return structured fields.
- Storage: simple JSON file or SQLite for run history — no need for a real database
- Data files provided: po_dataset.json, vendor_history.json (attached)
- Test invoices provided: 5 PDFs in /invoices folder (attached)

## The exact decision logic (IMPLEMENT EXACTLY AS WRITTEN — this is the core IP of the project)

```
STEP 1 — Anomaly check (runs FIRST, overrides everything else below)
IF vendor's bank_account on this invoice != vendor's last_known_bank_account
   in vendor_history.json
   OR invoice_number exactly matches one already in vendor's processed_invoices list
   OR line items don't mathematically sum to the stated total (internal inconsistency)
→ DECISION = ESCALATE
   reasoning: explain which specific anomaly triggered this, regardless of
   how clean the rest of the match is.

STEP 2 — Extraction confidence check (only if Step 1 did not escalate)
IF extraction confidence = LOW
   (i.e. critical fields like invoice_number OR total were not reliably
   extracted, OR the source document was visibly low quality)
→ DECISION = FLAG
   reasoning: explain what couldn't be reliably extracted and why that
   blocks auto-approval (e.g. "cannot confirm this isn't a duplicate
   without a reliable invoice number").

STEP 3 — PO match check (only if Steps 1 & 2 passed)
IF no PO found matching the po_reference on the invoice
→ DECISION = FLAG ("no matching PO found, needs human to locate or create one")

IF PO found AND vendor on invoice == vendor on PO AND
   invoice total is within tolerance of PO approved_amount
   (tolerance = the SMALLER of: 2% of approved_amount, OR Rs. 500 flat)
→ DECISION = APPROVE

IF PO found AND vendor matches AND invoice total EXCEEDS tolerance
→ DECISION = FLAG
   reasoning: give a best-guess explanation for the variance (e.g. "likely
   additional freight/tax line item not on original PO") but do not auto-approve.

IF invoice total is meaningfully LESS than the PO approved_amount
   (e.g. invoice is a fraction of the PO total, suggesting a partial/
   installment bill)
→ DECISION = FLAG
   reasoning: note this looks like a partial/split billing against a larger
   PO, state what fraction of the PO this represents, and recommend
   confirming the installment arrangement with the vendor since this
   isn't auto-approved on the first instance.

IF vendor on invoice != vendor on PO (vendor name mismatch despite a PO
   reference matching)
→ DECISION = ESCALATE
   reasoning: vendor mismatch is a risk signal, not just a data entry error.
```

## Output requirements (the UI/dashboard)

### Live run view
When a user uploads an invoice, show each processing stage as it happens,
in sequence, visibly:
1. "Extracting data..." → show extracted fields once done
2. "Checking for anomalies..." → show pass/fail
3. "Matching against PO..." → show match result
4. "Decision: [APPROVE / FLAG / ESCALATE]" → show full plain-English reasoning

This should feel like watching the process think through the invoice step by
step, not just a spinner that jumps straight to a final answer.

### Dashboard / history view
A table showing all processed invoices: filename, vendor, invoice number,
total, decision status (with a color: green=approve, yellow=flag, red=escalate),
and timestamp. Clicking a row expands to show the full extracted data and full
reasoning for that decision.

### Batch support
Support uploading multiple PDFs at once, processing each one through the same
pipeline, and showing each in the history table.

## File structure suggestion
```
/app
  /backend
    main.py              <- FastAPI app, endpoints
    extraction.py         <- Claude API call for PDF -> structured JSON
    decision_engine.py     <- the deterministic rule logic above (pure Python, no LLM)
    reasoning.py          <- Claude API call that takes the decision + data and
                             writes the human-readable explanation paragraph
    data/
      po_dataset.json     <- provided
      vendor_history.json  <- provided
    runs_history.json (or sqlite) <- stores processed run results
  /frontend
    (upload UI, live run view, dashboard)
  /invoices
    (the 5 test PDFs provided)
```

## Important constraints
- The decision logic (decision_engine.py) must be deterministic, plain Python —
  no LLM call inside it. The LLM is only used in extraction.py (reading the PDF)
  and reasoning.py (writing the explanation of a decision already made).
- This needs to actually run live, end to end, with real file uploads — not a
  mockup or hardcoded demo.
- Keep it lean. We are NOT building a polished production product — we need
  it to run correctly and be explainable, per the case study's own guidance:
  "A process that handles 3 scenarios well beats one that half-handles ten."

## What to build first (in order)
1. Backend skeleton: FastAPI app with a single /upload endpoint that accepts
   a PDF and returns a hardcoded dummy response (just to confirm the pipe works)
2. extraction.py: wire up Claude API call, send the PDF, get back structured JSON
3. decision_engine.py: implement the exact rule logic above, test against our
   5 known test invoices and confirm each produces the EXPECTED outcome we
   designed (invoice 1 = approve, invoice 2 = flag, invoice 3 = flag,
   invoice 4 = flag, invoice 5 = escalate)
4. reasoning.py: generate the explanation text
5. Frontend: upload UI + live run view
6. Dashboard: history table with expand-to-detail

Let's start with step 1 — the FastAPI skeleton with the dummy endpoint — and
confirm that runs before moving to extraction.
