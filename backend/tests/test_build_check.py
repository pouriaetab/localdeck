"""The stale-build guard: it must catch the real thing and never cry wolf."""
from __future__ import annotations

import os
import tempfile
import time
import unittest
from pathlib import Path

from app import build_check


def write(path: Path, text: str, when: float | None = None) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    if when is not None:
        os.utime(path, (when, when))
    return path


class BuildCheckTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.old = time.time() - 10_000
        self.new = time.time()
        build_check._cache.clear()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _built_app(self, *, index_at: float, source_at: float) -> None:
        write(self.root / "static/index.html",
              '<script src="/assets/index-AAAA1111.js"></script>', index_at)
        write(self.root / "static/assets/index-AAAA1111.js", "current", index_at)
        write(self.root / "frontend/src/App.jsx", "source", source_at)

    def test_build_older_than_source_is_stale_and_names_the_file(self) -> None:
        self._built_app(index_at=self.old, source_at=self.new)
        status = build_check.status(str(self.root))
        self.assertTrue(status["stale"])
        self.assertEqual(status["newer_source"], "frontend/src/App.jsx")
        self.assertIn("stale build", " ".join(build_check.warnings_for(status)).lower())

    def test_build_newer_than_source_is_not_stale(self) -> None:
        self._built_app(index_at=self.new, source_at=self.old)
        status = build_check.status(str(self.root))
        self.assertFalse(status["stale"])
        self.assertEqual(build_check.warnings_for(status), [])

    def test_fingerprint_changes_when_the_build_changes(self) -> None:
        self._built_app(index_at=self.old, source_at=self.old)
        first = build_check.status(str(self.root))["fingerprint"]
        later = self.old + 500
        os.utime(self.root / "static/index.html", (later, later))
        second = build_check.status(str(self.root), refresh=True)["fingerprint"]
        self.assertNotEqual(first, second)

    def test_unreferenced_bundles_are_counted_and_hand_placed_files_are_not(self) -> None:
        self._built_app(index_at=self.new, source_at=self.old)
        write(self.root / "static/assets/index-OLD00001.js", "previous build")
        write(self.root / "static/assets/index-OLD00002.css", "previous build")
        write(self.root / "static/assets/logo.png", "not build output")
        status = build_check.status(str(self.root), refresh=True)
        self.assertEqual(status["orphan_bundles"], 2)
        self.assertIn("old bundle", " ".join(build_check.warnings_for(status)).lower())

    def test_a_project_with_nothing_built_has_no_status(self) -> None:
        write(self.root / "frontend/src/App.jsx", "source")
        self.assertIsNone(build_check.status(str(self.root)))
        self.assertEqual(build_check.warnings_for(None), [])

    def test_a_missing_directory_never_raises(self) -> None:
        self.assertIsNone(build_check.status(str(self.root / "does-not-exist")))

    def test_the_cache_is_not_a_filesystem_walk_per_call(self) -> None:
        self._built_app(index_at=self.old, source_at=self.old)
        walks = 0
        original = build_check._inspect

        def counting(path: str):
            nonlocal walks
            walks += 1
            return original(path)

        build_check._inspect = counting
        try:
            for _ in range(50):
                build_check.status(str(self.root))
        finally:
            build_check._inspect = original
        self.assertEqual(walks, 1, "serialize_state runs on every broadcast")


class ServedHtmlTests(unittest.TestCase):
    """What the running app serves is what decides whether staleness can apply."""

    def test_a_dev_server_is_recognised(self) -> None:
        body = b'<html><head><script type="module" src="/@vite/client"></script></head></html>'
        self.assertEqual(build_check.classify_html(body), "dev")

    def test_a_built_bundle_is_recognised(self) -> None:
        body = b'<html><head><script type="module" crossorigin src="/assets/index-DAqa-JCy.js"></script></head></html>'
        self.assertEqual(build_check.classify_html(body), "built")

    def test_plain_html_and_json_are_neither(self) -> None:
        self.assertEqual(build_check.classify_html(b"<html><body>hi</body></html>"), "other")
        self.assertIsNone(build_check.classify_html(b'{"status":"ok"}'))

    def test_missing_cache_control_counts_as_cacheable(self) -> None:
        # This is the whole bug: no header is not a safe default. With a
        # Last-Modified and no explicit lifetime, a browser invents one.
        self.assertIs(build_check.html_is_cacheable(None), True)

    def test_no_store_and_friends_are_not_cacheable(self) -> None:
        for value in ("no-store", "no-store, must-revalidate", "no-cache", "max-age=0"):
            self.assertIs(build_check.html_is_cacheable(value), False, value)

    def test_a_long_max_age_is_cacheable(self) -> None:
        self.assertIs(
            build_check.html_is_cacheable("public, max-age=31536000, immutable"), True
        )


if __name__ == "__main__":
    unittest.main()
