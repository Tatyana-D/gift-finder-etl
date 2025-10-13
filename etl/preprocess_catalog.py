# etl/preprocess_catalog.py
import os, json, math, sys
import pandas as pd
from pathlib import Path
from slugify import slugify

CSV_URL = os.environ.get("CSV_URL")
ASSETS_PREFIX = os.environ.get("ASSETS_PREFIX", "").strip().strip("/")
INCLUDE_OOS = os.environ.get("INCLUDE_OOS", "0") == "1"
OUT_DIR = Path("./out")
OUT_DIR.mkdir(parents=True, exist_ok=True)

PERSONAS_PATH = Path(__file__).parent / "personas.json"
with open(PERSONAS_PATH, "r", encoding="utf-8") as f:
    PERSONA_RULES = json.load(f)

# имена колонок под твой файл
COL_HANDLE = "Handle"
COL_TITLE = "Title"
COL_VENDOR = "Vendor"
COL_TAGS = "Tags"
COL_DESC = "Body HTML"              # <- важно
COL_IMG = "Image Src"
COL_PRICE = "Variant Price"
COL_INV_VAR = "Variant Inventory Qty"
COL_INV_TOTAL = "Total Inventory Qty"
COL_VID = "Variant ID"
COL_VSKU = "Variant SKU"
COL_OPT1N, COL_OPT1V = "Option1 Name", "Option1 Value"
COL_OPT2N, COL_OPT2V = "Option2 Name", "Option2 Value"
COL_OPT3N, COL_OPT3V = "Option3 Name", "Option3 Value"

BUCKETS = [
    (0, 30, "under_30"),
    (30, 60, "30_to_60"),
    (60, 120, "60_to_120"),
    (120, math.inf, "over_120"),
]

def bucket(p):
    for lo, hi, k in BUCKETS:
        if lo <= p < hi:
            return k
    return "unknown"

def norm_tags(s):
    if not s:
        return []
    return [t.strip().lower() for t in s.split(",") if t.strip()]

def ffloat(x):
    try:
        if str(x).strip() == "": return 0.0
        return float(str(x).replace(",", "."))
    except:
        return 0.0

def fint(x):
    try:
        if str(x).strip() == "": return 0
        return int(float(str(x).replace(",", ".")))
    except:
        return 0

def map_personas(text, tags):
    hay = (text or "").lower() + " " + " ".join(tags or [])
    res = set()
    for persona, kws in PERSONA_RULES.items():
        if any(kw.lower() in hay for kw in kws):
            res.add(persona)
    return sorted(res)

def main():
    if not CSV_URL:
        print("Missing CSV_URL", file=sys.stderr)
        sys.exit(1)

    catalog = []
    by_tag, by_persona, by_bucket = {}, {}, {}
    total_rows = total_products = kept_products = kept_variants = 0

    usecols = None  # читаем все, чтобы не потерять поля
    for chunk in pd.read_csv(CSV_URL, chunksize=50000, dtype=str, keep_default_na=False, usecols=usecols):
        total_rows += len(chunk)

        # гарантируем существование нужных столбцов
        for c in [COL_HANDLE, COL_TITLE, COL_VENDOR, COL_TAGS, COL_DESC, COL_IMG,
                  COL_VID, COL_VSKU, COL_OPT1N, COL_OPT1V, COL_OPT2N, COL_OPT2V, COL_OPT3N, COL_OPT3V,
                  COL_PRICE, COL_INV_VAR, COL_INV_TOTAL]:
            if c not in chunk.columns:
                chunk[c] = ""

        # приведения типов
        chunk[COL_PRICE] = chunk[COL_PRICE].apply(ffloat)
        chunk[COL_INV_VAR] = chunk[COL_INV_VAR].apply(fint)
        chunk[COL_INV_TOTAL] = chunk[COL_INV_TOTAL].apply(fint)
        chunk[COL_TAGS] = chunk[COL_TAGS].apply(norm_tags)

        total_products += chunk[COL_HANDLE].nunique()

        for handle, rows in chunk.groupby(COL_HANDLE):
            rows = rows.copy().sort_values(by=[COL_PRICE])
            first = rows.iloc[0]
            product_id = slugify(handle) or handle
            title = first[COL_TITLE]
            vendor = first[COL_VENDOR]
            desc = first[COL_DESC]
            primary_image = first.get(COL_IMG, "")
            tags = sorted(set(t for lst in rows[COL_TAGS].tolist() for t in lst))

            variants = []
            min_price = 9e9

            for _, r in rows.iterrows():
                price = ffloat(r[COL_PRICE])
                inv_var = fint(r[COL_INV_VAR])
                inv_total = fint(r[COL_INV_TOTAL])
                inv = inv_var if inv_var else inv_total  # берём вариант, иначе общий остаток

                # доступность: есть цена > 0 и (inv > 0 или INCLUDE_OOS)
                if not (price > 0 and (inv > 0 or INCLUDE_OOS)):
                    continue

                vid = r.get(COL_VID) or r.get(COL_VSKU) or ""
                opt = " / ".join([
                    r.get(COL_OPT1V, ""),
                    r.get(COL_OPT2V, ""),
                    r.get(COL_OPT3V, "")
                ]).strip(" /")

                variants.append({
                    "id": int(vid) if str(vid).isdigit() else vid,
                    "title": opt or r.get(COL_VSKU, "") or "Default",
                    "price": round(price, 2),
                    "inventory": inv
                })
                kept_variants += 1
                if price < min_price:
                    min_price = price

            if not variants:
                continue

            if min_price == 9e9 and variants:
                min_price = variants[0]["price"]

            personas = map_personas(f"{title} {desc}", tags)
            p = {
                "product_id": product_id,
                "handle": handle,
                "title": title,
                "vendor": vendor,
                "tags": tags,
                "persona": personas,
                "price": round(min_price, 2),
                "currency": "EUR",
                "primary_image": primary_image,
                "variants": variants
            }
            catalog.append(p)
            kept_products += 1

            for t in tags: by_tag.setdefault(t, []).append(product_id)
            for pe in personas: by_persona.setdefault(pe, []).append(product_id)
            by_bucket.setdefault(bucket(min_price), []).append(product_id)

    # запись JSON
    out_catalog = OUT_DIR / "catalog.min.json"
    out_index = OUT_DIR / "index.min.json"
    with open(out_catalog, "w", encoding="utf-8") as f:
        json.dump(catalog, f, ensure_ascii=False, separators=(",", ":"))
    with open(out_index, "w", encoding="utf-8") as f:
        json.dump({"by_tag": by_tag, "by_persona": by_persona, "by_price_bucket": by_bucket}, f, ensure_ascii=False, separators=(",", ":"))

    # пути для workflow + краткая сводка
    print(str(out_catalog))
    print(str(out_index))
    print(f"SUMMARY rows={total_rows} products_in_csv={total_products} kept_products={kept_products} kept_variants={kept_variants}", file=sys.stderr)

if __name__ == "__main__":
    main()
