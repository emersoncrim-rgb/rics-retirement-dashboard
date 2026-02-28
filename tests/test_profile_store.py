# tests/test_profile_store.py
import json
import tempfile
import unittest
from pathlib import Path

import profile_store

class TestProfileStore(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.data_dir = Path(self.temp_dir.name)
        
        # Override module paths to write/read from temp
        self.tax_path = self.data_dir / "tax_profile.json"
        self.const_path = self.data_dir / "constraints.json"        
        self.tax_data = {
            "filing_status": "mfj",
            "tax_year": 2025,
            "ages": [72, 70],
            "ss_combined_annual": 56000,
            "unknown_tax_key": "preserve_me"
        }
        self.const_data = {
            "rmd_start_age": 73,
            "aggressiveness_score": {
                "current_target": 45
            },
            "unknown_const_key": "keep_me_too"
        }
        
        with open(self.tax_path, "w") as f:
            json.dump(self.tax_data, f)
        with open(self.const_path, "w") as f:
            json.dump(self.const_data, f)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_load_profile(self):
        merged = profile_store.load_profile(self.tax_path, self.const_path)
        self.assertEqual(merged["filing_status"], "mfj")
        self.assertEqual(merged["rmd_start_age"], 73)
        self.assertEqual(merged["unknown_tax_key"], "preserve_me")
        self.assertEqual(merged["unknown_const_key"], "keep_me_too")

    def test_validate_profile_valid(self):
        valid_patch = {"ages": [73, 71], "ss_combined_annual": 60000}
        errors = profile_store.validate_profile(valid_patch)
        self.assertEqual(len(errors), 0)

    def test_validate_profile_invalid(self):
        invalid_patch = {
            "ages": [-1, 70], 
            "filing_status": "married",
            "aggressiveness_score": {"current_target": 105},
            "agi_prior_year": -5000
        }
        errors = profile_store.validate_profile(invalid_patch)
        self.assertEqual(len(errors), 4)
        self.assertTrue(any("Ages" in e for e in errors))
        self.assertTrue(any("filing_status" in e for e in errors))
        self.assertTrue(any("between 0 and 100" in e for e in errors))
        self.assertTrue(any("non-negative" in e for e in errors))

    def test_save_profile_valid(self):
        patch = {
            "filing_status": "single",
            "rmd_start_age": 75,
            "new_key": "some_value"
        }
        merged, errors = profile_store.save_profile(patch, self.tax_path, self.const_path)
        self.assertEqual(errors, [])
        self.assertEqual(merged["filing_status"], "single")
        
        with open(self.tax_path, "r") as f:
            tax = json.load(f)
        with open(self.const_path, "r") as f:
            const = json.load(f)
            
        self.assertEqual(tax["filing_status"], "single")
        self.assertEqual(const["rmd_start_age"], 75)
        self.assertEqual(tax["unknown_tax_key"], "preserve_me")
        self.assertEqual(const["unknown_const_key"], "keep_me_too")
        self.assertEqual(tax["new_key"], "some_value")

    def test_save_profile_invalid(self):
        patch = {"ages": [-5, 70]}
        merged, errors = profile_store.save_profile(patch)
        self.assertTrue(len(errors) > 0)
        
        with open(self.tax_path, "r") as f:
            tax = json.load(f)
        
        self.assertEqual(tax["ages"], [72, 70])

if __name__ == "__main__":
    unittest.main()
