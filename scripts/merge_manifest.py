"""Merge master + txn manifests into one source_manifest.json.
Also records generator version, seed, scale, and the command lines
that produced the data, so the manifest is self-describing."""

import json, pathlib, subprocess, sys

REPO = pathlib.Path(__file__).resolve().parent.parent
m1 = json.loads((REPO / "source_manifest.json").read_text())
m2 = json.loads((REPO / "source_manifest_txn.json").read_text())

merged = {
    "generator_version": "0.3.0",
    "seed": m1["seed"],
    "scale": m1["scale"],
    "commands": [
        "python -m src.generate --seed 20261022 --scale starter",
        "python -m src.generate_txn --seed 20261022 --scale starter",
        "python -m src.anomalies",
    ],
    "master": m1["files"],
    "txn": m2["files"],
}
out = REPO / "source_manifest.json"
out.write_text(json.dumps(merged, indent=2, default=str))
print(f"wrote {out} with {len(merged['master'])} master and {len(merged['txn'])} txn files")