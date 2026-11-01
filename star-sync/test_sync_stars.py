"""Focused offline tests for the Star sync only; never calls GitHub."""
import copy
import json
from pathlib import Path
import tempfile
import unittest

import sync_stars as sync

T0 = "2026-11-01T00:00:00Z"
T1 = "2026-11-01T01:00:00Z"


def item(rid=1, name="owner/one", when=T0, private=False):
    return {"starred_at": when, "repo": {"id": rid, "full_name": name,
            "private": private, "description": "demo", "language": "Python", "archived": False}}


def record(rid=1, name="owner/one", when=T0):
    return sync.normalize(item(rid, name, when))


class StarSyncTests(unittest.TestCase):
    def baseline(self):
        return sync.merge_stars(None, [record()], T0)

    def test_first_sync_is_baseline_not_pending(self):
        state = self.baseline()
        self.assertEqual(state["repositories"]["1"]["analysis_status"], "baseline")
        self.assertEqual(sync.pending_records(state), [])

    def test_only_new_ids_enter_queue_once(self):
        state = sync.merge_stars(self.baseline(), [record(), record(2, "other/two")], T1)
        again = sync.merge_stars(state, [record(), record(2, "other/two")], "later")
        self.assertEqual([r["repo_id"] for r in sync.pending_records(state)], [2])
        self.assertEqual(state, again)

    def test_done_and_notes_survive_rename_and_restar(self):
        state = self.baseline()
        sync.set_status(state, "owner/one", "done", T0)
        state["repositories"]["1"]["notes"] = "Keep my analysis"
        result = sync.merge_stars(state, [record(1, "new/renamed", T1)], T1)
        self.assertEqual(len(result["repositories"]), 1)
        self.assertEqual(result["repositories"]["1"]["analysis_status"], "done")
        self.assertEqual(result["repositories"]["1"]["notes"], "Keep my analysis")
        self.assertEqual(state["repositories"]["1"]["full_name"], "owner/one")

    def test_unstar_does_not_delete_or_reset_work(self):
        state = self.baseline()
        sync.set_status(state, "owner/one", "pending", T0)
        result = sync.merge_stars(state, [record(2, "other/two")], T1)
        self.assertEqual(len(result["repositories"]), 2)
        self.assertEqual(len(sync.pending_records(result)), 2)

    def test_manual_status_and_history_requeue(self):
        state = self.baseline()
        sync.set_status(state, "OWNER/ONE", "pending", T1)
        self.assertEqual(len(sync.pending_records(state)), 1)
        sync.set_status(state, "owner/one", "ignored", T1)
        self.assertEqual(sync.pending_records(state), [])
        with self.assertRaises(ValueError):
            sync.set_status(state, "owner/missing", "done", T1)
        with self.assertRaises(ValueError):
            sync.set_status(state, "owner/one", "bad", T1)

    def test_complete_pagination_and_id_dedup(self):
        pages = {1: ([item(), item(2, "other/two")], True),
                 2: ([item(2, "other/two"), item(3, "other/three")], False)}
        seen = []
        def page(number, token):
            seen.append(number)
            return pages[number]
        result = sync.fetch_stars("", page)
        self.assertEqual(seen, [1, 2])
        self.assertEqual(len(result), 3)

    def test_mid_pagination_error_and_empty_are_not_success(self):
        def broken(number, token):
            if number == 1:
                return [item()], True
            raise RuntimeError("network interrupted")
        with self.assertRaises(RuntimeError):
            sync.fetch_stars("", broken)
        with self.assertRaises(ValueError):
            sync.fetch_stars("", lambda *_: ([], False))
        with self.assertRaises(ValueError):
            sync.fetch_stars("", lambda *_: ([], True))

    def test_private_or_unknown_visibility_never_exported(self):
        self.assertIsNone(sync.normalize(item(private=True)))
        unknown = item()
        del unknown["repo"]["private"]
        self.assertIsNone(sync.normalize(unknown))
        result = sync.fetch_stars("", lambda *_: ([item(), item(2, "secret/repo", private=True)], False))
        self.assertEqual(len(result), 1)

    def test_invalid_api_identity_or_timestamp_rejected(self):
        for bad in [item(name="owner/repo?unsafe"), item(rid=True), item(when="not-a-date"), {}]:
            with self.assertRaises(ValueError):
                sync.normalize(bad)

    def test_corrupt_state_not_silently_reset(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "stars.json"
            self.assertIsNone(sync.load_state(path))
            for value in ["broken json", json.dumps({"schema_version": 9}),
                          json.dumps({**self.baseline(), "username": "someone-else"})]:
                path.write_text(value, encoding="utf-8")
                with self.assertRaises(ValueError):
                    sync.load_state(path)
                self.assertEqual(path.read_text(encoding="utf-8"), value)

    def test_outputs_are_utf8_idempotent_and_pending_matches(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state = sync.merge_stars(self.baseline(), [record(2, "other/two")], T1)
            state["repositories"]["2"]["description"] = "中文 | [不执行](javascript:x) <script>\n下一行"
            sync.save_outputs(root, state)
            files = [root / "data/stars.json", root / "data/pending.json", root / "PENDING.md"]
            stamps = [p.stat().st_mtime_ns for p in files]
            sync.save_outputs(root, copy.deepcopy(state))
            self.assertEqual(stamps, [p.stat().st_mtime_ns for p in files])
            self.assertEqual(sync.load_state(files[0]), state)
            pending = json.loads(files[1].read_text(encoding="utf-8"))
            self.assertEqual([r["repo_id"] for r in pending["items"]], [2])
            md = files[2].read_text(encoding="utf-8")
            self.assertIn("中文 &#124;", md)
            self.assertIn("&lt;script&gt;", md)
            self.assertNotIn("[不执行](", md)

    def test_noop_preserves_last_changed_at(self):
        state = self.baseline()
        result = sync.merge_stars(state, [record()], T1)
        self.assertEqual(result["last_changed_at"], T0)
        self.assertEqual(result, state)


if __name__ == "__main__":
    unittest.main()
