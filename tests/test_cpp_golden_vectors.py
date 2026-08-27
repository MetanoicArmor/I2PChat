"""Drift guard for the C++ port's golden vectors.

The fixtures under ``cpp/testdata/vectors/`` are the compatibility contract for
the C++ client. They are generated from this implementation with a
deterministic CSPRNG, so regenerating them must be a no-op. A failure here
means a wire format, key schedule or at-rest format changed and the C++ port
(and the interop guarantee with released 1.4.x clients) needs attention.
"""

from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
GENERATOR = REPO_ROOT / "cpp" / "testdata" / "generate_vectors.py"
VECTORS_DIR = REPO_ROOT / "cpp" / "testdata" / "vectors"

EXPECTED_VECTOR_FILES = {
    "blindbox.json",
    "canonical_json.json",
    "crypto_handshake.json",
    "crypto_hkdf.json",
    "group_wire.json",
    "groups.json",
    "protocol_frames.json",
    "sam.json",
    "sealed_files.json",
    "text_chunking.json",
}


class GoldenVectorTests(unittest.TestCase):
    def test_all_expected_vector_files_are_committed(self) -> None:
        present = {p.name for p in VECTORS_DIR.glob("*.json")}
        self.assertEqual(EXPECTED_VECTOR_FILES, present)

    def test_vectors_are_valid_json_with_a_description(self) -> None:
        for path in sorted(VECTORS_DIR.glob("*.json")):
            with self.subTest(vector=path.name):
                payload = json.loads(path.read_text(encoding="utf-8"))
                self.assertIsInstance(payload, dict)
                self.assertTrue(payload.get("_description"))

    def test_regenerating_vectors_is_a_no_op(self) -> None:
        before = {
            path.name: path.read_bytes() for path in sorted(VECTORS_DIR.glob("*.json"))
        }
        result = subprocess.run(
            [sys.executable, str(GENERATOR)],
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
        )
        self.assertEqual(
            result.returncode, 0, f"generator failed:\n{result.stdout}\n{result.stderr}"
        )
        after = {
            path.name: path.read_bytes() for path in sorted(VECTORS_DIR.glob("*.json"))
        }
        drifted = sorted(
            name for name in after if before.get(name) != after[name]
        )
        self.assertEqual(
            drifted,
            [],
            "Golden vectors changed. If this is an intentional protocol or "
            "storage change, review cpp/docs/COMPATIBILITY.md and the C++ tests "
            "before committing the regenerated fixtures.",
        )


if __name__ == "__main__":
    unittest.main()
