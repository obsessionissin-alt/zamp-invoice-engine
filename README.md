# Zamp Invoice Processing Engine
**Accounts Payable Automation — Case Study PS-1**

An end-to-end automated invoice processing system that extracts structured data from vendor PDF invoices, matches them against Purchase Orders, detects fraud signals, and produces a deterministic **APPROVE / FLAG / ESCALATE** decision with full plain-English reasoning at every stage.

---

## Live Demo Flow

Upload a PDF → watch 4 stages complete in real time:

```
Stage 1  Extracting data...      → vendor, invoice #, line items, total, confidence
Stage 2  Checking anomalies...   → bank change? duplicate? math error?
Stage 3  Matching against PO...  → PO found? vendor match? within tolerance?
Stage 4  Decision                → APPROVE / FLAG / ESCALATE + plain-English reasoning
```

---

## Core Design Principle

> The AI extracts data and writes explanations. The approve/flag/escalate **decision is deterministic rule-based code — not an LLM judgment call.**

This makes every decision auditable, consistent, and explainable.

---

## Tech Stack

| Layer | Choice |
|---|---|
| Backend | Python · FastAPI · Server-Sent Events |
| PDF extraction | PyMuPDF (text) · Gemini 2.5 Flash (vision) |
| LLM structuring | Groq Llama 3.3 70B (primary, free) |
| Vision fallback | Gemini 2.5 Flash (free) |
| Decision engine | Pure Python — zero LLM |
| Frontend | Vanilla HTML/CSS/JS — no build step |
| Storage | JSON file |

---

## Quickstart

### 1. Clone the repo
```bash
git clone https://github.com/YOUR_USERNAME/zamp-invoice-engine.git
cd zamp-invoice-engine
```

### 2. Install dependencies
```bash
pip install -r requirements.txt
```

### 3. Add your API keys
Copy `.env.example` to `.env` and fill in your keys:
```
GROQ_API_KEY=gsk_...        # free — console.groq.com/keys
GOOGLE_API_KEY=AIza...      # free — aistudio.google.com/apikey
OPENAI_API_KEY=             # optional paid fallback
```

### 4. Run
```bash
cd app/backend
python -m uvicorn main:app --reload --port 8000
```

Open **http://localhost:8000**

---

## Decision Logic (3-step rule engine)

### Step 1 — Anomaly check → ESCALATE
- Bank account on invoice ≠ vendor's last known account (fraud signal)
- Invoice number already in vendor's processed list (duplicate)
- Line items don't sum to stated total (math inconsistency)

### Step 2 — Extraction confidence → FLAG
- Invoice number is null (engine overrides LLM confidence — cannot check duplicates)
- Total is null (cannot verify match)
- Document visibly degraded or unreadable

### Step 3 — PO match
| Condition | Decision |
|---|---|
| No PO found | FLAG |
| Vendor on invoice ≠ vendor on PO | ESCALATE |
| Total within `min(2% of PO, Rs.500)` | APPROVE |
| Total exceeds PO + tolerance | FLAG |
| Total much less than PO (partial billing) | FLAG |

---

## Test Invoices

| File | Expected | Scenario |
|---|---|---|
| `invoice_1_happy_path.pdf` | ✅ APPROVE | Exact match, Apex Office Supplies, PO-1001 |
| `invoice_2_near_miss.pdf` | 🟡 FLAG | Rs.76,700 vs PO Rs.76,200 (+freight) |
| `invoice_3_split_po.pdf` | 🟡 FLAG | Rs.31,250 = 25% of Rs.1,25,000 PO |
| `invoice_4_scanned_lowqual.pdf` | 🟡 FLAG | Blurry scan, no invoice number readable |
| `invoice_5_bank_change.pdf` | 🔴 ESCALATE | Bank account changed (fraud signal) |

---

## Project Structure

```
app/
  backend/
    main.py             FastAPI — SSE streaming, /api/upload, /api/history
    extraction.py       LLM extraction — Groq → Gemini → GPT-4o fallback
    decision_engine.py  Deterministic rules — zero LLM
    reasoning.py        LLM explanation generator
    data/
      po_dataset.json
      vendor_history.json
  frontend/
    index.html          Complete UI
Invoices/               5 test PDFs
PROJECT_GUIDE.md        Full technical deep-dive
```

---

## Full Documentation

See **[PROJECT_GUIDE.md](PROJECT_GUIDE.md)** for:
- Complete architecture diagram
- All 7 prompting principles explained
- Decision logic with worked examples
- API fallback chain rationale
- Step-by-step run guide
