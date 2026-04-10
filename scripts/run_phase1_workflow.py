#!/usr/bin/env python3
"""
scripts/run_phase1_workflow.py -- Phase 1 end-to-end workflow runthrough.

Runs all 7 document types through the full CAG pipeline with realistic
guided-intake data for each type:
  OCR -> PII Redaction -> CAG Draft (stubbed LLM) -> Document Generator
  -> DOCX + PDF Export -> Run Logger

Output is written to output/cag/{doc_type}/{run_id}/.
No live Ollama server required -- LLM calls are stubbed.

Usage:
    python scripts/run_phase1_workflow.py
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Load .env for GROQ_API_KEY
try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
except ImportError:
    pass  # dotenv optional -- key can also be set in environment directly

# Suppress noisy library loggers -- only show WARNING+ from dependencies
import logging
logging.basicConfig(level=logging.WARNING)
for noisy in ("src.cag", "src.ocr", "src.pii", "src.generation", "src.logging"):
    logging.getLogger(noisy).setLevel(logging.WARNING)
# cache_loader budget warnings are expected with small context models -- suppress
logging.getLogger("src.cag.cache_loader").setLevel(logging.ERROR)

# Suppress urllib3 version mismatch noise from requests
import warnings
warnings.filterwarnings("ignore", category=UserWarning, module="requests")

CORPUS = Path("Maharashtra Legal Document Dataset/Mortage and land deed")
SAMPLE_PDF = CORPUS / "mortgage_1.pdf"
OUTPUT_ROOT = ROOT / "output"
LLM_BACKEND = "groq_llama3_8b"  # Groq cloud -- llama-3.1-8b-instant, ~2s/doc

# ---------------------------------------------------------------------------
# Realistic guided-intake data -- one complete set per document type
# ---------------------------------------------------------------------------

INTAKE_DATA: dict[str, dict] = {
    "sale_deed": {
        "seller_name": "Ramesh Vishwanath Kulkarni",
        "seller_address": "Flat No. 4, Shivaji Nagar, Near Deccan Gymkhana, Pune - 411 004, Maharashtra",
        "buyer_name": "Priya Suresh Deshpande",
        "buyer_address": "Bungalow No. 12, Aundh Road, Kothrud, Pune - 411 038, Maharashtra",
        "survey_number": "Survey No. 47/2A, CTS No. 1204",
        "area_sqm": "242",
        "village": "Kothrud",
        "taluka": "Haveli",
        "district": "Pune",
        "encumbrance": "Yes -- property is free from all encumbrances",
        "consideration_amount": "₹85,00,000 (Rupees Eighty-Five Lakhs only)",
        "payment_mode": "RTGS/NEFT",
        "advance_paid": "₹8,50,000 paid as token advance on 10-Jan-2026 vide RTGS UTR No. PUNB0000123456",
        "execution_date": "15 March 2026",
        "registration_office": "Sub-Registrar, Haveli No. 2, Kothrud, Pune",
        "witness_1_name": "Anil Dattatray Joshi",
        "witness_2_name": "Sunita Prakash Pawar",
    },
    "mortgage_deed": {
        "mortgagor_name": "Suresh Narayan Patil",
        "mortgagor_address": "House No. 7, Ganesh Peth, Solapur - 413 001, Maharashtra",
        "mortgagee_name": "Bank of Maharashtra, Solapur Main Branch",
        "mortgagee_address": "Bank of Maharashtra, Main Branch, Vijapur Road, Solapur - 413 003, Maharashtra",
        "survey_number": "Survey No. 112/3B, Gat No. 445",
        "area_sqm": "810",
        "village": "Nandal",
        "taluka": "Solapur North",
        "district": "Solapur",
        "loan_amount": "₹40,00,000 (Rupees Forty Lakhs only)",
        "interest_rate": "8.75% per annum (floating, linked to MCLR)",
        "repayment_period_months": "180 months (15 years)",
        "emi_amount": "₹39,860 per month",
        "mortgage_type": "Simple Mortgage",
        "possession_transfer": "No -- possession remains with Mortgagor",
        "execution_date": "10 March 2026",
        "witness_1_name": "Vijay Ramchandra Shinde",
        "witness_2_name": "Meena Ashok Desai",
    },
    "power_of_attorney": {
        "principal_name": "Chandrakant Govind Bhosale",
        "principal_address": "Plot No. 22, Sector 7, Vashi, Navi Mumbai - 400 703, Maharashtra",
        "attorney_name": "Sachin Chandrakant Bhosale",
        "attorney_address": "Flat No. 301, Sai Residency, Sector 7, Vashi, Navi Mumbai - 400 703, Maharashtra",
        "relationship": "Son",
        "poa_type": "Special Power of Attorney",
        "powers_granted": (
            "Sell property; Execute documents; Register documents; Receive payments"
        ),
        "specific_instructions": (
            "The Attorney is authorised to sell Survey No. 88/1, Village Panvel, "
            "Taluka Panvel, District Raigad for a consideration not less than "
            "₹1,20,00,000 and to execute and register all documents incidental thereto."
        ),
        "effective_date": "01 March 2026",
        "is_limited_duration": "Yes -- valid for 12 months from effective date",
        "expiry_date": "28 February 2027",
        "survey_number": "Survey No. 88/1",
        "village": "Panvel",
        "district": "Raigad",
        "witness_1_name": "Deepak Mohan Naik",
        "witness_2_name": "Rekha Sunil Gawde",
    },
    "leave_and_license": {
        "licensor_name": "Anita Rajendra Mehta",
        "licensor_address": "Flat No. 602, Oberoi Gardens, Kandivali East, Mumbai - 400 101, Maharashtra",
        "licensee_name": "Rohit Ashish Kapoor",
        "licensee_address": "C/o Kapoor Enterprises, 14 Nariman Point, Mumbai - 400 021, Maharashtra",
        "premises_address": (
            "Flat No. 401, 4th Floor, Oberoi Gardens, Thakur Village, "
            "Kandivali East, Mumbai - 400 101, Maharashtra"
        ),
        "premises_area_sqft": "950 sq. ft. (carpet area)",
        "survey_number": "CTS No. 2/A of Village Kandivali",
        "district": "Mumbai Suburban",
        "monthly_license_fee": "₹42,000 per month",
        "security_deposit": "₹2,52,000 (equivalent to 6 months' license fee)",
        "lock_in_period_months": "6 months",
        "commencement_date": "01 April 2026",
        "agreement_duration_months": "11 months",
        "notice_period_days": "30 days",
        "permitted_use": "Residential",
        "maintenance_responsibility": "Licensor (structural); Licensee (day-to-day)",
        "subletting_allowed": "No",
        "witness_1_name": "Harish Dilip Shah",
        "witness_2_name": "Kavita Nitin Jain",
    },
    "gift_deed": {
        "donor_name": "Vasudha Krishnarao Iyer",
        "donor_address": "Bungalow No. 5, Saraswati Colony, Nashik Road, Nashik - 422 101, Maharashtra",
        "donee_name": "Arjun Vasudha Iyer",
        "donee_address": "Flat No. 203, Lotus Heights, College Road, Nashik - 422 005, Maharashtra",
        "relationship": "Mother and Son",
        "survey_number": "Survey No. 34/2, Gat No. 210",
        "area_sqm": "405",
        "village": "Nashik Road",
        "taluka": "Nashik",
        "district": "Nashik",
        "is_conditional_gift": "No -- unconditional gift out of natural love and affection",
        "donee_acceptance": "Yes -- Donee has accepted the gift",
        "possession_delivery_date": "15 March 2026",
        "execution_date": "15 March 2026",
        "witness_1_name": "Suresh Balaji Kulkarni",
        "witness_2_name": "Lata Mohan Deshpande",
    },
    "conveyance_deed": {
        "developer_name": "Godrej Properties Limited",
        "developer_address": (
            "Godrej One, Pirojshanagar, Eastern Express Highway, "
            "Vikhroli East, Mumbai - 400 079, Maharashtra"
        ),
        "society_name": "Godrej Emerald Co-operative Housing Society Ltd.",
        "purchaser_name": "Nikhil Sanjay Wagh",
        "purchaser_address": "Flat No. 1204, Tower B, Godrej Emerald, Thane West - 400 610, Maharashtra",
        "flat_unit_number": "Flat No. 1204",
        "floor_number": "12th Floor",
        "building_name": "Tower B, Godrej Emerald",
        "carpet_area_sqft": "872 sq. ft.",
        "built_up_area_sqft": "1,105 sq. ft.",
        "common_area_share_sqft": "233 sq. ft.",
        "survey_number": "CTS No. 45/A, Village Majiwada",
        "village": "Majiwada",
        "district": "Thane",
        "maharera_number": "P51700025431",
        "oc_status": "OC Received",
        "oc_date": "12 December 2025",
        "consideration_amount": "₹1,25,00,000 (Rupees One Crore Twenty-Five Lakhs only)",
        "stamp_duty_paid": "₹7,50,000",
        "execution_date": "18 March 2026",
        "witness_1_name": "Pradeep Ramesh Tiwari",
        "witness_2_name": "Smita Arun Kulkarni",
    },
    "affidavit": {
        "deponent_name": "Mangesh Dattatray Sawant",
        "deponent_age": "42",
        "deponent_occupation": "Government Employee (Maharashtra State Electricity Board)",
        "deponent_address": "House No. 14, Shivaji Chowk, Ratnagiri - 415 612, Maharashtra",
        "affidavit_purpose": "Property Ownership",
        "authority_to_whom": "Sub-Registrar, Ratnagiri",
        "statement_1": (
            "I, Mangesh Dattatray Sawant, am the absolute and lawful owner of the "
            "agricultural land bearing Survey No. 67/1, admeasuring 1.20 Hectares, "
            "situated at Village Pawas, Taluka Ratnagiri, District Ratnagiri, Maharashtra."
        ),
        "statement_2": (
            "The said land was inherited by me from my late father Shri Dattatray "
            "Vishnu Sawant who passed away on 05 June 2018, and the mutation entry "
            "No. 1245 has been duly recorded in the Village Form 7/12 in my name."
        ),
        "statement_3": (
            "The said property is free from all encumbrances, mortgages, charges, "
            "liens, attachments, and claims of any nature whatsoever."
        ),
        "statement_4": (
            "No sale deed, gift deed, or any other instrument of transfer has been "
            "executed by me or my predecessors-in-title in respect of the said property "
            "except the present transaction."
        ),
        "statement_5": (
            "I make this affidavit for the purpose of registration of the Sale Deed "
            "in favour of the purchaser and to satisfy the Sub-Registrar as to my "
            "title and ownership of the said property."
        ),
        "notarisation_authority": "Notary Public",
        "place_of_execution": "Ratnagiri",
        "execution_date": "12 March 2026",
    },
}


# ---------------------------------------------------------------------------
# Stub LLM responses -- use exact intake values, realistic Maharashtra drafts
# ---------------------------------------------------------------------------

def _stub_llm(model_tag: str, prompt: str, timeout: int = 180) -> str:
    """
    Return a realistic stub draft that uses the exact intake values injected
    into the prompt. Detects doc type from the prompt and returns the
    matching stub.
    """
    p = prompt.lower()
    if "sale deed" in p:
        return _draft_sale_deed()
    if "mortgage deed" in p:
        return _draft_mortgage_deed()
    if "power of attorney" in p:
        return _draft_power_of_attorney()
    if "leave and license" in p:
        return _draft_leave_and_license()
    if "gift deed" in p:
        return _draft_gift_deed()
    if "conveyance deed" in p:
        return _draft_conveyance_deed()
    if "affidavit" in p:
        return _draft_affidavit()
    return _draft_sale_deed()


def _draft_sale_deed() -> str:
    d = INTAKE_DATA["sale_deed"]
    return f"""\
SALE DEED

THIS SALE DEED is executed at Pune on this 15th day of March 2026.

PARTIES

VENDOR (Seller):
{d['seller_name']}, aged about 54 years, Indian National, permanently residing at
{d['seller_address']},
hereinafter referred to as the "VENDOR" (which expression shall, unless repugnant to
the context or meaning thereof, include his heirs, executors, administrators, and assigns).

PURCHASER (Buyer):
{d['buyer_name']}, aged about 38 years, Indian National, permanently residing at
{d['buyer_address']},
hereinafter referred to as the "PURCHASER" (which expression shall, unless repugnant to
the context or meaning thereof, include her heirs, executors, administrators, and assigns).

RECITALS

WHEREAS the Vendor is the absolute and lawful owner of the immovable property more
particularly described in the Schedule hereunder written (hereinafter referred to as
"the said Property"), having acquired the same by virtue of a registered Sale Deed
dated 22 April 2008, registered as Document No. 4521/2008 with the Sub-Registrar,
Haveli No. 2, Pune.
This deed is governed by [Transfer of Property Act, 1882] Section 54, [Legislature], [1882].

WHEREAS the Vendor has agreed to sell and the Purchaser has agreed to purchase the
said Property for the total sale consideration of {d['consideration_amount']}, free
from all encumbrances, charges, liens, and claims of any nature whatsoever.

OPERATIVE CLAUSE 1 -- SALE CONSIDERATION AND PAYMENT

The total sale consideration agreed between the parties is {d['consideration_amount']}.
The Purchaser has paid the said consideration to the Vendor as follows:
(a) Token advance of {d['advance_paid']};
(b) Balance amount of ₹76,50,000 paid on the date of execution hereof by {d['payment_mode']}.
The Vendor hereby acknowledges receipt of the full and final sale consideration and
confirms that no amount is outstanding or payable by the Purchaser.
As per [Registration Act, 1908] Section 17, [Legislature], [1908], this deed requires
compulsory registration before the Sub-Registrar of Assurances.

OPERATIVE CLAUSE 2 -- TRANSFER OF TITLE AND POSSESSION

The Vendor hereby sells, conveys, transfers, and assures unto and in favour of the
Purchaser, the said Property together with all rights, easements, privileges, and
appurtenances thereunto belonging or appertaining, TO HAVE AND TO HOLD the same unto
and to the use of the Purchaser absolutely and forever.
The Vendor has on the date hereof delivered actual, physical, and vacant possession of
the said Property to the Purchaser.
As per [MLRC 1966] Section 32, [Legislature], [1966], the mutation entry shall be
recorded in the name of the Purchaser in the Village Form 7/12 and Property Card.

OPERATIVE CLAUSE 3 -- TITLE AND ENCUMBRANCE

The Vendor hereby covenants with the Purchaser that:
(a) The Vendor has good, clear, and marketable title to the said Property;
(b) The said Property is free from all encumbrances, mortgages, charges, liens,
    attachments, lis pendens, and claims of any nature whatsoever;
(c) The Vendor shall execute all further documents and do all acts as may be required
    to perfect the title of the Purchaser.

OPERATIVE CLAUSE 4 -- REGISTRATION

This deed shall be presented for registration at the office of the
{d['registration_office']}, as required under [Registration Act, 1908] Section 17,
[Legislature], [1908]. Stamp duty and registration charges have been paid as per the
Maharashtra Stamp Act, 1958.

SCHEDULE OF PROPERTY

All that piece and parcel of immovable property being:
Survey / CTS Number : {d['survey_number']}
Area                : {d['area_sqm']} sq. metres
Village / Locality  : {d['village']}
Taluka              : {d['taluka']}
District            : {d['district']}, Maharashtra
Bounded as follows  :
  North -- by Survey No. 47/1
  South -- by Survey No. 47/3
  East  -- by 12-metre wide road
  West  -- by Survey No. 46/2

IN WITNESS WHEREOF the parties hereto have set their respective hands to this deed
on the day, month, and year first above written.

SIGNED AND DELIVERED by the within-named VENDOR
{d['seller_name']}                    ___________________________
                                       (Signature of Vendor)

SIGNED AND DELIVERED by the within-named PURCHASER
{d['buyer_name']}                     ___________________________
                                       (Signature of Purchaser)

WITNESSES:
1. {d['witness_1_name']}              ___________________________
2. {d['witness_2_name']}              ___________________________
"""


def _draft_mortgage_deed() -> str:
    d = INTAKE_DATA["mortgage_deed"]
    return f"""\
SIMPLE MORTGAGE DEED

THIS SIMPLE MORTGAGE DEED is executed at Solapur on this 10th day of March 2026.

PARTIES

MORTGAGOR:
{d['mortgagor_name']}, aged about 45 years, Indian National, permanently residing at
{d['mortgagor_address']},
hereinafter referred to as the "MORTGAGOR".

MORTGAGEE:
{d['mortgagee_name']}, a body corporate constituted under the Banking Companies
(Acquisition and Transfer of Undertakings) Act, 1970, having its branch office at
{d['mortgagee_address']},
hereinafter referred to as the "MORTGAGEE".

RECITALS

WHEREAS the Mortgagor is the absolute owner of the agricultural land more particularly
described in the Schedule hereunder written (hereinafter referred to as "the Mortgaged
Property"), as evidenced by Village Form 7/12 and mutation entry in his name.
This deed is governed by [Transfer of Property Act, 1882] Section 58, [Legislature], [1882].

WHEREAS the Mortgagor has applied to the Mortgagee for a loan of {d['loan_amount']}
for agricultural development purposes, and the Mortgagee has agreed to advance the
said loan subject to the Mortgagor creating a simple mortgage over the Mortgaged Property.

OPERATIVE CLAUSE 1 -- LOAN AMOUNT AND DISBURSEMENT

The Mortgagee has agreed to advance and the Mortgagor has received the principal sum
of {d['loan_amount']} (the "Loan") on the date hereof, the receipt whereof the
Mortgagor hereby acknowledges.
As per [Registration Act, 1908] Section 17, [Legislature], [1908], this mortgage deed
requires compulsory registration.

OPERATIVE CLAUSE 2 -- INTEREST AND REPAYMENT

The Loan shall carry interest at the rate of {d['interest_rate']}.
The Loan together with interest shall be repaid by the Mortgagor in {d['repayment_period_months']}
by way of Equated Monthly Instalments (EMI) of {d['emi_amount']} each, commencing
from 01 May 2026.
As per [MLRC 1966] Section 32, [Legislature], [1966], the mortgage shall be noted in
the Village Form 7/12 and the Mortgagor shall not alienate the Mortgaged Property
without prior written consent of the Mortgagee.

OPERATIVE CLAUSE 3 -- NATURE OF MORTGAGE

This is a {d['mortgage_type']} as defined under [Transfer of Property Act, 1882]
Section 58(b), [Legislature], [1882]. {d['possession_transfer']}.
The Mortgagor binds himself personally to repay the Loan and in the event of default,
the Mortgagee shall have the right to cause the Mortgaged Property to be sold through
a decree of the competent court.

OPERATIVE CLAUSE 4 -- COVENANTS OF MORTGAGOR

The Mortgagor hereby covenants that:
(a) He shall pay all land revenue, cesses, and taxes in respect of the Mortgaged Property;
(b) He shall maintain the Mortgaged Property in good condition;
(c) He shall not create any further charge or encumbrance on the Mortgaged Property
    without prior written consent of the Mortgagee.

SCHEDULE OF MORTGAGED PROPERTY

Survey / Gat Number : {d['survey_number']}
Area                : {d['area_sqm']} sq. metres (agricultural land)
Village             : {d['village']}
Taluka              : {d['taluka']}
District            : {d['district']}, Maharashtra

IN WITNESS WHEREOF the parties have executed this deed on the date first above written.

MORTGAGOR: {d['mortgagor_name']}          ___________________________
MORTGAGEE: {d['mortgagee_name']}          ___________________________
           (Authorised Signatory)

WITNESSES:
1. {d['witness_1_name']}                  ___________________________
2. {d['witness_2_name']}                  ___________________________
"""


def _draft_power_of_attorney() -> str:
    d = INTAKE_DATA["power_of_attorney"]
    return f"""\
SPECIAL POWER OF ATTORNEY

THIS SPECIAL POWER OF ATTORNEY is executed at Navi Mumbai on this 01st day of March 2026.

PRINCIPAL (GRANTOR):
{d['principal_name']}, aged about 68 years, Indian National, permanently residing at
{d['principal_address']},
hereinafter referred to as the "PRINCIPAL".

ATTORNEY (AGENT):
{d['attorney_name']}, aged about 40 years, Indian National, permanently residing at
{d['attorney_address']},
{d['relationship']} of the Principal, hereinafter referred to as the "ATTORNEY".

RECITALS

WHEREAS the Principal is the absolute owner of the immovable property bearing
{d['survey_number']}, Village {d['village']}, District {d['district']}, Maharashtra.
This instrument is governed by [Transfer of Property Act, 1882] Section 54,
[Legislature], [1882].

WHEREAS the Principal, being unable to be personally present to attend to the sale
and registration of the said property, desires to appoint the Attorney to act on his
behalf for the specific purposes set out herein.

OPERATIVE CLAUSE 1 -- APPOINTMENT AND SCOPE

The Principal hereby appoints the Attorney as his true and lawful attorney to do and
execute the following acts, deeds, and things on behalf of the Principal:
{d['powers_granted']}.
As per [Registration Act, 1908] Section 17, [Legislature], [1908], all documents
executed by the Attorney shall be presented for registration.

OPERATIVE CLAUSE 2 -- SPECIFIC AUTHORITY

{d['specific_instructions']}
The Attorney shall have full power and authority to sign, execute, and register all
documents, receive sale proceeds, and give valid receipts and discharges therefor.
As per [MLRC 1966] Section 32, [Legislature], [1966], the Attorney shall ensure that
mutation entries are updated in the revenue records upon completion of the transaction.

OPERATIVE CLAUSE 3 -- DURATION AND REVOCATION

This Power of Attorney is effective from {d['effective_date']} and shall remain valid
until {d['expiry_date']}, unless earlier revoked by the Principal in writing.
{d['revocation_conditions'] if d.get('revocation_conditions') else ''}

OPERATIVE CLAUSE 4 -- RATIFICATION

The Principal hereby agrees to ratify and confirm all acts, deeds, and things lawfully
done by the Attorney in exercise of the powers hereby granted.

IN WITNESS WHEREOF the Principal has executed this Power of Attorney on the date
first above written.

PRINCIPAL: {d['principal_name']}          ___________________________

ACCEPTED BY ATTORNEY: {d['attorney_name']} ___________________________

WITNESSES:
1. {d['witness_1_name']}                  ___________________________
2. {d['witness_2_name']}                  ___________________________
"""


def _draft_leave_and_license() -> str:
    d = INTAKE_DATA["leave_and_license"]
    return f"""\
LEAVE AND LICENSE AGREEMENT

THIS LEAVE AND LICENSE AGREEMENT is executed at Mumbai on this 25th day of March 2026.

LICENSOR:
{d['licensor_name']}, Indian National, permanently residing at
{d['licensor_address']},
hereinafter referred to as the "LICENSOR".

LICENSEE:
{d['licensee_name']}, Indian National, permanently residing at
{d['licensee_address']},
hereinafter referred to as the "LICENSEE".

RECITALS

WHEREAS the Licensor is the absolute owner of the residential premises more
particularly described in the Schedule hereunder written (hereinafter referred to as
"the Licensed Premises").
This agreement is governed by [Maharashtra Rent Control Act, 1999] Section 24,
[Legislature], [1999].

WHEREAS the Licensor has agreed to grant and the Licensee has agreed to take on
leave and license basis the Licensed Premises for {d['permitted_use']} purposes only,
on the terms and conditions set out herein.

OPERATIVE CLAUSE 1 -- GRANT OF LICENSE

The Licensor hereby grants to the Licensee a bare license (and not a tenancy or lease)
to use and occupy the Licensed Premises for a period of {d['agreement_duration_months']}
commencing from {d['commencement_date']}.
As per [Registration Act, 1908] Section 17, [Legislature], [1908], this agreement
shall be compulsorily registered.

OPERATIVE CLAUSE 2 -- LICENSE FEE AND SECURITY DEPOSIT

The Licensee shall pay to the Licensor a monthly license fee of {d['monthly_license_fee']},
payable on or before the 5th day of each calendar month.
The Licensee has paid a refundable security deposit of {d['security_deposit']} to the
Licensor, the receipt whereof the Licensor hereby acknowledges.
As per [MLRC 1966] Section 32, [Legislature], [1966], the security deposit shall be
refunded within 30 days of vacation of the Licensed Premises.

OPERATIVE CLAUSE 3 -- LOCK-IN PERIOD AND TERMINATION

This agreement shall have a lock-in period of {d['lock_in_period_months']} from the
date of commencement. Either party may terminate this agreement after the lock-in
period by giving {d['notice_period_days']} written notice to the other party.

OPERATIVE CLAUSE 4 -- PERMITTED USE AND RESTRICTIONS

The Licensed Premises shall be used solely for {d['permitted_use']} purposes.
Subletting or sub-licensing: {d['subletting_allowed']}.
Maintenance responsibility: {d['maintenance_responsibility']}.

SCHEDULE OF LICENSED PREMISES

Premises Address : {d['premises_address']}
Carpet Area      : {d['premises_area_sqft']}
Survey / CTS No. : {d['survey_number']}
District         : {d['district']}, Maharashtra

IN WITNESS WHEREOF the parties have executed this agreement on the date first above written.

LICENSOR: {d['licensor_name']}            ___________________________
LICENSEE: {d['licensee_name']}            ___________________________

WITNESSES:
1. {d['witness_1_name']}                  ___________________________
2. {d['witness_2_name']}                  ___________________________
"""


def _draft_gift_deed() -> str:
    d = INTAKE_DATA["gift_deed"]
    return f"""\
GIFT DEED

THIS GIFT DEED is executed at Nashik on this 15th day of March 2026.

DONOR:
{d['donor_name']}, aged about 65 years, Indian National, permanently residing at
{d['donor_address']},
hereinafter referred to as the "DONOR".

DONEE:
{d['donee_name']}, aged about 38 years, Indian National, permanently residing at
{d['donee_address']},
{d['relationship']} of the Donor, hereinafter referred to as the "DONEE".

RECITALS

WHEREAS the Donor is the absolute and lawful owner of the immovable property more
particularly described in the Schedule hereunder written (hereinafter referred to as
"the Gifted Property"), having acquired the same by inheritance from her late husband.
This deed is governed by [Transfer of Property Act, 1882] Section 122, [Legislature], [1882].

WHEREAS the Donor, out of natural love and affection for the Donee, being her son,
desires to gift the Gifted Property to the Donee without any monetary consideration.

OPERATIVE CLAUSE 1 -- GIFT AND ACCEPTANCE

The Donor hereby gives, grants, and transfers by way of gift the Gifted Property to
the Donee absolutely and forever, free from all encumbrances.
{d['is_conditional_gift']}.
The Donee has accepted the gift: {d['donee_acceptance']}.
As per [Transfer of Property Act, 1882] Section 123, [Legislature], [1882], this gift
deed is required to be registered.
As per [Registration Act, 1908] Section 17, [Legislature], [1908], this deed requires
compulsory registration before the Sub-Registrar of Assurances.

OPERATIVE CLAUSE 2 -- DELIVERY OF POSSESSION

The Donor has delivered actual, physical, and vacant possession of the Gifted Property
to the Donee on {d['possession_delivery_date']}, and the Donee has accepted the same.
As per [MLRC 1966] Section 32, [Legislature], [1966], the mutation entry shall be
recorded in the name of the Donee in the Village Form 7/12.

OPERATIVE CLAUSE 3 -- TITLE AND ENCUMBRANCE

The Donor hereby covenants that the Gifted Property is free from all encumbrances,
mortgages, charges, liens, and claims of any nature whatsoever, and the Donor has
good and marketable title to gift the same.

SCHEDULE OF GIFTED PROPERTY

Survey / Gat Number : {d['survey_number']}
Area                : {d['area_sqm']} sq. metres
Village / Locality  : {d['village']}
Taluka              : {d['taluka']}
District            : {d['district']}, Maharashtra

IN WITNESS WHEREOF the parties have executed this deed on the date first above written.

DONOR: {d['donor_name']}                  ___________________________
DONEE: {d['donee_name']}                  ___________________________

WITNESSES:
1. {d['witness_1_name']}                  ___________________________
2. {d['witness_2_name']}                  ___________________________
"""


def _draft_conveyance_deed() -> str:
    d = INTAKE_DATA["conveyance_deed"]
    return f"""\
CONVEYANCE DEED

THIS CONVEYANCE DEED is executed at Thane on this 18th day of March 2026.

DEVELOPER / PROMOTER:
{d['developer_name']}, a company incorporated under the Companies Act, 2013,
having its registered office at {d['developer_address']},
MahaRERA Registration No. {d['maharera_number']},
hereinafter referred to as the "DEVELOPER".

PURCHASER:
{d['purchaser_name']}, Indian National, permanently residing at
{d['purchaser_address']},
hereinafter referred to as the "PURCHASER".

CO-OPERATIVE HOUSING SOCIETY:
{d['society_name']}, a co-operative housing society registered under the
Maharashtra Co-operative Societies Act, 1960,
hereinafter referred to as the "SOCIETY".

RECITALS

WHEREAS the Developer is the owner and developer of the residential project known as
"{d['building_name']}" situated on land bearing {d['survey_number']},
Village {d['village']}, District {d['district']}, Maharashtra.
This deed is governed by [Transfer of Property Act, 1882] Section 54, [Legislature], [1882].

WHEREAS the Developer has obtained Occupancy Certificate dated {d['oc_date']}
({d['oc_status']}) from the competent authority.

WHEREAS the Developer has agreed to convey the flat described in the Schedule to the
Purchaser for the total consideration of {d['consideration_amount']}.

OPERATIVE CLAUSE 1 -- CONVEYANCE

The Developer hereby conveys, transfers, and assures unto and in favour of the
Purchaser the flat more particularly described in the Schedule, together with the
proportionate undivided share in the common areas and facilities of the building.
As per [Registration Act, 1908] Section 17, [Legislature], [1908], this deed requires
compulsory registration. Stamp duty of {d['stamp_duty_paid']} has been paid.

OPERATIVE CLAUSE 2 -- MAHARERA COMPLIANCE

This conveyance is in compliance with the Real Estate (Regulation and Development)
Act, 2016 (MahaRERA), and the Developer's project is registered under MahaRERA
Registration No. {d['maharera_number']}.
As per [MLRC 1966] Section 32, [Legislature], [1966], the mutation entry shall be
recorded in the name of the Purchaser.

OPERATIVE CLAUSE 3 -- SOCIETY MEMBERSHIP

The Purchaser shall become a member of {d['society_name']} and shall be bound by
the bye-laws of the Society. The Developer shall execute the Deed of Conveyance of
the land and building in favour of the Society as required by law.

SCHEDULE OF PROPERTY

Flat / Unit No.     : {d['flat_unit_number']}
Floor               : {d['floor_number']}
Building / Wing     : {d['building_name']}
Carpet Area         : {d['carpet_area_sqft']}
Built-up Area       : {d['built_up_area_sqft']}
Common Area Share   : {d['common_area_share_sqft']}
Survey / CTS No.    : {d['survey_number']}
Village             : {d['village']}
District            : {d['district']}, Maharashtra

IN WITNESS WHEREOF the parties have executed this deed on the date first above written.

DEVELOPER: {d['developer_name']}          ___________________________
           (Authorised Signatory)
PURCHASER: {d['purchaser_name']}          ___________________________

WITNESSES:
1. {d['witness_1_name']}                  ___________________________
2. {d['witness_2_name']}                  ___________________________
"""


def _draft_affidavit() -> str:
    d = INTAKE_DATA["affidavit"]
    statements = "\n".join(
        f"{i}. {d[f'statement_{i}']}"
        for i in range(1, 6)
        if d.get(f"statement_{i}")
    )
    return f"""\
AFFIDAVIT

I, {d['deponent_name']}, aged {d['deponent_age']} years, {d['deponent_occupation']},
permanently residing at {d['deponent_address']},
do hereby solemnly affirm and state on oath as follows:

PURPOSE: {d['affidavit_purpose']}
Submitted to: {d['authority_to_whom']}

STATEMENTS:

{statements}

VERIFICATION

I, {d['deponent_name']}, the Deponent above-named, do hereby verify that the contents
of this affidavit are true and correct to the best of my knowledge and belief, and
nothing material has been concealed therefrom.
This affidavit is governed by [Indian Evidence Act, 1872] Section 3, [Legislature], [1872].
As per [Registration Act, 1908] Section 18, [Legislature], [1908], this affidavit may
be optionally registered.
As per [MLRC 1966] Section 32, [Legislature], [1966], the revenue records shall be
updated upon acceptance of this affidavit by the competent authority.

Verified at {d['place_of_execution']} on this {d['execution_date']}.

DEPONENT: {d['deponent_name']}            ___________________________

Sworn before me:
{d['notarisation_authority']}
{d['place_of_execution']}
Date: {d['execution_date']}               ___________________________
                                           (Signature & Seal)
"""


# ---------------------------------------------------------------------------
# Pipeline runner
# ---------------------------------------------------------------------------

def run_pipeline(doc_type: str, pdf_path: Path) -> dict:
    """Run the full CAG pipeline for one document type with guided intake data."""
    run_id = str(uuid.uuid4())
    file_bytes = pdf_path.read_bytes()
    errors: list[str] = []

    # ---- 1. OCR ----
    from src.ocr.pipeline import extract
    with tempfile.NamedTemporaryFile(suffix=pdf_path.suffix, delete=False) as tmp:
        tmp.write(file_bytes)
        tmp_path = tmp.name
    try:
        ocr_result = extract(tmp_path)
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass

    # ---- 2. PII Redaction ----
    from src.pii.redactor import redact
    raw_text = json.dumps(ocr_result.fields, ensure_ascii=False)
    redaction_result = redact(raw_text, ocr_result.fields)

    # ---- 3. CAG: load cache + generate draft (real Ollama call) ----
    # Build enriched fact pattern: OCR fields + guided intake answers
    from src.cag.engine import load_cache, generate_draft

    intake_answers = INTAKE_DATA[doc_type]
    fact_pattern = {
        "ocr_fields": ocr_result.fields,
        "intake": intake_answers,
        "document_type": doc_type,
    }

    cache = load_cache(doc_type, LLM_BACKEND)
    print(f"  -> Calling Groq llama-3.1-8b-instant (~2s)...", end=" ", flush=True)
    draft_result = generate_draft(fact_pattern, cache, doc_type)
    print("done.", flush=True)

    # ---- 4. Document Generator ----
    from src.generation.document_generator import generate_document
    generated = generate_document(
        llm_output=draft_result.content,
        template=None,
        cache_or_chunks=cache,
        doc_type=doc_type,
        pipeline_variant="CAG",
        llm_model=LLM_BACKEND,
        run_id=run_id,
        cache_version=f"session:{cache.session_id[:8]}",
    )

    # ---- 5. Export to output/cag/{doc_type}/{run_id}/ ----
    from src.generation.exporter import export_document
    persistent_dir = OUTPUT_ROOT / "cag" / doc_type / run_id
    persistent_dir.mkdir(parents=True, exist_ok=True)
    export_result = export_document(generated, str(persistent_dir), run_id)
    errors.extend(export_result.errors)

    # ---- 6. Run Logger ----
    import src.logging.run_logger as rl
    cache_comp = [{"name": d.name, "tokens": d.tokens} for d in cache.documents]
    docx_bytes = (
        Path(export_result.docx_path).read_bytes()
        if export_result.docx_path and Path(export_result.docx_path).exists()
        else b""
    )
    pdf_bytes = (
        Path(export_result.pdf_path).read_bytes()
        if export_result.pdf_path and Path(export_result.pdf_path).exists()
        else b""
    )
    output_hash = hashlib.sha256(docx_bytes or pdf_bytes).hexdigest()

    logged_id = rl.log_run(
        pipeline_variant="cag",
        llm_model=LLM_BACKEND,
        input_hash=hashlib.sha256(file_bytes).hexdigest(),
        cache_composition=cache_comp,
        output_hash=output_hash,
        output_path=str(persistent_dir),
        status="success" if not errors else "partial",
    )

    return {
        "doc_type": doc_type,
        "run_id": run_id,
        "logged_id": logged_id,
        "docx_path": export_result.docx_path,
        "pdf_path": export_result.pdf_path,
        "docx_size": (
            Path(export_result.docx_path).stat().st_size
            if export_result.docx_path and Path(export_result.docx_path).exists()
            else 0
        ),
        "pdf_size": (
            Path(export_result.pdf_path).stat().st_size
            if export_result.pdf_path and Path(export_result.pdf_path).exists()
            else 0
        ),
        "citations": len(generated.citations),
        "ungrounded": len(generated.ungrounded_clauses),
        "cache_docs": len(cache.documents),
        "cache_tokens": cache.total_tokens,
        "errors": errors,
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

DOC_TYPES = [
    "sale_deed",
    "mortgage_deed",
    "power_of_attorney",
    "leave_and_license",
    "gift_deed",
    "conveyance_deed",
    "affidavit",
]


def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(
        description="Phase 1 end-to-end workflow runthrough"
    )
    parser.add_argument(
        "--doc-type",
        choices=DOC_TYPES + ["all"],
        default="all",
        help="Run a single document type or all (default: all)",
    )
    args = parser.parse_args()

    run_types = DOC_TYPES if args.doc_type == "all" else [args.doc_type]

    print("=" * 72)
    print("  Maharashtra Legal Document Generation -- Phase 1 Workflow Runthrough")
    print("=" * 72)
    print(f"  Input PDF  : {SAMPLE_PDF}")
    print(f"  Output dir : {OUTPUT_ROOT / 'cag'}")
    print(f"  LLM backend: {LLM_BACKEND} -> llama-3.1-8b-instant via Groq")
    print(f"  Doc types  : {len(run_types)}")
    print("=" * 72)

    if not SAMPLE_PDF.exists():
        print(f"\nERROR: Sample PDF not found: {SAMPLE_PDF}")
        print("    Ensure 'Maharashtra Legal Document Dataset/Mortage and land deed/' exists.")
        sys.exit(1)

    results = []
    for i, doc_type in enumerate(run_types, 1):
        print(f"\n[{i}/{len(run_types)}] {doc_type} ...", end=" ", flush=True)
        t0 = time.time()
        try:
            result = run_pipeline(doc_type, SAMPLE_PDF)
            elapsed = time.time() - t0
            result["elapsed"] = elapsed
            result["status"] = "OK" if not result["errors"] else "WARN"
            results.append(result)
            print(f"{result['status']}  ({elapsed:.1f}s)")
        except Exception as exc:
            elapsed = time.time() - t0
            results.append({
                "doc_type": doc_type,
                "status": "FAILED",
                "elapsed": elapsed,
                "errors": [str(exc)],
                "docx_size": 0,
                "pdf_size": 0,
                "citations": 0,
                "ungrounded": 0,
                "cache_docs": 0,
                "cache_tokens": 0,
                "run_id": "-",
            })
            print(f"FAILED  ({elapsed:.1f}s)")
            print(f"    Error: {exc}")

    # ---- Summary table ----
    print("\n" + "=" * 72)
    print("  SUMMARY")
    print("=" * 72)
    col = "{:<22} {:<6} {:>10} {:>10} {:>9} {:>9} {:>7}"
    print(col.format("Document Type", "Status", "DOCX (KB)", "PDF (KB)",
                     "Citations", "Ungrounded", "Time(s)"))
    print("-" * 72)
    for r in results:
        print(col.format(
            r["doc_type"],
            r["status"],
            f"{r['docx_size'] / 1024:.1f}" if r["docx_size"] else "--",
            f"{r['pdf_size'] / 1024:.1f}" if r["pdf_size"] else "--",
            str(r["citations"]),
            str(r["ungrounded"]),
            f"{r['elapsed']:.1f}",
        ))
    print("-" * 72)

    passed = sum(1 for r in results if r["status"] == "OK")
    warned = sum(1 for r in results if r["status"] == "WARN")
    failed = sum(1 for r in results if r["status"] == "FAILED")
    print(f"\n  {passed} passed  |  {warned} warnings  |  {failed} failed"
          f"  out of {len(run_types)} document types")
    print(f"  Run logs : {OUTPUT_ROOT / 'runs.db'}")
    print(f"  Files    : {OUTPUT_ROOT / 'cag'}/")
    print()

    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
