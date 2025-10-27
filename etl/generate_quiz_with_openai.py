#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Generate quiz_output.json with OpenAI based on a compact product dataset.

Usage:
  python etl/generate_quiz_with_openai.py \
    --catalog_ai ./out/catalog_for_ai.json \
    --out ./out/quiz_output.json

ENV:
  OPENAI_API_KEY (required)
  OPENAI_MODEL   (optional, default: gpt-4.1-mini or gpt-4o-mini / gpt-4-turbo)
"""

import os
import json
import time
import argparse
from pathlib import Path

from jsonschema import validate, ValidationError
from openai import OpenAI

# -------------------- JSON schema --------------------
QUIZ_SCHEMA = {
  "type": "object",
  "required": ["personas", "quiz", "recommended_product"],
  "properties": {
    "personas": {
      "type": "array",
      "minItems": 3,
      "maxItems": 20,
      "items": {
        "type": "object",
        "required": ["id", "name"],
        "properties": {
          "id": {"type": "string"},
          "name": {"type": "string"}
        }
      }
    },
    "quiz": {
      "type": "array",
      "minItems": 1,
      "maxItems": 10,
      "items": {
        "type": "object",
        "required": ["id", "text", "type", "options"],
        "properties": {
          "id":   {"type": "string"},
          "text": {"type": "string"},
          "type": {"type": "string", "enum": ["single"]},
          "options": {
            "type": "array",
            "minItems": 4,
            "maxItems": 4,
            "items": {
              "type": "object",
              "required": ["id", "label"],
              "properties": {
                "id": {"type": "string"},
                "label": {"type": "string"},
                "filters": {"type": "object"}
              }
            }
          },
          "weights": {"type": "object"}
        }
      }
    },
    "recommended_product": {
      "type": "object",
      "required": ["id", "name", "image", "price", "stock"],
      "properties": {
        "id":    {"type": "string"},
        "name":  {"type": "string"},
        "image": {"type": "string"},
        "price": {"type": "string"},
        "stock": {"type": "string"}
      }
    },
    "analysis_example": {"type": "string"},
    "recommendation_logic": {"type": "string"}
  }
}

SYSTEM_HINT = (
  "You are the Gift Finder Quiz Master for an ecommerce site. "
  "You must ONLY use the provided JSON product catalogue. "
  "Create a multiple-choice quiz (max 10 questions, 4 options each) that infers a persona "
  "and select ONE in-stock product that fits that persona. "
  "Return STRICT JSON that matches the provided schema. No markdown, no prose, only JSON."
)

USER_PROMPT_TMPL = """\
SOURCE DATA (compact products JSON):
{catalog_json}

Rules:
- Stock Priority: recommend a product only if inventory_qty > 0.
- Use title, body_html, type, tags to reason about personas.
- Keep questions short and storefront-safe.
- Currency: use the symbol present in price context (€, £ etc). If unclear, prefer €.
- Return valid JSON only (no comments, no markdown).
- Aim for 5–10 questions if possible.

Output JSON must match this shape exactly:
{schema_json}
"""

# -------------------- helpers --------------------
def load_ai_catalog(path: str, max_items: int = 300):
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data[:max_items]

def ask_openai(model: str, api_key: str, system_hint: str, user_prompt: str) -> str:
    client = OpenAI(api_key=api_key)
    # Используем chat.completions для совместимости со «старыми» версиями SDK
    resp = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system_hint},
            {"role": "user", "content": user_prompt}
        ],
        temperature=0.4,
        top_p=0.9,
        max_tokens=2000,
    )
    return resp.choices[0].message.content

def try_parse_and_validate(txt: str):
    data = json.loads(txt)
    validate(instance=data, schema=QUIZ_SCHEMA)
    return data

def build_fallback():
    return {
      "personas":[
        {"id":"general","name":"General"},
        {"id":"cozy","name":"Cozy Homebody"},
        {"id":"tech","name":"Tech Enthusiast"}
      ],
      "quiz":[
        {"id":"budget","text":"What's your budget?","type":"single","options":[
          {"id":"u30","label":"Under €30"},
          {"id":"30_60","label":"€30–€60"},
          {"id":"60_120","label":"€60–€120"},
          {"id":"o120","label":"€120+"}
        ]}
      ],
      "analysis_example":"Fallback used.",
      "recommendation_logic":"Fallback used.",
      "recommended_product":{"id":"","name":"","image":"","price":"€0","stock":"In Stock (Qty: 0)"}
    }

# -------------------- main --------------------
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--catalog_ai", required=True, help="Path to ./out/catalog_for_ai.json")
    parser.add_argument("--out", required=True, help="Path to write ./out/quiz_output.json")
    args = parser.parse_args()

    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        print("ERROR: OPENAI_API_KEY is missing")
        raise SystemExit(1)
    model = os.environ.get("OPENAI_MODEL", "gpt-4.1-mini")

    items = load_ai_catalog(args.catalog_ai, max_items=300)
    catalog_json = json.dumps(items, ensure_ascii=False)
    schema_json = json.dumps(QUIZ_SCHEMA, ensure_ascii=False)

    user_prompt = USER_PROMPT_TMPL.format(
        catalog_json=catalog_json,
        schema_json=schema_json
    )

    attempts = 3
    last_err = None
    data = None
    for _ in range(attempts):
        try:
            raw = ask_openai(model, api_key, SYSTEM_HINT, user_prompt)
            data = try_parse_and_validate(raw)
            break
        except (ValidationError, json.JSONDecodeError) as e:
            last_err = e
            # Вторая попытка: попросим исправить JSON (без schema-tools)
            try:
                fix_prompt = (
                    "The previous JSON didn't match the schema. "
                    "Please return a corrected JSON ONLY, matching this schema strictly:\n"
                    f"{schema_json}\n\n"
                    "Previous output:\n"
                    f"{raw}"
                )
                raw2 = ask_openai(model, api_key, SYSTEM_HINT, fix_prompt)
                data = try_parse_and_validate(raw2)
                break
            except Exception as e2:
                last_err = e2
                time.sleep(2)
        except Exception as e:
            last_err = e
            time.sleep(2)

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    if data is None:
        fb = build_fallback()
        with out_path.open("w", encoding="utf-8") as f:
            json.dump(fb, f, ensure_ascii=False, separators=(",", ":"))
        print(f"[fallback] quiz_output.json created. Last error: {last_err}")
        return

    with out_path.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
    print(f"quiz_output.json generated with {len(data.get('quiz', []))} questions and {len(data.get('personas', []))} personas.")

if __name__ == "__main__":
    main()
