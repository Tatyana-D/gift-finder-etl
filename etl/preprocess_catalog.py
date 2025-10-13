import os, json, math, sys
import pandas as pd
from pathlib import Path
from slugify import slugify

CSV_URL = os.environ.get("CSV_URL")
ASSETS_PREFIX = os.environ.get("ASSETS_PREFIX", "").strip().strip("/")
OUT_DIR = Path("./out")
OUT_DIR.mkdir(parents=True, exist_ok=True)

PERSONAS_PATH = Path(__file__).parent / "personas.json"
with open(PERSONAS_PATH, "r", encoding="utf-8") as f:
    PERSONA_RULES = json.load(f)

EXPECTED_COLS = {
    "Handle","Title","Body (HTML)","Vendor","Tags",
    "Variant ID","Variant SKU","Variant Price","Variant Inventory Qty",
    "Image Src","Status","Option1 Value","Option2 Value","Option3 Value"
}

BUCKETS = [
    (0,30,"under_30"),
    (30,60,"30_to_60"),
    (60,120,"60_to_120"),
    (120,math.inf,"over_120"),
]

def bucket(p):
    for lo,hi,k in BUCKETS:
        if lo <= p < hi: return k
    return "unknown"

def norm_tags(s):
    if not s: return []
    return [t.strip().lower() for t in s.split(",") if t.strip()]

def ffloat(x):
    try: return float(x)
    except: return 0.0

def fint(x):
    try: return int(float(x))
    except: return 0

def map_personas(text, tags):
    hay = (text or "").lower() + " " + " ".join(tags or [])
    res = set()
    for persona,kws in PERSONA_RULES.items():
        if any(kw.lower() in hay for kw in kws):
            res.add(persona)
    return sorted(res)

def main():
    if not CSV_URL:
        print("Missing CSV_URL", file=sys.stderr)
        sys.exit(1)

    catalog = []
    by_tag, by_persona, by_bucket = {}, {}, {}
    for chunk in pd.read_csv(CSV_URL, chunksize=50000, dtype=str, keep_default_na=False):
        for c in EXPECTED_COLS:
            if c not in chunk.columns: chunk[c] = ""

        chunk["Variant Price"] = chunk["Variant Price"].apply(ffloat)
        chunk["Variant Inventory Qty"] = chunk["Variant Inventory Qty"].apply(fint)
        chunk["Tags"] = chunk["Tags"].apply(norm_tags)

        for handle, rows in chunk.groupby("Handle"):
            rows = rows.copy().sort_values(by=["Variant Price"])
            first = rows.iloc[0]
            product_id = slugify(handle) or handle
            title = first["Title"]; vendor = first["Vendor"]
            desc = first["Body (HTML)"]; primary_image = first.get("Image Src","")
            tags = sorted(set(t for lst in rows["Tags"].tolist() for t in lst))

            variants = []
            min_price = 9e9
            for _, r in rows.iterrows():
                price = ffloat(r["Variant Price"])
                inv = fint(r["Variant Inventory Qty"])
                if not (price > 0 and inv > 0): continue
                vid = r.get("Variant ID") or r.get("Variant SKU") or ""
                opt = " / ".join([r.get("Option1 Value",""), r.get("Option2 Value",""), r.get("Option3 Value","")]).strip(" /")
                variants.append({
                    "id": int(vid) if vid.isdigit() else vid,
                    "title": opt or r.get("Variant SKU","") or "Default",
                    "price": round(price,2),
                    "inventory": inv
                })
                if price < min_price: min_price = price

            if not variants: continue
            if min_price == 9e9: min_price = variants[0]["price"]

            personas = map_personas(f"{title} {desc}", tags)
            p = {
                "product_id": product_id,
                "handle": handle,
                "title": title,
                "vendor": vendor,
                "tags": tags,
                "persona": personas,
                "price": round(min_price,2),
                "currency": "EUR",
                "primary_image": primary_image,
                "variants": variants
            }
            catalog.append(p)

            for t in tags: by_tag.setdefault(t, []).append(product_id)
            for pe in personas: by_persona.setdefault(pe, []).append(product_id)
            by_bucket.setdefault(bucket(min_price), []).append(product_id)

    out_catalog = OUT_DIR/"catalog.min.json"
    out_index = OUT_DIR/"index.min.json"
    with open(out_catalog,"w",encoding="utf-8") as f:
        json.dump(catalog,f,ensure_ascii=False,separators=(",",":"))
    with open(out_index,"w",encoding="utf-8") as f:
        json.dump({"by_tag":by_tag,"by_persona":by_persona,"by_price_bucket":by_bucket},f,ensure_ascii=False,separators=(",",":"))

    print(str(out_catalog))
    print(str(out_index))

if __name__ == "__main__":
    main()
