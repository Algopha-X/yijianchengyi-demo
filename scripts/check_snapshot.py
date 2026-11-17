"""Verify exported source files without revealing credentials or private history."""

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    publication = json.loads((ROOT / "PUBLICATION.json").read_text(encoding="utf-8"))
    mismatches = []
    expected_files = publication.get("exportFileSha256", publication["sourceFileSha256"])
    for name, expected in expected_files.items():
        path = (ROOT / name).resolve()
        if not path.is_relative_to(ROOT) or not path.is_file():
            mismatches.append(name)
            continue
        data = path.read_bytes()
        if not name.endswith(".ttf"):
            data = data.decode("utf-8").replace("\r\n", "\n").encode("utf-8")
        if hashlib.sha256(data).hexdigest() != expected:
            mismatches.append(name)
    if mismatches:
        print("Source snapshot differs: " + ", ".join(mismatches))
        return 1
    print(f"Verified {len(expected_files)} exported source files; original hashes and documented publication adjustments remain in PUBLICATION.json.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
