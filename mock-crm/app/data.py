"""A fixed, deterministic (no random seed drift) dataset of 25 messy
customer records, using the same abbreviated 'legacy CRM' header style
backend/app/canonical.py already has aliases for (custid, compname,
cntry, dt_create -- see backend/docs/PROGRESS.md's Day 3 notes on the
seed generator's legacy headers) -- so a batch pulled from this API
exercises the identical deterministic-mapping path a legacy-header file
upload does.
"""

_RAW_CUSTOMERS: list[tuple[str, str, str, str, str, str, str]] = [
    ("Whitfield & Sons", "contact@whitfield.example", "+1-202-555-0101", "US", "Manufacturing", "medium", "2024-03-11"),
    ("Nordkap Logistics AB", "info@nordkap.example", "+46-8-555-0102", "SE", "Logistics", "low", "2024-01-22"),
    ("Rivera Consulting Group", "hello@riveraconsulting.example", "+1-415-555-0103", "US", "Consulting", "high", "2023-11-02"),
    ("Blackwood Textiles", "sales@blackwoodtextiles.example", "+44-20-555-0104", "GB", "Retail", "medium", "2024-06-19"),
    ("Meridian Health Partners", "admin@meridianhealth.example", "+1-312-555-0105", "US", "Healthcare", "high", "2023-09-14"),
    ("Kestrel Robotics", "team@kestrelrobotics.example", "+49-30-555-0106", "DE", "Manufacturing", "low", "2024-02-28"),
    ("Solaris Energy Corp", "contact@solarisenergy.example", "+1-713-555-0107", "US", "Energy", "medium", "2024-04-07"),
    # Deliberate near-duplicate of row 1: same email/phone/country, name
    # spelled differently, one day apart -- gives the Splink fuzzy-dedupe
    # pass (Day 6) a real positive case, mirroring the file-upload seed
    # data's own DUPLICATE_FUZZY row.
    ("Whitfield and Sons Ltd", "contact@whitfield.example", "+1-202-555-0101", "US", "Manufacturing", "medium", "2024-03-12"),
    ("Ashcombe Financial", "info@ashcombefinancial.example", "+44-161-555-0109", "GB", "Finance", "high", "2023-12-30"),
    ("Delacroix Freight", "ops@delacroixfreight.example", "+33-1-555-0110", "FR", "Logistics", "medium", "2024-05-16"),
    ("Yorkshire Dairy Co", "sales@yorkshiredairy.example", "+44-113-555-0111", "GB", "Retail", "low", "2024-01-09"),
    ("Cobalt Analytics", "hello@cobaltanalytics.example", "+1-650-555-0112", "US", "Technology", "medium", "2024-07-01"),
    ("Marbach Industrieteile", "kontakt@marbach.example", "+49-711-555-0113", "DE", "Manufacturing", "high", "2023-10-21"),
    ("Sundown Hospitality Group", "info@sundownhospitality.example", "+1-305-555-0114", "US", "Hospitality", "low", "2024-03-30"),
    ("Nordstrand Fisheries", "office@nordstrandfisheries.example", "+47-22-555-0115", "NO", "Agriculture", "medium", "2024-02-14"),
    ("Ironvale Steelworks", "sales@ironvalesteel.example", "+1-412-555-0116", "US", "Manufacturing", "high", "2023-08-19"),
    ("Cassia Wellness Clinics", "contact@cassiawellness.example", "+61-2-555-0117", "AU", "Healthcare", "medium", "2024-04-22"),
    ("Halden Maritime Services", "info@haldenmaritime.example", "+47-33-555-0118", "NO", "Logistics", "low", "2024-06-03"),
    ("Prairie Grain Traders", "trade@prairiegrain.example", "+1-306-555-0119", "CA", "Agriculture", "medium", "2023-12-05"),
    ("Vantage Point Insurance", "support@vantagepoint.example", "+1-617-555-0120", "US", "Finance", "high", "2024-01-27"),
    ("Elmsworth Timber Co", "office@elmsworthtimber.example", "+44-131-555-0121", "GB", "Manufacturing", "low", "2024-05-08"),
    ("Bright Harbor Software", "hello@brightharbor.example", "+1-206-555-0122", "US", "Technology", "medium", "2024-03-25"),
    ("Alto Verde Vineyards", "info@altoverde.example", "+56-2-555-0123", "CL", "Agriculture", "low", "2023-11-18"),
    ("Sentry Risk Advisors", "contact@sentryrisk.example", "+1-212-555-0124", "US", "Finance", "high", "2024-02-09"),
    ("Loch Fyne Renewables", "info@lochfynerenewables.example", "+44-141-555-0125", "GB", "Energy", "medium", "2024-06-27"),
]

CUSTOMERS: list[dict] = [
    {
        "CustID": f"MCRM-{1000 + i}",
        "CompName": name,
        "Email": email,
        "Phone": phone,
        "Cntry": country,
        "Ind": industry,
        "RiskTier": risk,
        "DT_Create": created,
    }
    for i, (name, email, phone, country, industry, risk, created) in enumerate(_RAW_CUSTOMERS)
]
