# Procure Co-pilot: RFx to award, end to end

A buyer describes an RFx to an AI co-pilot and sends it out. Vendors reply in whatever format they like: Excel, PDF, Word, a phone photo or a one-line email. Claude reads every reply. Deterministic code then normalises units, currency and freight, checks each number against its source, scores confidence and applies the quality gate. The buyer reviews what the system isn't sure about, asks questions in plain English, and exports a defensible award.

**Rule followed:** the plumbing is stubbed (email transport). The AI loops are real: extraction, co-pilot, analyst, clarification emails and the award memo all call Claude. Nothing is hard-coded to the demo data.

## Run it (about 5 minutes)
```bash
cd procure_copilot
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env          # paste your ANTHROPIC_API_KEY
streamlit run app.py
```
If the default model id isn't enabled on your key, set `CLAUDE_MODEL` in `.env` (for example to your Sonnet or Opus model).

**Demo path:**

1. **1 · Create RFx:** load the demo RFx, or chat to create one.
2. **2 · Send & receive:** send it (stubbed), then *Simulate vendor replies*.
3. **3 · Extract & review:** *Run AI extraction*. This takes about 1–3 minutes the first time; results are cached after that.
4. **4 · Compare** → **5 · Quality & terms** → **6 · Ask the analyst** → **7 · Award & export**.

Before the live demo, run extraction once so it's cached; a re-run is then instant. Check the result with `python eval/score_extraction.py`, which scores the live extraction against the dataset's answer key.

## Architecture
```
vendor files ──► readers.py ──► extract.py (Claude, 1 call/vendor, forced JSON schema, verbatim citations)
                 (xlsx cells A12, docx P9, pdf page, eml L3, image)        │ reads & interprets, never converts or computes
                                                                            ▼
buyer history (ERP last-year prices, FX) ─► normalize.py (pure Python) ─► SQLite analysis tables
   units (per 100, per kg x spec weight, per metre), USD→INR, freight → landed cost, cross-refs ("same as item 6"),
   "same as last year" lookup, citation verification, arithmetic / size / outlier checks, confidence, quality gate
                                                                            │
      review queue + overrides (audit-logged) ◄─────────────────────────────┤
      analyst.py (Claude + tools: run_sql [read-only], award_scenario, show_table, make_chart, export_excel, get_evidence)
      award.py (deterministic scenarios, shared by UI and agent) ─► exporter.py (Excel with sources + audit, memo)
```

| Module | Job |
|---|---|
| `core/extract.py` | Extraction schema and prompt. One entry per RFx line, including lines the vendor didn't quote |
| `core/normalize.py` | All the arithmetic, plus a record of how each number was derived |
| `core/award.py` | Award scenarios: line-wise cheapest or single vendor, with gate mode, estimates and unconfirmed-price toggles |
| `core/analyst.py` | The analyst agent loop, its data dictionary and the SQL guard |
| `core/copilot.py` | RFx drafting via a structured tool call |
| `core/comms.py` | Stubbed outbox/inbox, plus AI-drafted clarification emails |
| `ui/` | The seven screens |

## Tests (no API key needed)
```bash
python tests/test_pipeline.py         # normalisation + gate vs answer key: 114/114 landed prices, gates correct
python tests/test_agents.py           # analyst loop plumbing, SQL guard, award engine, Excel export, overrides
python tests/test_extract_plumbing.py # every file type -> Claude content blocks, forced tool call, cache
python tests/test_ui_smoke.py         # executes every screen headless
```
The tests use `tests/fixture_ideal_extraction.py`, which is what a perfect extractor would return. It only exercises the deterministic code; **the app never uses it**.

See `docs/decision_note.md` for the one-page note and `docs/demo_script.md` for the analyst walkthrough.
