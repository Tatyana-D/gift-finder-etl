import os, json, math, sys, re
import pandas as pd
from pathlib import Path
from slugify import slugify

CSV_URL = os.environ.get("CSV_URL")
ASSETS_PREFIX = os.environ.get("ASSETS_PREFIX", "").strip().strip("/")
INCLUDE_OOS = os.environ.get("INCLUDE_OOS", "0") == "1"
ALLOW_ZERO_PRICE = os.environ.get("ALLOW_ZERO_PRICE", "0") == "1"
OUT_DIR = Path("./out")
OUT_DIR.mkdir(parents=True, exist_ok=True)

# ---- CSV column names (Shopify export) ----
COL_HANDLE   = "Handle"
COL_TITLE    = "Title"
COL_VENDOR   = "Vendor"
COL_TAGS     = "Tags"
COL_DESC     = "Body HTML"
COL_IMG      = "Image Src"
COL_TYPE     = "Type"
COL_STATUS   = "Status"
COL_PUBLISHED= "Published"

PRICE_CANDIDATES = ["Variant Price", "Variant Compare At Price", "Price"]

COL_INV_VAR   = "Variant Inventory Qty"
COL_INV_TOTAL = "Total Inventory Qty"
COL_INV_POLICY= "Variant Inventory Policy"

COL_VID   = "Variant ID"
COL_VSKU  = "Variant SKU"
COL_OPT1V = "Option1 Value"
COL_OPT2V = "Option2 Value"
COL_OPT3V = "Option3 Value"

BUCKETS = [(0,30,"under_30"), (30,60,"30_to_60"), (60,120,"60_to_120"), (120,math.inf,"over_120")]

def bucket(p):
    for lo,hi,k in BUCKETS:
        if lo <= p < hi: return k
    return "unknown"

def norm_tags(s):
    if not s: return []
    return [t.strip().lower() for t in s.split(",") if t.strip()]

_money_re = re.compile(r"[^\d\.,-]")
def ffloat_any(x):
    if x is None: return 0.0
    s = str(x).strip()
    if s == "": return 0.0
    s = _money_re.sub("", s)
    if "," in s and "." in s:
        if s.rfind(",") > s.rfind("."):
            s = s.replace(".", "").replace(",", ".")
        else:
            s = s.replace(",", "")
    else:
        s = s.replace(",", ".")
    try:
        return float(s)
    except:
        return 0.0

def fint(x):
    try:
        s = str(x).strip()
        if s == "": return 0
        s = s.replace(",", ".")
        return int(float(s))
    except:
        return 0

# --- sniff Shopify CSV (Products header + ';' delimiter) ---
def _read_head_bytes(path_or_url, n=4096):
    if os.path.isfile(path_or_url):
        with open(path_or_url, "rb") as f:
            return f.read(n)
    try:
        import urllib.request
        with urllib.request.urlopen(path_or_url) as r:
            return r.read(n)
    except Exception:
        return b""

def guess_csv_params(path_or_url):
    head = _read_head_bytes(path_or_url).decode("utf-8", errors="ignore")
    lines = head.splitlines()
    sep = None
    skiprows = 0
    if lines and lines[0].strip().lower().startswith("products"):
        skiprows = 1
    header_line = lines[skiprows] if len(lines) > skiprows else ""
    if ";" in header_line and "," not in header_line:
        sep = ";"
    else:
        sep = None
    return {"sep": sep, "skiprows": skiprows}

def iter_frames(url):
    params = guess_csv_params(url)
    try:
        for chunk in pd.read_csv(
            url,
            chunksize=50000,
            dtype=str,
            keep_default_na=False,
            sep=params["sep"],
            skiprows=params["skiprows"],
            engine="python",
            on_bad_lines="skip"
        ):
            yield chunk
        return
    except Exception as e:
        print(f"[info] read_csv failed, trying Excel: {e}", file=sys.stderr)
    try:
        xls = pd.read_excel(url, dtype=str)
        yield xls
        return
    except Exception as e:
        print(f"[error] read_excel failed: {e}", file=sys.stderr)
        return

def pick_price(row):
    for col in PRICE_CANDIDATES:
        if col in row and str(row[col]).strip() != "":
            val = ffloat_any(row[col])
            if val > 0: return val
    return ffloat_any(row.get(PRICE_CANDIDATES[0], 0))

def clean_html(s, maxlen=500):
    if not s: return ""
    # грубая чистка html-тегов
    txt = re.sub(r"<[^>]+>", " ", str(s))
    txt = re.sub(r"\s+", " ", txt).strip()
    return (txt[:maxlen] + "…") if len(txt) > maxlen else txt

def main():
    if not CSV_URL:
        print("Missing CSV_URL", file=sys.stderr)
        sys.exit(1)

    catalog = []
    by_tag, by_bucket = {}, {}
    total_rows = total_products = kept_products = kept_variants = 0
    any_rows = False

    # для файла для AI
    ai_rows = []

    for chunk in iter_frames(CSV_URL):
        any_rows = True
        total_rows += len(chunk)

        must_cols = [
            COL_HANDLE, COL_TITLE, COL_VENDOR, COL_TAGS, COL_DESC, COL_IMG,
            COL_TYPE, COL_STATUS, COL_PUBLISHED,
            COL_VID, COL_VSKU, COL_OPT1V, COL_OPT2V, COL_OPT3V,
            COL_INV_VAR, COL_INV_TOTAL, COL_INV_POLICY
        ] + PRICE_CANDIDATES

        for c in must_cols:
            if c not in chunk.columns:
                chunk[c] = ""

        chunk[COL_TAGS] = chunk[COL_TAGS].apply(norm_tags)
        chunk[COL_INV_VAR] = chunk[COL_INV_VAR].apply(fint)
        chunk[COL_INV_TOTAL] = chunk[COL_INV_TOTAL].apply(fint)

        total_products += chunk[COL_HANDLE].nunique()

        for handle, rows in chunk.groupby(COL_HANDLE):
            rows = rows.copy()
            rows["_calc_price"] = rows.apply(pick_price, axis=1)
            rows = rows.sort_values(by=["_calc_price"])

            first = rows.iloc[0]
            product_id = slugify(handle) or handle
            title = first[COL_TITLE]
            vendor = first[COL_VENDOR]
            desc = first[COL_DESC]
            primary_image = first.get(COL_IMG, "")
            tags = sorted(set(t for lst in rows[COL_TAGS].tolist() for t in lst))
            ptype = first.get(COL_TYPE, "")
            status = (first.get(COL_STATUS, "") or "").lower()
            published = str(first.get(COL_PUBLISHED, "")).strip().lower() in ("true","1","yes")

            variants = []
            min_price = 9e9

            # вычислим суммарный/макс остаток для продукта
            total_inv_seen = 0
            max_inv_seen = 0

            for _, r in rows.iterrows():
                price = float(r["_calc_price"])
                policy = (str(r.get(COL_INV_POLICY, "")).strip().lower())
                inv_var = fint(r.get(COL_INV_VAR))
                inv_tot = fint(r.get(COL_INV_TOTAL))
                inv = inv_var if inv_var != 0 else inv_tot

                total_inv_seen += max(inv, 0)
                if inv > max_inv_seen: max_inv_seen = inv

                price_ok = (price > 0) or ALLOW_ZERO_PRICE
                inv_ok = (inv > 0) or (inv == -1) or (policy == "continue") or INCLUDE_OOS
                if not (price_ok and inv_ok):
                    continue

                vid = r.get(COL_VID) or r.get(COL_VSKU) or ""
                opt = " / ".join([r.get(COL_OPT1V, ""), r.get(COL_OPT2V, ""), r.get(COL_OPT3V, "")]).strip(" /")

                variants.append({
                    "id": int(vid) if str(vid).isdigit() else vid,
                    "title": opt or r.get(COL_VSKU, "") or "Default",
                    "price": round(price, 2),
                    "inventory": inv
                })
                kept_variants += 1
                if price > 0 and price < min_price:
                    min_price = price

            if not variants:
                # даже если не берём в итоговый каталог (нет валидных вариантов),
                # всё равно сформируем строку для AI, чтобы он знал про ассортимент
                pass
            else:
                if min_price == 9e9:
                    min_price = min((v["price"] for v in variants), default=0.0)

                product = {
                    "product_id": product_id,
                    "handle": handle,
                    "title": title,
                    "vendor": vendor,
                    "tags": tags,
                    "price": round(min_price, 2),
                    "currency": "EUR",
                    "primary_image": primary_image,
                    "variants": variants
                }
                catalog.append(product); kept_products += 1

                for t in tags: by_tag.setdefault(t, []).append(product_id)
                by_bucket.setdefault(bucket(min_price), []).append(product_id)

            # строка для AI — всегда (даже если variants пусто)
            ai_rows.append({
                "id": product_id,
                "title": title,
                "type": ptype,
                "tags": tags,
                "status": status,
                "published": bool(published),
                "price": round(min_price if min_price != 9e9 else pick_price(first), 2) if rows["_calc_price"].notna().any() else 0.0,
                "inventory_qty": int(max_inv_seen if max_inv_seen > 0 else total_inv_seen),
                "image": primary_image,
                "body_html": clean_html(desc, maxlen=500)
            })

    if not any_rows:
        print("No data read from CSV_URL (check direct link/permissions).", file=sys.stderr)

    out_catalog = OUT_DIR / "catalog.min.json"
    out_index   = OUT_DIR / "index.min.json"
    out_ai      = OUT_DIR / "catalog_for_ai.json"

    with open(out_catalog, "w", encoding="utf-8") as f:
        json.dump(catalog, f, ensure_ascii=False, separators=(",", ":"))
    with open(out_index, "w", encoding="utf-8") as f:
        json.dump({"by_tag": by_tag, "by_price_bucket": by_bucket}, f, ensure_ascii=False, separators=(",", ":"))
    with open(out_ai, "w", encoding="utf-8") as f:
        json.dump(ai_rows, f, ensure_ascii=False, separators=(",", ":"))

    # Печатаем пути (3 строки) — их читает workflow:
    print(str(out_catalog))
    print(str(out_index))
    print(str(out_ai))

    print(
        f"SUMMARY rows={total_rows} products_in_csv={total_products} kept_products={kept_products} kept_variants={kept_variants} "
        f"include_oos={INCLUDE_OOS} allow_zero_price={ALLOW_ZERO_PRICE}",
        file=sys.stderr
    )

if __name__ == "__main__":
    main()
