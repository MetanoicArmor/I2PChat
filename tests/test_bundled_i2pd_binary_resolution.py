import tempfile
import unittest
from pathlib import Path
from unittest import mock

from i2pchat.router import bundled_i2pd


class BundledI2pdBinaryResolutionTests(unittest.TestCase):
    def test_prefers_repo_path(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            rel = root / "vendor" / "i2pd" / "linux-x86_64"
            rel.mkdir(parents=True, exist_ok=True)
            binary = rel / "i2pd"
            binary.write_text("x", encoding="utf-8")
            with mock.patch.object(bundled_i2pd.sys, "platform", "linux"), \
                    mock.patch.object(
                        bundled_i2pd.Path,
                        "resolve",
                        return_value=root / "i2pchat" / "router" / "bundled_i2pd.py",
                    ):
                got = bundled_i2pd.resolve_bundled_i2pd_binary()
            self.assertEqual(got, str(binary))

    def test_uses_meipass_when_repo_binary_missing(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            meipass = Path(td)
            rel = meipass / "vendor" / "i2pd" / "linux-x86_64"
            rel.mkdir(parents=True, exist_ok=True)
            binary = rel / "i2pd"
            binary.write_text("x", encoding="utf-8")
            fake_file = meipass / "elsewhere" / "bundled_i2pd.py"
            fake_file.parent.mkdir(parents=True, exist_ok=True)
            fake_file.write_text("", encoding="utf-8")
            with mock.patch.object(bundled_i2pd.sys, "platform", "linux"), \
                    mock.patch.object(bundled_i2pd.sys, "_MEIPASS", str(meipass), create=True), \
                    mock.patch.object(
                        bundled_i2pd.Path,
                        "resolve",
                        return_value=fake_file,
                    ):
                got = bundled_i2pd.resolve_bundled_i2pd_binary()
            self.assertEqual(got, str(binary))


if __name__ == "__main__":
    unittest.main()
