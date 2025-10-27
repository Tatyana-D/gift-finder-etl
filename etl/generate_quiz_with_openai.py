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
  OPENAI_MODEL   (optional) — default: gpt-4o-mini
"""

import os
import json
import time
import argparse
from pathlib import Path
from jsonschema import validate, ValidationError

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

SYSTEM_PROMPT = (
  "You are the Gift Finder Quiz Master. You will ONLY use the provided product "
  "catalogue JSON. Build a short multiple-choice quiz to infer a persona and "
  "recommend one in-stock product.\n"
  "Return STRICT JSON matching the given schema. No markdown, no commentary."
)

USER_INSTRUCTIONS = """\
Source Data Requirement: Use ONLY this JSON array of products (subset):
Each item: {id, title, type, tags, status, published, price, inventory_qty, image, body_html}

Rules:
- Stock Priority: recommend only products with inventory_qty > 0.
- Matching: use title, body_html, type, tags.
- Persona Mapping: quiz answers should steer to personas; final product must fit the persona.
- Keep questions concise and safe for storefront.
- Max 10 questions, 4 options each, single choice.
- Currency: prefer the symbol you see in data (€, £). If unknown, use €.

Output JSON (exact keys):
{
  "personas": [{"id":"host","name":"Elegant Host"}, ... (max 20)],
  "quiz": [
    {
      "id":"q1","text":"...","type":"single",
      "options":[
        {"id":"a","label":"..."},
        {"id":"b","label":"..."},
        {"id":"c","label":"..."},
        {"id":"d","label":"..."}
      ],
      "weights": { "a":{"host":2}, "b":{"cozy":2} }  // optional mapping to personas
    }
  ],
  "analysis_example":"...",
  "recommendation_logic":"...",
  "recommended_product":{
    "id":"...", "name":"...", "image":"...", "price":"€..", "stock":"In Stock (Qty: ..)"
  }
}

CATALOG_SNIPPET:
"""

def load_ai_catalog(path: str, max_items: int = 300):
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data[:max_items]

def call_openai(api_key: str, model_name: str, snippet_json: str) -> str:
    # Новый SDK v1.x
    from openai import OpenAI
    client = OpenAI(api_key=api_key)

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": USER_INSTRUCTIONS + snippet_json}
    ]

    # Без response_format — просто просим строгий JSON текстом
    resp = client.chat.completions.create(
        model=model_name or "gpt-4o-mini",
        messages=messages,
        temperature=0.4,
        top_p=0.9,
        max_tokens=1800
    )
    return resp.choices[0].message.content.strip()

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--catalog_ai", required=True, help="Path to ./out/catalog_for_ai.json")
    parser.add_argument("--out", required=True, help="Path to write ./out/quiz_output.json")
    args = parser.parse_args()

    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        print("ERROR: OPENAI_API_KEY is missing")
        raise SystemExit(1)

    model_name = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")
    items = load_ai_catalog(args.catalog_ai, max_items=300)
    snippet_json = json.dumps(items, ensure_ascii=False)

    attempts = 3
    last_err = None
    for _ in range(attempts):
        try:
            raw = call_openai(api_key, model_name, snippet_json)
            data = json.loads(raw)
            validate(instance=data, schema=QUIZ_SCHEMA)
            Path(args.out).parent.mkdir(parents=True, exist_ok=True)
            with open(args.out, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
            print(f"quiz_output.json generated with {len(data.get('quiz', []))} questions.")
            return
        except (json.JSONDecodeError, ValidationError, Exception) as e:
            last_err = e
            time.sleep(2)

    # Fallback (минимальный опрос по бюджету)
    fallback = {
      "personas":[{"id":"general","name":"General"}, {"id":"host","name":"Elegant Host"}, {"id":"cozy","name":"Cozy Homebody"}],
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
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(fallback, f, ensure_ascii=False, separators=(",", ":"))
    print(f"[fallback] quiz_output.json created. Last error: {last_err}")

if __name__ == "__main__":
    main()
