"""Prove reproducibility: run the generators twice with the same seed and
compare the manifests byte-for-byte. Exits non-zero on mismatch."""

import filecmp
import pathlib
import shutil
import subprocess
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent
BUILD = REPO / "build"
BUILD.mkdir(exist_ok=True)
TMP = BUILD / "manifest_run1.json"


def run_generators() -> None:
    for mod in ("src.generate", "src.generate_txn"):
        subprocess.run(
            [sys.executable, "-m", mod, "--seed", "20261022", "--scale", "starter"],
            cwd=REPO, check=True, stdout=subprocess.DEVNULL,
        )


def main() -> int:
    run_generators()
    shutil.copy(REPO / "source_manifest_txn.json", TMP)
    run_generators()
    if not filecmp.cmp(TMP, REPO / "source_manifest_txn.json", shallow=False):
        print("NOT REPRODUCIBLE: manifests differ")
        return 1
    print("REPRODUCIBLE: manifests identical")
    return 0


if __name__ == "__main__":
    sys.exit(main())
