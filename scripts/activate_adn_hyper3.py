#!/usr/bin/env python3
"""
Seed org_adn + adn_hierarchy for hyper3 org.
Run AFTER 002_adn_vms.sql has been applied in Supabase SQL editor.
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from dotenv import load_dotenv
load_dotenv()

from supabase import create_client

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_SERVICE_KEY = os.environ["SUPABASE_SERVICE_KEY"]
HYPER3_ORG_ID = "c2eeb6c9-dd3f-4174-936f-3e2120ff33c6"

db = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)

# ── 1. Get all ADNs ───────────────────────────────────────────────────────────
adns = db.table("adn_catalog").select("id, slug, name").order("sort_order").execute()
if not adns.data:
    print("ERROR: adn_catalog is empty. Run 002_adn_vms.sql in Supabase SQL editor first.")
    sys.exit(1)

print(f"Found {len(adns.data)} ADNs:")
for a in adns.data:
    print(f"  {a['slug']:15} {a['name']}")

slug_id = {a["slug"]: a["id"] for a in adns.data}

# ── 2. Activate all ADNs for hyper3 ──────────────────────────────────────────
print(f"\nActivating all ADNs for org {HYPER3_ORG_ID} ...")
records = [
    {
        "org_id": HYPER3_ORG_ID,
        "adn_id": a["id"],
        "status": "active",
        "notes": "Premium — hyper3.network@gmail.com — all ADNs free",
    }
    for a in adns.data
]
result = db.table("org_adn").upsert(records, on_conflict="org_id,adn_id").execute()
print(f"  Upserted {len(result.data)} org_adn records.")

# ── 3. Default hierarchy ───────────────────────────────────────────────────────
edges = [
    ("mba", "cmo"), ("mba", "cfo"), ("mba", "coo"), ("mba", "abogado"),
    ("cmo", "redactor"),
    ("cfo", "contador"),
    ("coo", "ingeniero"),
]
hierarchy_records = [
    {"org_id": HYPER3_ORG_ID, "manager_adn_id": slug_id[m], "report_adn_id": slug_id[r]}
    for m, r in edges if m in slug_id and r in slug_id
]
h = db.table("adn_hierarchy").upsert(hierarchy_records, on_conflict="org_id,manager_adn_id,report_adn_id").execute()
print(f"  Upserted {len(h.data)} hierarchy edges.")
print("\nDone. hyper3 org has all ADNs active and hierarchy configured.")
