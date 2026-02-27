"""tests/test_settings_store.py — Focused tests for settings_store module."""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


# Ensure repo root is importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import settings_store


class TestSettingsStore(unittest.TestCase):
    """All tests use a temp directory so we never touch real ~/.rics."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.settings_dir = Path(self.tmp) / ".rics"
        self.settings_dir.mkdir()
        self.settings_file = self.settings_dir / "settings.json"
        # Patch _settings_path to use our temp location
        self._patcher = patch.object(
            settings_store, "_settings_path", return_value=self.settings_file
        )
        self._patcher.start()

    def tearDown(self):
        self._patcher.stop()
        # Cleanup
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    # ── Missing file => {} ────────────────────────────────────────────────
    def test_load_missing_file(self):
        """load_settings returns {} when the file does not exist."""
        self.assertFalse(self.settings_file.exists())
        self.assertEqual(settings_store.load_settings(), {})

    # ── Corrupt JSON => {} ────────────────────────────────────────────────
    def test_load_corrupt_json(self):
        self.settings_file.write_text("{bad json!!", encoding="utf-8")
        self.assertEqual(settings_store.load_settings(), {})

    def test_load_non_dict_json(self):
        """A JSON array or string should be treated as corrupt."""
        self.settings_file.write_text('["a","b"]', encoding="utf-8")
        self.assertEqual(settings_store.load_settings(), {})

    # ── Round-trip save/load ──────────────────────────────────────────────
    def test_save_and_load(self):
        settings_store.save_settings({"finnhub_api_key": "abc123"})
        result = settings_store.load_settings()
        self.assertEqual(result["finnhub_api_key"], "abc123")

    # ── Merge patch (shallow) ─────────────────────────────────────────────
    def test_merge_patch(self):
        settings_store.save_settings({"a": 1, "b": 2})
        merged = settings_store.save_settings({"b": 99, "c": 3})
        self.assertEqual(merged, {"a": 1, "b": 99, "c": 3})
        # Verify on-disk
        self.assertEqual(settings_store.load_settings(), {"a": 1, "b": 99, "c": 3})

    # ── get_setting / set_setting ─────────────────────────────────────────
    def test_get_setting_default(self):
        self.assertIsNone(settings_store.get_setting("nonexistent"))
        self.assertEqual(settings_store.get_setting("nonexistent", "fallback"), "fallback")

    def test_set_and_get_setting(self):
        settings_store.set_setting("price_mode", "live")
        self.assertEqual(settings_store.get_setting("price_mode"), "live")

    # ── Home vs fallback directory ────────────────────────────────────────
    def test_fallback_when_home_not_writable(self):
        """_settings_dir falls back to repo-local when home mkdir raises."""
        real_mkdir = Path.mkdir

        def _fail_mkdir(self_path, *a, **kw):
            if "fake_homeless" in str(self_path):
                raise OSError("permission denied")
            return real_mkdir(self_path, *a, **kw)

        with patch.object(Path, "home", return_value=Path("/tmp/fake_homeless_xyz")):
            with patch.object(Path, "mkdir", _fail_mkdir):
                d = settings_store._settings_dir()
                # Should be a repo-local .rics dir, not under fake_homeless
                self.assertNotIn("fake_homeless", str(d))
                self.assertTrue(str(d).endswith(".rics"))

    # ── Atomic write doesn't corrupt on normal operation ──────────────────
    def test_atomic_write_produces_valid_json(self):
        settings_store.save_settings({"key": "value"})
        raw = self.settings_file.read_text(encoding="utf-8")
        parsed = json.loads(raw)
        self.assertEqual(parsed, {"key": "value"})


if __name__ == "__main__":
    unittest.main()
