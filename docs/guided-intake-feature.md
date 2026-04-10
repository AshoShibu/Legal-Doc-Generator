# Guided Intake Feature — Design Documentation

## Problem Statement

The current pipeline generates documents that are structurally correct but factually generic. The LLM receives only OCR-extracted fields from a scanned land record (survey number, owner name, area) as its fact pattern. This is too sparse for a real legal document — a Sale Deed needs the exact consideration amount, mode of payment, encumbrance history, and witness details; a Power of Attorney needs the scope of authority, duration, and revocation conditions; a Leave and License Agreement needs rent, deposit, lock-in period, and permitted use.

The result is a document full of placeholder-like language that a lawyer still has to rewrite from scratch. The fix is a **Guided Intake Form** — a structured Q&A step inserted between OCR extraction and LLM generation that collects the document-specific facts the LLM needs to produce a complete, usable first draft.

---

## Where This Fits in the Existing Pipeline

The current pipeline has 7 stages:

```
OCR Extraction → PII Redaction → Cache Loading → LLM Generation → Citation Verification → Export → Run Log
```

The Guided Intake step is inserted between PII Redaction and Cache Loading:

```
OCR Extraction → PII Redaction → [GUIDED INTAKE] → Cache Loading → LLM Generation → Citation Verification → Export → Run Log
```

The OCR result pre-fills as many intake fields as it can (survey number, owner name, area). The user fills in the rest through the form. The combined result — OCR fields merged with intake answers — becomes the enriched fact pattern that is passed to the LLM prompt. Nothing else in the pipeline changes.

---

## System Components Involved

| Component | File | Change Required |
|---|---|---|
| Intake schema definitions | `config/intake_schemas/` (new) | New directory — one JSON schema per document type |
| Intake form renderer | `src/intake/form_renderer.py` (new) | Renders Streamlit widgets from schema; returns filled answers |
| Fact pattern builder | `src/intake/fact_pattern_builder.py` (new) | Merges OCR fields + intake answers into enriched fact pattern dict |
| CAG Engine prompt | `src/cag/engine.py` | `_build_generation_prompt()` updated to accept structured dict |
| Streamlit frontend | `src/frontend/app.py` | New intake step between OCR and generation |
| Tests | `tests/intake/` (new) | Schema validation, pre-fill logic, fact pattern merge |

---

## Intake Schema Format

Each document type has a JSON schema file at `config/intake_schemas/{document_type}.json`. The schema defines every question the form will ask, in order.

### Schema structure

```json
{
  "document_type": "sale_deed",
  "display_name": "Sale Deed",
  "sections": [
    {
      "title": "Parties",
      "fields": [
        {
          "id": "seller_name",
          "label": "Full name of Seller (Vendor)",
          "type": "text",
          "required": true,
          "ocr_source": "owner_name",
          "help": "As it appears on the 7/12 Extract or Property Card"
        },
        {
          "id": "buyer_name",
          "label": "Full name of Buyer (Vendee)",
          "type": "text",
          "required": true,
          "ocr_source": null
        },
        {
          "id": "seller_address",
          "label": "Seller's permanent address",
          "type": "textarea",
          "required": true,
          "ocr_source": null
        },
        {
          "id": "buyer_address",
          "label": "Buyer's permanent address",
          "type": "textarea",
          "required": true,
          "ocr_source": null
        }
      ]
    },
    {
      "title": "Property Details",
      "fields": [
        {
          "id": "survey_number",
          "label": "Survey / CTS Number",
          "type": "text",
          "required": true,
          "ocr_source": "Survey_Number"
        },
        {
          "id": "area_sqm",
          "label": "Total area (sq. metres)",
          "type": "number",
          "required": true,
          "ocr_source": "area"
        },
        {
          "id": "village",
          "label": "Village / Locality",
          "type": "text",
          "required": true,
          "ocr_source": null
        },
        {
          "id": "taluka",
          "label": "Taluka",
          "type": "text",
          "required": true,
          "ocr_source": null
        },
        {
          "id": "district",
          "label": "District",
          "type": "select",
          "required": true,
          "options": ["Pune", "Mumbai", "Thane", "Nashik", "Nagpur", "Aurangabad", "Other"],
          "ocr_source": null
        },
        {
          "id": "encumbrance",
          "label": "Is the property free from all encumbrances?",
          "type": "boolean",
          "required": true,
          "ocr_source": null
        },
        {
          "id": "encumbrance_details",
          "label": "If encumbered, describe the encumbrance",
          "type": "textarea",
          "required": false,
          "conditional_on": {"field": "encumbrance", "value": false},
          "ocr_source": null
        }
      ]
    },
    {
      "title": "Transaction",
      "fields": [
        {
          "id": "consideration_amount",
          "label": "Sale consideration (₹)",
          "type": "number",
          "required": true,
          "ocr_source": null,
          "help": "Total amount agreed between parties"
        },
        {
          "id": "payment_mode",
          "label": "Mode of payment",
          "type": "select",
          "required": true,
          "options": ["RTGS/NEFT", "Cheque", "Demand Draft", "Cash", "Part cash / Part RTGS"],
          "ocr_source": null
        },
        {
          "id": "advance_paid",
          "label": "Advance / token amount already paid (₹)",
          "type": "number",
          "required": false,
          "ocr_source": null
        },
        {
          "id": "execution_date",
          "label": "Date of execution",
          "type": "date",
          "required": true,
          "ocr_source": null
        },
        {
          "id": "registration_office",
          "label": "Sub-Registrar office for registration",
          "type": "text",
          "required": true,
          "ocr_source": null,
          "help": "e.g. Sub-Registrar, Haveli No. 1, Pune"
        }
      ]
    },
    {
      "title": "Witnesses",
      "fields": [
        {
          "id": "witness_1_name",
          "label": "Witness 1 — Full name",
          "type": "text",
          "required": true,
          "ocr_source": null
        },
        {
          "id": "witness_2_name",
          "label": "Witness 2 — Full name",
          "type": "text",
          "required": true,
          "ocr_source": null
        }
      ]
    }
  ]
}
```

### Field types supported

| Type | Streamlit widget | Notes |
|---|---|---|
| `text` | `st.text_input` | Single-line string |
| `textarea` | `st.text_area` | Multi-line string |
| `number` | `st.number_input` | Integer or float |
| `date` | `st.date_input` | Returns `datetime.date` |
| `select` | `st.selectbox` | Requires `options` list |
| `multiselect` | `st.multiselect` | Requires `options` list |
| `boolean` | `st.checkbox` | True/False |

### `ocr_source` pre-fill

When `ocr_source` is set to a non-null string, the form renderer looks up that key in the `OCRResult.fields` dict and pre-populates the widget's default value. The user can override it. This means a well-scanned 7/12 Extract will auto-fill survey number, owner name, and area — the user only needs to fill in the transaction-specific fields.

### `conditional_on`

A field with `conditional_on` is only rendered if the referenced field currently has the specified value. In the example above, `encumbrance_details` only appears when the user answers "No" to the encumbrance question. This keeps the form clean and avoids irrelevant questions.

---

## Intake Schemas for All 7 Document Types

Each schema covers the sections and fields specific to that document type. Below is a summary of what each schema collects beyond the common OCR-extracted fields.

### Sale Deed (`sale_deed.json`)

Sections: Parties, Property Details, Transaction, Witnesses

Key fields beyond OCR: buyer name and address, consideration amount, payment mode, advance paid, execution date, Sub-Registrar office, witness names.

### Mortgage Deed (`mortgage_deed.json`)

Sections: Parties, Property Details, Loan Terms, Repayment Schedule, Witnesses

Key fields: mortgagor and mortgagee names, loan amount, interest rate (% p.a.), repayment period (months), EMI amount, type of mortgage (simple / equitable / English), whether possession is transferred, default clause details.

### Power of Attorney (`power_of_attorney.json`)

Sections: Parties, Scope of Authority, Duration, Property Details (if applicable), Witnesses

Key fields: principal name, attorney name, specific powers granted (multi-select: sell, lease, mortgage, litigate, collect rent, execute documents), whether general or special POA, effective date, expiry date (if limited), revocation conditions.

### Leave and License Agreement (`leave_and_license.json`)

Sections: Parties, Premises Details, Financial Terms, Duration, Permitted Use, Witnesses

Key fields: licensor and licensee names, premises address and area, monthly license fee (₹), security deposit (₹), lock-in period (months), agreement duration (months), permitted use (residential / commercial / mixed), maintenance responsibility, notice period for termination.

### Gift Deed (`gift_deed.json`)

Sections: Parties, Property Details, Gift Conditions, Witnesses

Key fields: donor and donee names and relationship, whether gift is conditional (boolean), conditions if any, whether donee accepts the gift (boolean), date of delivery of possession.

### Conveyance Deed (`conveyance_deed.json`)

Sections: Parties, Property Details, Development Context, Transaction, Witnesses

Key fields: developer/society name, flat/unit number, floor, building name, carpet area, built-up area, common area share, OC (Occupancy Certificate) status, MahaRERA registration number, consideration amount, stamp duty paid.

### Affidavit (`affidavit.json`)

Sections: Deponent, Purpose, Statements, Notarisation

Key fields: deponent name, age, occupation, address, purpose of affidavit (select: property ownership, name correction, income declaration, address proof, other), list of factual statements (dynamic — user can add up to 10 statements), whether sworn before Notary or Executive Magistrate, place of execution.

---

## Step-by-Step: How the Feature Works End-to-End

### Step 1 — User uploads document and selects document type (unchanged)

The user uploads a PDF/image and selects the document type from the sidebar dropdown. This is identical to the current flow.

### Step 2 — OCR runs automatically on upload

When the user clicks "Generate Document", the pipeline first runs OCR extraction (`src/ocr/pipeline.py`). The `OCRResult.fields` dict is stored in `st.session_state.ocr_result`.

This is a change from the current flow where OCR runs as part of the full pipeline. With guided intake, OCR runs first and its output is used to pre-fill the form before the user sees it.

### Step 3 — Intake form is rendered

The frontend loads `config/intake_schemas/{doc_type}.json` and calls `form_renderer.render_form(schema, ocr_fields)`. The form is displayed section by section in the main panel.

Fields with `ocr_source` are pre-populated from `OCRResult.fields`. The user reviews, corrects, and fills in the remaining fields. Conditional fields appear/disappear dynamically as the user answers.

A "Proceed to Generate" button appears at the bottom of the form. It is disabled until all `required` fields are filled.

### Step 4 — Fact pattern is built

When the user clicks "Proceed to Generate", `fact_pattern_builder.build(schema, ocr_result, intake_answers)` is called. It produces a structured dict:

```python
{
  "document_type": "sale_deed",
  "ocr_fields": {
    "Survey_Number": "123/4A",
    "owner_name": "<PETITIONER_1>",
    "area": "500 sq. metres"
  },
  "intake": {
    "seller_name": "<PETITIONER_1>",
    "buyer_name": "<RESPONDENT_1>",
    "seller_address": "...",
    "buyer_address": "...",
    "survey_number": "123/4A",
    "area_sqm": 500,
    "village": "Hadapsar",
    "taluka": "Haveli",
    "district": "Pune",
    "encumbrance": true,
    "consideration_amount": 4500000,
    "payment_mode": "RTGS/NEFT",
    "advance_paid": 450000,
    "execution_date": "2026-03-20",
    "registration_office": "Sub-Registrar, Haveli No. 1, Pune",
    "witness_1_name": "<WITNESS_1>",
    "witness_2_name": "<WITNESS_2>"
  }
}
```

Note that names entered in the intake form are passed through the PII redactor before being included in the fact pattern. The redactor assigns sequential placeholders (`<PETITIONER_1>`, `<RESPONDENT_1>`, `<WITNESS_1>`) and records them in the audit log so the final document can be de-anonymised if needed.

### Step 5 — Enriched fact pattern is injected into the LLM prompt

`_build_generation_prompt()` in `src/cag/engine.py` already accepts a `dict | str` fact pattern. The enriched dict is serialised to a formatted JSON string and injected into the prompt. The LLM now has all the specific facts it needs to produce a complete, non-generic draft.

The prompt instruction block is updated to tell the LLM to use the intake fields directly:

```
FACT PATTERN (use these exact values in the document):
{
  "seller_name": "<PETITIONER_1>",
  "buyer_name": "<RESPONDENT_1>",
  "consideration_amount": "₹45,00,000 (Rupees Forty-Five Lakhs only)",
  "payment_mode": "RTGS/NEFT",
  ...
}
```

### Step 6 — Rest of pipeline is unchanged

Cache loading, LLM generation, citation verification, export, and run logging all proceed exactly as before. The only difference is that the LLM now has a rich, specific fact pattern instead of a sparse OCR dump.

---

## New Files to Create

### `config/intake_schemas/` (directory)

One JSON file per document type:

```
config/intake_schemas/
├── sale_deed.json
├── mortgage_deed.json
├── power_of_attorney.json
├── leave_and_license.json
├── gift_deed.json
├── conveyance_deed.json
└── affidavit.json
```

### `src/intake/form_renderer.py`

```python
def render_form(schema: dict, ocr_fields: dict) -> dict | None:
    """
    Render a Streamlit intake form from a schema dict.
    Pre-fills fields from ocr_fields where ocr_source is set.
    Returns the filled answers dict when the user submits,
    or None if the form has not been submitted yet.
    """
```

Key responsibilities:
- Iterate over `schema["sections"]` and render each as a Streamlit expander or header.
- For each field, render the appropriate widget type.
- Apply `ocr_source` pre-fill: `default = ocr_fields.get(field["ocr_source"])` if `ocr_source` is set.
- Apply `conditional_on` logic: skip rendering the field if the condition is not met.
- Track which required fields are empty and disable the submit button accordingly.
- Return the answers dict on submit.

### `src/intake/fact_pattern_builder.py`

```python
def build(
    schema: dict,
    ocr_result: OCRResult,
    intake_answers: dict,
    redaction_result: RedactionResult,
) -> dict:
    """
    Merge OCR fields and intake answers into a single enriched fact pattern dict.
    Intake answers that contain personal names are already redacted by this point.
    Returns a structured dict ready for injection into the LLM prompt.
    """
```

Key responsibilities:
- Combine `ocr_result.fields` and `intake_answers` into a single flat dict.
- Format numeric fields (e.g. `consideration_amount: 4500000` → `"₹45,00,000 (Rupees Forty-Five Lakhs only)"`).
- Format date fields as `"20 March 2026"`.
- Include `document_type` and a `generated_at` timestamp.
- Return the structured dict.

### `src/intake/__init__.py`

Empty init file to make `src/intake` a package.

### `tests/intake/` (directory)

```
tests/intake/
├── test_schema_validation.py     # All 7 schemas parse without error; required fields present
├── test_prefill.py               # ocr_source fields are pre-filled correctly
├── test_fact_pattern_builder.py  # Merge logic, number formatting, date formatting
└── test_conditional_fields.py    # Conditional fields appear/disappear correctly
```

---

## Changes to Existing Files

### `src/frontend/app.py`

The pipeline runner `_run_pipeline()` is split into two phases:

**Phase A — OCR + Intake (triggered on "Generate Document" click):**

```python
# 1. Run OCR
ocr_result = extract(tmp_path)
st.session_state.ocr_result = ocr_result

# 2. Load intake schema
schema = load_intake_schema(doc_type)  # reads config/intake_schemas/{doc_type}.json

# 3. Set stage to "intake" — triggers form rendering on next rerun
st.session_state.stage = "intake"
st.session_state.intake_schema = schema
st.rerun()
```

**Phase B — Generation (triggered on "Proceed to Generate" click):**

```python
# 4. Build enriched fact pattern
fact_pattern = build(schema, ocr_result, intake_answers, redaction_result)

# 5. Continue with existing pipeline: cache loading → LLM → export → log
```

A new `"intake"` stage is added to the session state machine:

```
idle → running_ocr → intake → running_generation → done | error
```

The main panel renders the intake form when `st.session_state.stage == "intake"`.

### `src/cag/engine.py`

`_build_generation_prompt()` receives the enriched fact pattern dict. The prompt template is updated to include a section that explicitly lists each intake field with its label and value, instructing the LLM to use these exact values in the document rather than inventing them.

No changes to `load_cache()` or `generate_draft()` signatures.

### `config/settings.py`

Add `INTAKE_SCHEMAS_DIR` environment variable (default: `./config/intake_schemas`).

---

## Session State Changes

Two new keys are added to `st.session_state`:

| Key | Type | Description |
|---|---|---|
| `intake_schema` | `dict` | Loaded JSON schema for the current document type |
| `intake_answers` | `dict` | Filled answers from the intake form |

The `stage` key gains two new values:

| Value | Meaning |
|---|---|
| `"running_ocr"` | OCR is in progress |
| `"intake"` | OCR complete; intake form is displayed |

---

## PII Handling in Intake Answers

Names entered in the intake form (seller name, buyer name, witness names, etc.) must go through the PII redactor before being included in the fact pattern sent to the LLM. The flow is:

1. User fills in `seller_name = "Ramesh Kumar Sharma"` in the form.
2. On submit, `fact_pattern_builder.build()` calls `redact()` on the name fields.
3. The redactor assigns `<PETITIONER_1>` and records the mapping in the audit log.
4. The fact pattern sent to the LLM contains `"seller_name": "<PETITIONER_1>"`.
5. The generated document contains `<PETITIONER_1>` as a placeholder.
6. The audit log retains the mapping so a lawyer can do a find-and-replace before filing.

This preserves the existing PII architecture — the LLM never sees real names.

---

## Validation Rules

The form renderer enforces these rules before enabling the submit button:

1. All fields with `"required": true` must be non-empty.
2. `number` fields must be non-negative.
3. `date` fields must not be in the past (configurable — some documents like affidavits may need past dates).
4. `select` fields must have a value from the `options` list.
5. Conditional fields are only validated if their condition is currently met.

Validation errors are shown inline below each field using `st.error()`.

---

## Backward Compatibility

The guided intake feature is additive. The existing `_run_pipeline()` logic is preserved intact — it is simply called after the intake form is submitted rather than directly on button click. The `generate_draft()` and `load_cache()` interfaces are unchanged. The enriched fact pattern dict is a superset of the current sparse OCR dict, so the LLM prompt assembly handles both formats (the `dict | str` type annotation on `fact_pattern` already supports this).

The `scripts/generate_all_types.py` script and `tests/test_multi_doc_output.py` continue to work without modification — they bypass the intake form and pass a stub fact pattern directly to `generate_draft()`, which is the correct behaviour for automated testing.

---

## Implementation Order

1. Create `config/intake_schemas/` and write all 7 JSON schema files.
2. Implement `src/intake/form_renderer.py` — schema loading, widget rendering, pre-fill, conditional logic, validation.
3. Implement `src/intake/fact_pattern_builder.py` — merge logic, number/date formatting, PII pass-through.
4. Update `src/frontend/app.py` — split pipeline into OCR phase and generation phase, add intake stage to session state machine, render form in main panel.
5. Update `src/cag/engine.py` — enrich the prompt template to use structured intake fields explicitly.
6. Write tests in `tests/intake/`.
7. Update `README.md` — add "Guided Intake" section to the dashboard usage guide.

---

## Example: Before and After

### Before (current output — generic)

```
SALE DEED

This Sale Deed is executed between <PETITIONER_1> (Party A) and <RESPONDENT_1> (Party B).

RECITALS
Whereas Party A is the absolute owner of the property described in the Schedule.
This deed is governed by [Transfer of Property Act, 1882] Section 54, [Legislature], [1882].

OPERATIVE CLAUSE 1
Party A hereby transfers the property to Party B for the consideration stated herein.

SCHEDULE
Survey Number: 123/4A, Village: Pune, Taluka: Haveli, District: Pune.
Area: 500 sq. metres.
```

### After (with guided intake)

```
SALE DEED

This Sale Deed is executed on the 20th day of March 2026 at Pune between:

VENDOR: <PETITIONER_1>, residing at <ADDRESS_1>, hereinafter referred to as "the Vendor";

AND

VENDEE: <RESPONDENT_1>, residing at <ADDRESS_2>, hereinafter referred to as "the Vendee".

RECITALS
Whereas the Vendor is the absolute owner of the property bearing Survey No. 123/4A,
situated at Village Hadapsar, Taluka Haveli, District Pune, admeasuring 500 sq. metres,
free from all encumbrances, as evidenced by the 7/12 Extract.

This deed is governed by [Transfer of Property Act, 1882] Section 54, [Legislature], [1882].

OPERATIVE CLAUSE 1 — CONSIDERATION
The Vendee has agreed to purchase and the Vendor has agreed to sell the said property
for a total consideration of ₹45,00,000 (Rupees Forty-Five Lakhs only), of which
₹4,50,000 (Rupees Four Lakhs Fifty Thousand only) has been paid as advance/token amount,
and the balance of ₹40,50,000 shall be paid by RTGS/NEFT at the time of registration.

OPERATIVE CLAUSE 2 — REGISTRATION
This deed shall be compulsorily registered before the Sub-Registrar, Haveli No. 1, Pune,
as required under [Registration Act, 1908] Section 17, [Legislature], [1908].

SCHEDULE
Survey No. 123/4A, Village Hadapsar, Taluka Haveli, District Pune.
Total area: 500 sq. metres. Boundaries: as per 7/12 Extract.

IN WITNESS WHEREOF the parties have signed this deed in the presence of:
Witness 1: <WITNESS_1>
Witness 2: <WITNESS_2>
```

The difference is the specificity of the consideration amount, payment mode, advance paid, registration office, and witness details — all of which came from the intake form.
