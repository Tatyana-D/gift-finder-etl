#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Generate quiz_output.json with Gemini based on a compact product dataset.

Usage:
  python etl/generate_quiz_with_gemini.py \
    --catalog_ai ./out/catalog_for_ai.json \
    --out ./out/quiz_output.json

ENV:
  GOOGLE_API_KEY (required) — https://aistudio.google.com/app/apikey
  GEMINI_MODEL   (optional) — default: gemini-1.5-pro
"""

import os
import json
import time
import argparse
from pathlib import Path

from jsonschema import validate
import google.generativeai as genai

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
      "maxItems": 5,
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

# -------------------- Prompts --------------------
SYSTEM_HINT = (
  "You are the Gift Finder Quiz Master. Use ONLY the provided product catalogue JSON.\n"
  "Goal: Create a short multiple-choice quiz (max 5 questions, 4 options each) to infer a persona and pick one in-stock product.\n"
  "Return STRICT JSON per schema. No markdown, no commentary."
)

USER_PROMPT = """\
Source Data Requirement: Use ONLY the provided JSON array of products with fields:
- id, title, type, tags, status, published, price, inventory_qty, image, body_html

Core Rules:
- Stock Priority: recommend only products with inventory_qty > 0.
- Search/Matching: use title, body_html, type, tags.
- Persona Mapping: map answers to personas and ensure the final product fits.
- Keep questions concise and storefront-safe.
- Currency: use the symbol present in data context (e.g., € or £). If unknown, use €.

Output JSON shape:
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
      "weights": { "a":{"host":2}, "b":{"cozy":2} }  // optional
    }
  ],
  "analysis_example":"...",
  "recommendation_logic":"...",
  "recommended_product":{
    "id":"...", "name":"...", "image":"...", "price":"€..", "stock":"In Stock (Qty: ..)"
  }
}

CATALOG_SNIPPET:
{snippet}
"""

# -------------------- Helpers --------------------
def load_ai_catalog(path: str, max_items: int = 300):
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data[:max_items]

def call_gemini(api_key: str, model_name: str, snippet_json: str) -> str:
    genai.configure(api_key=api_key)
    model = genai.GenerativeModel(model_name or "gemini-1.5-pro")
    prompt = USER_PROMPT.format(snippet=snippet_json)
    resp = model.generate_content(
        [SYSTEM_HINT, prompt],
        generation_config={
            "temperature": 0.5,
            "top_p": 0.9,
            "max_output_tokens": 1800,
            "response_mime_type": "application/json"
        }
    )
    return resp.text

# -------------------- Entry --------------------
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--catalog_ai", required=True, help="Path to ./out/catalog_for_ai.json")
    parser.add_argument("--out", required=True, help="Path to write ./out/quiz_output.json")
    args = parser.parse_args()

    api_key = os.environ.get("GOOGLE_API_KEY")
    if not api_key:
        print("ERROR: GOOGLE_API_KEY is missing")
        raise SystemExit(1)

    model_name = os.environ.get("GEMINI_MODEL", "gemini-1.5-pro")
    items = load_ai_catalog(args.catalog_ai, max_items=300)
    snippet_json = json.dumps(items, ensure_ascii=False)

    attempts = 3
    last_err = None
    for _ in range(attempts):
        try:
            raw = call_gemini(api_key, model_name, snippet_json)
            data = json.loads(raw)
            validate(instance=data, schema=QUIZ_SCHEMA)
            Path(args.out).parent.mkdir(parents=True, exist_ok=True)
            with open(args.out, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
            print(f"quiz_output.json generated with {len(data.get('quiz', []))} questions.")
            return
        except Exception as e:
            last_err = e
            time.sleep(2)

    # Fallback if Gemini fails
    fallback = {
      "personas":[{"id":"general","name":"General"}],
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
