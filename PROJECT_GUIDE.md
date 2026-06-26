# nZamp Invoice Processing Engine — Complete Project Guide

**Case Study PS-1 | Accounts Payable Automation**

---

## Table of Contents

1. [What This Project Does](#1-what-this-project-does)
2. [System Architecture](#2-system-architecture)
3. [The Decision Logic — Exact Rules](#3-the-decision-logic--exact-rules)
4. [Prompting Strategy — The Critical Part](#4-prompting-strategy--the-critical-part)
5. [Tech Stack & Why Each Choice Was Made](#5-tech-stack--why-each-choice-was-made)
6. [File Structure](#6-file-structure)
7. [How to Run It](#7-how-to-run-it)
8. [The 5 Test Invoices — Expected Outcomes](#8-the-5-test-invoices--expected-outcomes)
9. [The API Fallback Chain](#9-the-api-fallback-chain)
10. [What the UI Shows](#10-what-the-ui-shows)

---

## 1. What This Project Does

This system automates the **Accounts Payable (AP) invoice review process**. In a real finance team, an AP clerk receives vendor invoices, checks them against Purchase Orders, looks for fraud signals, and decides whether to approve payment or escalate for review. This project does that automatically.-

**Input:** A vendor invoice PDF  
**Output:** One of three decisions — `APPROVE`, `FLAG`, or `ESCALATE` — with a full plain-English explanation of why

### Core design principle

> The AI extracts data and writes explanations. The actual approve/flag/escalate decision is **deterministic rule-based code** — not an LLM judgment call.

This is critical. It means:

- Every decision is **auditable** — you can trace exactly which rule fired
- Decisions are **consistent** — the same invoice always gets the same result
- The system is **explainable** to a non-technical auditor

---

## 2. System Architecture

```
┌──────────────────────────────────────────────────────────┐
│                    User uploads PDF(s)                    │
└──────────────────────┬───────────────────────────────────┘
                       │
                       ▼
┌──────────────────────────────────────────────────────────┐
│  extraction.py  — LLM reads the PDF                      │
│                                                           │
│  Tier 1: PyMuPDF extracts text → Groq Llama 3.3 (FREE)  │
│  Tier 2: Scanned PDF → PNG → Gemini vision (FREE)        │
│  Tier 3: Both fail → GPT-4o (paid fallback)              │
│                                                           │
│  Returns structured JSON:                                 │
│  { invoice_number, vendor_name, po_reference,            │
│    bank_account, line_items, total, confidence }         │
└──────────────────────┬───────────────────────────────────┘
                       │
                       ▼
┌──────────────────────────────────────────────────────────┐
│  decision_engine.py  — Pure Python rules, ZERO LLM       │
│                                                           │
│  Step 1: Anomaly check  → ESCALATE if triggered          │
│  Step 2: Confidence     → FLAG if extraction was poor    │
│  Step 3: PO match       → APPROVE / FLAG / ESCALATE      │
│                                                           │
│  Returns: { decision, rule_triggered, raw_reason }       │
└──────────────────────┬───────────────────────────────────┘
                       │
                       ▼
┌──────────────────────────────────────────────────────────┐
│  reasoning.py  — LLM writes the explanation              │
│                                                           │
│  Takes the decision + rule + data                        │
│  Returns 2-4 sentence plain-English paragraph            │
│  (Groq → Gemini → GPT-4o fallback chain)                 │
└──────────────────────┬───────────────────────────────────┘
                       │
                       ▼
┌──────────────────────────────────────────────────────────┐
│  main.py  — FastAPI streams live stages via SSE          │
│  frontend/index.html  — Shows each stage as it happens   │
└──────────────────────────────────────────────────────────┘
```

The flow is always **linear and one-directional**. No LLM ever influences the decision — only the data extraction and the explanation.

---

## 3. The Decision Logic — Exact Rules

This is implemented in `app/backend/decision_engine.py`. It runs **three steps in strict order**. If a step triggers, the process stops — later steps don't run.

### Step 1 — Anomaly Check (triggers ESCALATE)

These are **fraud / data integrity signals**. Any one of them escalates immediately.


| Check                | Condition                                                               | Why it matters                                              |
| -------------------- | ----------------------------------------------------------------------- | ----------------------------------------------------------- |
| Bank account changed | Invoice bank account ≠ `last_known_bank_account` in vendor_history.json | Classic fraud vector — attacker changes payment destination |
| Duplicate invoice    | Invoice number already in vendor's `processed_invoices` list            | Double-payment attempt                                      |
| Math error           | Sum of line items ≠ stated total (tolerance: Rs. 1)                     | Internal inconsistency suggests document tampering          |


### Step 2 — Extraction Confidence Check (triggers FLAG)

If the LLM couldn't reliably read the document, we cannot safely auto-process it.


| Condition            | Reason                                                                                  |
| -------------------- | --------------------------------------------------------------------------------------- |
| `confidence = "LOW"` | `invoice_number` or `total` could not be read reliably, or document is visibly degraded |


> Without a reliable invoice number, we cannot check for duplicates. That alone blocks approval.

### Step 3 — PO Match Check (APPROVE / FLAG / ESCALATE)


| Condition                               | Decision | Reasoning                              |
| --------------------------------------- | -------- | -------------------------------------- |
| No PO found for `po_reference`          | FLAG     | Human needs to locate or create the PO |
| Vendor on invoice ≠ vendor on PO        | ESCALATE | Risk signal — not just a typo          |
| Total within tolerance of PO amount     | APPROVE  | ✓ Clean match                          |
| Total **exceeds** PO amount + tolerance | FLAG     | Possible freight/tax overage           |
| Total **much less** than PO amount      | FLAG     | Partial/instalment billing             |


**Tolerance formula:** `min(2% of PO approved_amount, Rs. 500)`

Example calculations:

- PO = Rs. 18,500 → tolerance = min(Rs. 370, Rs. 500) = **Rs. 370**
- PO = Rs. 76,200 → tolerance = min(Rs. 1,524, Rs. 500) = **Rs. 500**
- PO = Rs. 9,000  → tolerance = min(Rs. 180, Rs. 500) = **Rs. 180**

The `min()` keeps tolerance **tight on large POs** — a Rs. 1,524 overrun on a Rs. 76,200 PO should not auto-approve.

---

## 4. Prompting Strategy — The Critical Part

Prompting is the most important engineering decision in this system. A bad prompt gives wrong or inconsistent extractions, which means wrong decisions downstream. Here is the exact strategy used, and why each part matters.

### The 7 principles applied

---

#### Principle 1: Role + Task Framing (System prompt)

```
You are a precise accounts-payable data extraction engine.
Your ONLY job is to read an invoice document and return a single valid JSON object.
You never invent, guess, or hallucinate field values.
If a field is absent or unreadable, you return null — never a placeholder.
```

**Why:** Models perform better when given a specific role. The "never hallucinate / return null" instruction is critical — without it, models sometimes invent plausible-looking invoice numbers for scanned documents they can't read clearly.

---

#### Principle 2: Exhaustive Field Synonyms

Each field in the extraction prompt lists every real-world label variant:

```
invoice_number — accept: Invoice #, Invoice No., Order ID, Order #,
                 Bill No, Receipt #, Reference #, Document No, Voucher No
```

**Why:** Invoices from different countries, industries, and software systems use completely different labels for the same concept. "Order ID" on a Northwind dataset invoice IS the invoice number. Without this, the model returns null and confidence drops to LOW.

This also covers:

- `vendor_name` — "From", "Billed By", "Supplier", "Company Name"
- `po_reference` — "PO #", "Against PO", "Purchase Order", "Order Reference"
- `total` — "Grand Total", "Amount Due", "Net Payable", "Invoice Total"
- `bank_account` — "A/C No", "Pay To Account", "Bank Account"

---

#### Principle 3: Output Contract (Exact JSON Schema)

```json
{
  "invoice_number" : string | null,
  "vendor_name"    : string | null,
  "po_reference"   : string | null,
  "invoice_date"   : string | null,
  "bank_account"   : string | null,
  "line_items"     : [ { "description": string, "amount": number } ],
  "total"          : number | null,
  "confidence"     : "HIGH" | "LOW"
}
```

**Why:** Specifying exact types (string vs number, null allowed) prevents the model from returning `"18,500"` (string with comma) instead of `18500` (number). The decision engine does arithmetic on `total` and `line_items.amount` — a string would break it.

---

#### Principle 4: Normalisation Rules

```
For amounts: plain numbers only — strip Rs., $, £, ₹, commas
For bank_account: return masked format as-is (e.g. XXXX-XXXX-4521)
```

**Why:** Invoices come in many formats. Without explicit normalisation, the model might return `"Rs. 18,500"`, `"18500.00"`, `"18,500"`, or `18500` — all for the same value. The code expects `18500`. This instruction standardises the output.

---

#### Principle 5: Binary Confidence with Precise Criteria

```
"LOW" if ANY of:
  - document is blurry, rotated, low-resolution, or partially illegible
  - invoice_number could not be found or read clearly
  - total could not be found or read clearly
  - More than 2 fields are null due to poor quality
"HIGH" if document is clear AND both invoice_number and total were extracted reliably
```

**Why:** Vague confidence criteria like "rate your confidence" produce inconsistent results. Binary HIGH/LOW with explicit testable conditions gives the model a decision tree it can follow reliably. Invoice 4 (blurry scan) reliably returns LOW because the criteria are unambiguous.

---

#### Principle 6: Few-Shot Example

The prompt includes one complete input → output example:

```
Input text:
  TAX INVOICE
  Apex Office Supplies | HDFC Bank | A/C: XXXX-XXXX-4521
  Invoice No: INV-2305  Date: 15-Mar-2024  PO Ref: PO-1001
  ...

Output:
{
  "invoice_number": "INV-2305",
  "vendor_name": "Apex Office Supplies",
  ...
}
```

**Why:** This is the single most powerful prompting technique for structured extraction. The example shows the model the exact format, field names, and value types expected — more reliably than any description alone. Models are pattern-matchers — give them a pattern to match.

---

#### Principle 7: Failure Instruction

```
If a field is absent or unreadable, return null — never a placeholder.
```

**Why:** Without this, models sometimes return `"N/A"`, `"unknown"`, `"not found"`, or `"-"` instead of `null`. The decision engine checks `if not invoice_number` — it correctly handles `null` but would treat `"N/A"` as a valid invoice number, bypassing the confidence check.

---

### Groq-specific: `response_format: {"type": "json_object"}`

For the Groq (text path) call, we pass `response_format={"type": "json_object"}`. This is an **OpenAI-compatible JSON mode** that forces the model to output only valid JSON — no markdown, no explanation, no preamble. This eliminates the need for regex-stripping of markdown fences and prevents malformed responses.

---

## 5. Tech Stack & Why Each Choice Was Made


| Component           | Choice                   | Why                                                                            |
| ------------------- | ------------------------ | ------------------------------------------------------------------------------ |
| Backend framework   | FastAPI                  | Async-native, built-in SSE support, fast                                       |
| Live updates        | Server-Sent Events (SSE) | Simpler than WebSockets for one-directional streaming                          |
| PDF text extraction | PyMuPDF (fitz)           | Pure Python, no external dependencies, cross-platform                          |
| Primary LLM         | Groq — Llama 3.3 70B     | Free tier (14,400 req/day), OpenAI-compatible API, no model deprecation issues |
| Vision LLM          | Gemini 2.5 Flash         | Free tier, best-in-class vision, handles degraded scans                        |
| Paid fallback       | GPT-4o                   | Most capable fallback if both free tiers fail                                  |
| Storage             | JSON file                | Sufficient for a case study — no database overhead                             |
| Frontend            | Vanilla HTML/CSS/JS      | No build step — starts instantly, fully auditable                              |
| Decision logic      | Pure Python              | Zero LLM — deterministic, testable, auditable                                  |
| Env config          | python-dotenv            | Standard Python practice for secrets management                                |


---

## 6. File Structure

```
zamp/
├── .env                        ← Your API keys (never commit this)
├── .env.example                ← Template showing what keys are needed
├── .gitignore                  ← Ensures .env is never committed
├── requirements.txt            ← Python dependencies
├── po_dataset.json             ← 8 Purchase Orders (source of truth)
├── vendor_history.json         ← 5 vendor records with bank details
├── PROJECT_GUIDE.md            ← This document
│
├── app/
│   ├── backend/
│   │   ├── main.py             ← FastAPI app, SSE streaming, /api/upload, /api/history
│   │   ├── extraction.py       ← LLM extraction with 3-tier fallback
│   │   ├── decision_engine.py  ← Deterministic rule engine (no LLM)
│   │   ├── reasoning.py        ← LLM explanation generator
│   │   ├── runs_history.json   ← Auto-created when first invoice is processed
│   │   └── data/
│   │       ├── po_dataset.json
│   │       └── vendor_history.json
│   └── frontend/
│       └── index.html          ← Complete UI (upload + live view + dashboard)
│
└── Invoices/
    ├── invoice_1_happy_path.pdf
    ├── invoice_2_near_miss.pdf
    ├── invoice_3_split_po.pdf
    ├── invoice_4_scanned_lowqual.pdf
    └── invoice_5_bank_change.pdf
```

---

## 7. How to Run It

### Prerequisites

- Python 3.11+
- Free Groq API key: [console.groq.com/keys](https://console.groq.com/keys)
- Free Google API key: [aistudio.google.com/apikey](https://aistudio.google.com/apikey)

### Step 1 — Create your `.env` file

Create a file at `C:\Users\0forv\OneDrive\Documents\zamp\.env` with:

```
GROQ_API_KEY=gsk_...
GOOGLE_API_KEY=AIza...
OPENAI_API_KEY=          ← optional, only needed if both free tiers fail
```

### Step 2 — Install dependencies (one time only)

```powershell
cd "C:\Users\0forv\OneDrive\Documents\zamp"
python -m pip install -r requirements.txt
```

### Step 3 — Start the server

```powershell
cd "C:\Users\0forv\OneDrive\Documents\zamp\app\backend"
python -m uvicorn main:app --reload --port 8000
```

You should see:

```
INFO:  Uvicorn running on http://127.0.0.1:8000
INFO:  Application startup complete.
```

### Step 4 — Open the app

Go to **[http://localhost:8000](http://localhost:8000)** in your browser.

### Step 5 — Upload an invoice

Drag and drop any PDF from the `Invoices/` folder onto the upload zone. Watch all 4 stages complete in real time.

---

## 8. The 5 Test Invoices — Expected Outcomes

These invoices were designed to test every branch of the decision engine.


| File                            | Vendor                | PO      | Invoice Total | Expected    | Why                                                          |
| ------------------------------- | --------------------- | ------- | ------------- | ----------- | ------------------------------------------------------------ |
| `invoice_1_happy_path.pdf`      | Apex Office Supplies  | PO-1001 | Rs. 18,500    | ✅ APPROVE   | Exact match within Rs. 370 tolerance                         |
| `invoice_2_near_miss.pdf`       | Brightway Electricals | PO-1003 | Rs. 76,700    | 🟡 FLAG     | Exceeds Rs. 76,200 PO by Rs. 500 (tolerance is Rs. 500 flat) |
| `invoice_3_split_po.pdf`        | Meridian IT Solutions | PO-1005 | Rs. 31,250    | 🟡 FLAG     | Only 25% of Rs. 1,25,000 PO — partial billing                |
| `invoice_4_scanned_lowqual.pdf` | Coastal Packaging Co. | PO-1007 | Rs. 54,000    | 🟡 FLAG     | No invoice number readable → confidence LOW                  |
| `invoice_5_bank_change.pdf`     | Apex Office Supplies  | PO-1004 | Rs. 8,750     | 🔴 ESCALATE | Bank account changed from XXXX-4521 to XXXX-9988             |


Run all 5 and verify the outcomes match exactly. If any differ, there is a prompt or rule issue.

---

## 9. The API Fallback Chain

### For extraction (reading the PDF)

```
Digital PDF (has selectable text)?
    YES → PyMuPDF extracts text → Groq Llama 3.3 70B structures it to JSON
           If Groq fails ↓
    NO  → PyMuPDF converts to PNG → Gemini 2.5 Flash reads the image
           If Gemini fails ↓
                          → GPT-4o reads the image (paid)
```

### For reasoning (writing the explanation)

```
Groq Llama 3.3 70B
    If Groq fails ↓
Gemini 2.5 Flash
    If Gemini fails ↓
GPT-4o
    If GPT-4o fails ↓
Raw rule reason returned as-is (always has a result)
```

### Why this ordering?

1. **Groq first** — most generous free tier (14,400 req/day), no vision quota to exhaust, OpenAI-compatible (easy integration)
2. **Gemini second** — only for vision (scanned PDFs), free tier, Google account required
3. **GPT-4o last** — most capable but paid; kept as a true emergency fallback

The `model_used` field in every result tells you which tier actually ran.

---

## 10. What the UI Shows

### Process Invoice tab

Upload one or more PDFs. For each file, the UI shows **4 stages completing in real time**:


| Stage            | What it shows                                                                                  |
| ---------------- | ---------------------------------------------------------------------------------------------- |
| 1. Extraction    | All extracted fields in a table (vendor, invoice #, line items, total, confidence, model used) |
| 2. Anomaly check | Pass ✓ or ESCALATE ⚠ with the specific anomaly described                                       |
| 3. PO match      | Matched PO details, or "no PO found", or "skipped" if anomaly/low confidence                   |
| 4. Decision      | Color-coded badge (green/amber/red) + full plain-English reasoning paragraph                   |


This step-by-step view is intentional — it lets a reviewer see exactly how the decision was reached at each stage, not just the final answer.

### History tab

A table of all processed invoices with:

- Filename, vendor, invoice number, total, decision badge, timestamp
- Click any row → expands to show full extracted data, PO match details, and reasoning

---

## Key Takeaways for the Video

1. **The LLM is a reader, not a judge.** It reads the PDF and writes explanations. The decision is always made by deterministic code.
2. **Prompting quality directly determines extraction quality.** The 7 principles above — especially field synonyms and the few-shot example — are what make the extraction work reliably across different invoice formats.
3. **The fallback chain means the system never goes down** because one API is having a bad day. Free tiers handle all normal traffic; paid is only insurance.
4. **Every decision is explainable.** `rule_triggered` tells you the exact rule. `reasoning` tells a finance manager why in plain English. The history log stores everything.
5. **This mirrors real AP workflows.** The three-bucket system (APPROVE / FLAG / ESCALATE) maps directly to real accounts-payable SLAs: auto-approve clean invoices, flag for same-day review, escalate for compliance investigation.

