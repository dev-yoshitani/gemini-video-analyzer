import importlib.util
from pathlib import Path
import tempfile
import unittest
import zipfile


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("package_release", ROOT / "tools" / "package_release.py")
packager = importlib.util.module_from_spec(spec)
spec.loader.exec_module(packager)


class ReleasePackageTests(unittest.TestCase):
    def test_packages_have_gui_dependencies_but_no_private_or_outdated_files(self):
        with tempfile.TemporaryDirectory() as directory:
            archives = packager.build_packages(ROOT, Path(directory))
            for archive in archives:
                with zipfile.ZipFile(archive) as package:
                    names = set(package.namelist())
                    self.assertIn("desktop_app.py", names)
                    self.assertIn("USAGE.md", names)
                    self.assertNotIn(".env", names)
                    self.assertNotIn("handover.md", names)
                    self.assertNotIn(".app_settings.json", names)
                    self.assertNotIn("manual.pdf", names)
                    launcher = "Start_EN.bat" if "English" in archive.name else "Start.bat"
                    self.assertIn(launcher, names)
            self.assertTrue((Path(directory) / "SHA256SUMS.txt").exists())

    def test_repeated_builds_have_identical_archive_bytes(self):
        with tempfile.TemporaryDirectory() as directory:
            first = packager.build_packages(ROOT, Path(directory) / "first")
            second = packager.build_packages(ROOT, Path(directory) / "second")
            self.assertEqual([p.read_bytes() for p in first], [p.read_bytes() for p in second])
