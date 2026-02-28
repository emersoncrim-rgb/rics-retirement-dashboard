import json
import tempfile
import unittest
from pathlib import Path

import sector_prefs_store


class TestSectorPrefsStore(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.data_dir = Path(self.temp_dir.name)

        self.tax_path = self.data_dir / "tax_profile.json"
        self.const_path = self.data_dir / "constraints.json"

        with open(self.tax_path, "w") as f:
            json.dump({}, f)
        with open(self.const_path, "w") as f:
            json.dump({}, f)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_load_defaults(self):
        prefs = sector_prefs_store.load_sector_preferences({})
        self.assertEqual(prefs["liked_sectors"], [])
        self.assertEqual(prefs["avoided_sectors"], [])
        self.assertEqual(prefs["tilt_strength"], 0)

    def test_validate_trim_and_dedupe(self):
        prefs = {
            "liked_sectors": [" Tech ", "tech", "HEALTH", ""],
            "avoided_sectors": ["  Finance", "finance "],
            "tilt_strength": 3,
        }
        clean, errors = sector_prefs_store.validate_sector_preferences(prefs)
        self.assertEqual(errors, [])
        self.assertEqual(clean["liked_sectors"], ["Tech", "HEALTH"])
        self.assertEqual(clean["avoided_sectors"], ["Finance"])
        self.assertEqual(clean["tilt_strength"], 3)

    def test_validate_overlap(self):
        prefs = {"liked_sectors": ["Tech"], "avoided_sectors": ["tech"]}
        clean, errors = sector_prefs_store.validate_sector_preferences(prefs)
        self.assertTrue(any("Overlap" in e for e in errors))

    def test_validate_max_length(self):
        prefs = {"liked_sectors": [str(i) for i in range(25)]}
        clean, errors = sector_prefs_store.validate_sector_preferences(prefs)
        self.assertTrue(any("Maximum 20" in e for e in errors))
        self.assertEqual(len(clean["liked_sectors"]), 20)

    def test_validate_tilt_strength_bounds(self):
        prefs = {"tilt_strength": 10}
        clean, errors = sector_prefs_store.validate_sector_preferences(prefs)
        self.assertEqual(clean["tilt_strength"], 5)

        prefs2 = {"tilt_strength": -5}
        clean2, errors2 = sector_prefs_store.validate_sector_preferences(prefs2)
        self.assertEqual(clean2["tilt_strength"], 0)

    def test_save_valid_preferences(self):
        prefs = {"liked_sectors": ["Tech"], "avoided_sectors": ["Finance"], "tilt_strength": 2}
        sector_prefs_store.save_sector_preferences(prefs, str(self.tax_path), str(self.const_path))

        with open(self.tax_path, "r") as f:
            data = json.load(f)
        self.assertIn("sector_preferences", data)
        self.assertEqual(data["sector_preferences"]["liked_sectors"], ["Tech"])

    def test_save_invalid_preferences(self):
        prefs = {"liked_sectors": ["Tech"], "avoided_sectors": ["Tech"]}
        with self.assertRaises(ValueError):
            sector_prefs_store.save_sector_preferences(prefs, str(self.tax_path), str(self.const_path))


if __name__ == "__main__":
    unittest.main()
