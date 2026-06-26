"""
FastAPI application — Invoice Processing Decision Engine
Endpoints:
  POST /api/upload   — accepts one or more PDFs, streams SSE events per stage
  GET  /api/history  — returns all past run results
  GET  /             — serves the frontend index.html
"""

import json
import os
import uuid
from datetime import datetime
from pathlib import Path

from dotenv import find_dotenv, load_dotenv
from fastapi import FastAPI, File, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

import decision_engine
import extraction
import reasoning

load_dotenv(find_dotenv(usecwd=True))

BASE_DIR = Path(__file__).parent
FRONTEND_DIR = BASE_DIR.parent / "frontend"
HISTORY_FILE = BASE_DIR / "runs_history.json"

app = FastAPI(title="Zamp Invoice Processor")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# History helpers
# ---------------------------------------------------------------------------

def _load_history() -> list:
    if HISTORY_FILE.exists():
        with open(HISTORY_FILE, "r") as f:
            return json.load(f)
    return []


def _save_run(run: dict) -> None:
    history = _load_history()
    history.insert(0, run)  # newest first
    with open(HISTORY_FILE, "w") as f:
        json.dump(history, f, indent=2)


# ---------------------------------------------------------------------------
# SSE helpers
# ---------------------------------------------------------------------------

def _sse(event: str, data: dict) -> str:
    """Format a Server-Sent Event string."""
    payload = json.dumps(data)
    return f"event: {event}\ndata: {payload}\n\n"


# ---------------------------------------------------------------------------
# Pipeline for a single invoice (generator — yields SSE strings)
# ---------------------------------------------------------------------------

async def _process_invoice(filename: str, pdf_bytes: bytes):
    run_id = str(uuid.uuid4())
    started_at = datetime.utcnow().isoformat() + "Z"

    # Stage 1 — Extraction
    yield _sse("stage", {"stage": 1, "status": "running", "label": "Extracting data from invoice..."})
    try:
        extracted = extraction.extract(pdf_bytes)
    except Exception as e:
        yield _sse("error", {"message": f"Extraction failed: {e}"})
        return

    yield _sse("stage", {
        "stage": 1,
        "status": "done",
        "label": "Extraction complete",
        "data": extracted,
    })

    # Stage 2 — Anomaly check
    yield _sse("stage", {"stage": 2, "status": "running", "label": "Checking for anomalies..."})

    engine_result = decision_engine.run(extracted)
    decision = engine_result["decision"]
    rule_triggered = engine_result["rule_triggered"]
    raw_reason = engine_result["raw_reason"]
    po_match = engine_result["po_match"]

    anomaly_rules = {"bank_account_changed", "duplicate_invoice", "line_items_mismatch"}
    if rule_triggered in anomaly_rules:
        yield _sse("stage", {
            "stage": 2,
            "status": "escalate",
            "label": "Anomaly detected — escalating",
            "detail": raw_reason,
        })
    else:
        yield _sse("stage", {
            "stage": 2,
            "status": "done",
            "label": "No anomalies detected",
        })

    # Stage 3 — PO match (only meaningful if anomaly check passed)
    if rule_triggered not in anomaly_rules:
        yield _sse("stage", {"stage": 3, "status": "running", "label": "Matching against Purchase Orders..."})

        if rule_triggered == "low_confidence":
            yield _sse("stage", {
                "stage": 3,
                "status": "skipped",
                "label": "PO match skipped — extraction confidence too low",
            })
        elif po_match:
            yield _sse("stage", {
                "stage": 3,
                "status": "done",
                "label": f"PO match found: {po_match['po_number']} ({po_match['vendor']})",
                "po": po_match,
            })
        else:
            yield _sse("stage", {
                "stage": 3,
                "status": "warn",
                "label": f"No PO found for reference '{extracted.get('po_reference', 'N/A')}'",
            })
    else:
        yield _sse("stage", {
            "stage": 3,
            "status": "skipped",
            "label": "PO match skipped — anomaly escalation in effect",
        })

    # Stage 4 — Generate reasoning + final decision
    yield _sse("stage", {"stage": 4, "status": "running", "label": "Generating decision reasoning..."})

    try:
        reasoning_result = reasoning.generate(
            extracted=extracted,
            decision=decision,
            rule_triggered=rule_triggered,
            raw_reason=raw_reason,
            po_match=po_match,
        )
    except Exception as e:
        reasoning_result = {
            "reasoning": raw_reason,
            "model_used": "fallback (raw rule reason)",
        }

    # Build full run record
    run = {
        "run_id": run_id,
        "filename": filename,
        "started_at": started_at,
        "completed_at": datetime.utcnow().isoformat() + "Z",
        "decision": decision,
        "rule_triggered": rule_triggered,
        "reasoning": reasoning_result["reasoning"],
        "extraction_model": extracted.get("model_used", "unknown"),
        "reasoning_model": reasoning_result["model_used"],
        "extracted": extracted,
        "po_match": po_match,
    }

    _save_run(run)

    yield _sse("stage", {
        "stage": 4,
        "status": "done",
        "label": f"Decision: {decision}",
        "decision": decision,
        "reasoning": reasoning_result["reasoning"],
        "model_used": reasoning_result["model_used"],
    })

    yield _sse("complete", {"run_id": run_id, "decision": decision})


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.post("/api/upload")
async def upload_invoices(files: list[UploadFile] = File(...)):
    """
    Accept one or more PDF files and stream SSE events for each stage.
    Multiple files are processed sequentially; each emits its own set of events.
    """
    async def event_stream():
        for file in files:
            pdf_bytes = await file.read()
            yield _sse("file_start", {"filename": file.filename})
            async for event in _process_invoice(file.filename, pdf_bytes):
                yield event
            yield _sse("file_end", {"filename": file.filename})
        yield _sse("all_done", {})

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@app.get("/api/history")
async def get_history():
    return _load_history()


@app.get("/api/health")
async def health():
    return {"status": "ok"}


# Serve frontend
@app.get("/")
async def serve_index():
    return FileResponse(FRONTEND_DIR / "index.html")


# Mount static assets (if any are added later)
if FRONTEND_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(FRONTEND_DIR)), name="static")
