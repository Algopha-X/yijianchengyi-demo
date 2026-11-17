#!/usr/bin/env python3
"""Check local contracts and cross-file invariants without starting the application.

--strict additionally requires jsonschema and openapi-spec-validator. The default
checks intentionally do not claim to implement the full JSON Schema standard.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1]
FIELD_KEYS = {"title", "subtitle", "body", "time", "location", "organizer"}
STYLES = {"fresh", "asymmetric", "lively", "elegant", "modern", "retro"}
SCENARIOS = {"poster", "article_image"}


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def load(path: Path) -> dict:
    with path.open(encoding="utf-8") as stream:
        return json.load(stream)


def check_refs(node: object, source: Path) -> None:
    if isinstance(node, dict):
        if "$ref" in node:
            value = node["$ref"]
            parts = urlsplit(value)
            require(not parts.scheme, f"Unexpected remote $ref in {source}: {value}")
            target = (source.parent / unquote(parts.path)).resolve() if parts.path else source
            require(target.is_relative_to(ROOT), f"Reference escapes project: {value}")
            current = load(target)
            if parts.fragment:
                require(parts.fragment.startswith("/"), f"Unsupported reference: {value}")
                for token in unquote(parts.fragment).split("/")[1:]:
                    token = token.replace("~1", "/").replace("~0", "~")
                    current = current[int(token)] if isinstance(current, list) else current[token]
        for value in node.values():
            check_refs(value, source)
    elif isinstance(node, list):
        for value in node:
            check_refs(value, source)


def check_box(box: dict, canvas: dict, label: str) -> None:
    require(all(type(box[key]) is int for key in ("x", "y", "width", "height")), f"Non-integer box: {label}")
    require(box["x"] >= 0 and box["y"] >= 0, f"Negative box: {label}")
    require(box["width"] > 0 and box["height"] > 0, f"Empty box: {label}")
    require(box["x"] + box["width"] <= canvas["width"], f"Box outside width: {label}")
    require(box["y"] + box["height"] <= canvas["height"], f"Box outside height: {label}")


def check_template(path: Path, template: dict) -> None:
    require(template["id"] == path.stem, f"Filename/id mismatch: {path}")
    require(template["schemaVersion"] == "1.0", f"Unknown schemaVersion: {path}")
    require(template["style"] in STYLES and template["scenario"] in SCENARIOS, f"Unknown category: {path}")
    require(template["id"].endswith(f'-v{template["version"]}'), f"Version mismatch: {path}")
    prefix = "poster" if template["scenario"] == "poster" else "article-image"
    require(template["id"] == f'{prefix}-{template["style"]}-v{template["version"]}', f"ID/category mismatch: {path}")
    expected_size = (1080, 1440) if template["scenario"] == "poster" else (1200, 675)
    require((template["canvas"]["width"], template["canvas"]["height"]) == expected_size, f"Wrong canvas: {path}")
    require(set(template["fields"]) == FIELD_KEYS, f"Fields differ from shared keys: {path}")
    require(template["fields"]["title"]["required"] and template["fields"]["body"]["required"], f"Missing required title/body: {path}")
    for key, field in template["fields"].items():
        require(field["minFontSize"] <= field["fontSize"], f"Font range invalid: {path}:{key}")
        require(field["question"].strip() != "", f"Empty question: {path}:{key}")
        check_box(field["box"], template["canvas"], f"{path}:{key}")
    check_box(template["imageSlot"]["box"], template["canvas"], f"{path}:imageSlot")
    for key, box in template["withoutImage"].items():
        require(key in FIELD_KEYS, f"Unknown no-image field: {key}")
        check_box(box, template["canvas"], f"{path}:withoutImage:{key}")
    for item in template["decorations"]:
        check_box(item["box"], template["canvas"], f"{path}:decoration")


def check_markdown_links() -> int:
    count = 0
    for path in ROOT.rglob("*.md"):
        if any(part in {"node_modules", ".venv", ".git", "data"} for part in path.parts):
            continue
        for raw in re.findall(r"\[[^\]]+\]\(([^)]+)\)", path.read_text(encoding="utf-8")):
            target = raw.strip("<>").split("#", 1)[0]
            if not target or urlsplit(target).scheme:
                continue
            require((path.parent / unquote(target)).exists(), f"Broken link in {path}: {raw}")
            count += 1
    return count


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--strict", action="store_true")
    args = parser.parse_args()
    schema_path = ROOT / "contracts/template.schema.json"
    api_path = ROOT / "contracts/openapi.json"
    schema, api = load(schema_path), load(api_path)
    check_refs(schema, schema_path)
    check_refs(api, api_path)
    require(api["openapi"] == "3.1.0", "Unexpected OpenAPI version")
    ids = [op["operationId"] for path in api["paths"].values() for method, op in path.items() if method in {"get", "post", "patch", "delete", "put"}]
    require(len(ids) == len(set(ids)), "Duplicate operationId")
    require(len(ids) == 16, "Update the route count check with an intentional API contract change")
    require(set(api["components"]["schemas"]["Fields"]["properties"]) == FIELD_KEYS, "API fields mismatch")
    templates = [(path, load(path)) for path in sorted((ROOT / "templates").glob("*.json"))]
    require(bool(templates), "No templates")
    for path, template in templates:
        check_template(path, template)
    examples = [(path, load(path)) for path in sorted((ROOT / "examples").glob("*.json"))]
    require(len(examples) >= 2, "Need complete and missing examples")
    for path, example in examples:
        require(example["scenario"] in SCENARIOS, f"Unknown scenario: {path}")
        require(example.get("style") in STYLES, f"Unknown style: {path}")
        require(0 < len(example["rawText"]) <= 10000, f"Invalid rawText length: {path}")
        require(len(example["assetIds"]) <= 1, f"Too many assets: {path}")
    links = check_markdown_links()
    baseline_path = ROOT / "contracts/baseline.json"
    if baseline_path.exists():
        baseline = load(baseline_path)
        required_paths = {"contracts/openapi.json", "contracts/template.schema.json", "contracts/database.sql",
                          "docs/API.md", "docs/DEVELOPMENT.md", "docs/TEMPLATE_SPEC.md"}
        require(set(baseline["sha256"]) == required_paths, "Contract snapshot must cover all six contract sources")
        for relative, expected in baseline["sha256"].items():
            require(hashlib.sha256((ROOT / relative).read_bytes()).hexdigest() == expected,
                    f"Contract snapshot changed: {relative}; update through a group-approved contract MR")
        print("Contract snapshot hashes match; group approval must still be verified on GitLab.")
    print(f"Structural checks passed: {len(ids)} operations, {len(templates)} template(s), {len(examples)} examples, {links} local links.")
    if args.strict:
        try:
            from jsonschema import Draft202012Validator
            from openapi_spec_validator import validate
        except ImportError as exc:
            raise ValueError("--strict requires jsonschema and openapi-spec-validator in the current Python environment") from exc
        Draft202012Validator.check_schema(schema)
        validator = Draft202012Validator(schema)
        for _, template in templates:
            validator.validate(template)
        validate(api, base_uri=api_path.as_uri())
        request_schema = {**api["components"]["schemas"]["TaskCreate"], "components": api["components"]}
        for _, example in examples:
            Draft202012Validator(request_schema).validate(example)
        print("Full JSON Schema, sample data and OpenAPI validation passed.")
    else:
        print("Full standard validation was not requested; use --strict with validator dependencies.")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (ValueError, KeyError, IndexError, OSError, json.JSONDecodeError) as exc:
        print(f"Contract check failed: {exc}", file=sys.stderr)
        sys.exit(1)
