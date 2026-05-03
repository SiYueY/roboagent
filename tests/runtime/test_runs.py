from __future__ import annotations

import unittest

from roboagent.runtime import RunManager, RunStatus


class RunManagerTests(unittest.TestCase):
    def test_create_and_update_run(self) -> None:
        manager = RunManager()

        record = manager.create(thread_id="thread-1", assistant_id="lead", run_id="run-1")
        updated = manager.set_status("run-1", RunStatus.RUNNING)

        self.assertEqual(record.run_id, "run-1")
        self.assertEqual(updated.status, RunStatus.RUNNING)
        self.assertEqual(manager.get("run-1"), updated)

    def test_duplicate_run_id_is_rejected(self) -> None:
        manager = RunManager()
        manager.create(thread_id="thread-1", run_id="run-1")

        with self.assertRaises(ValueError):
            manager.create(thread_id="thread-1", run_id="run-1")

    def test_list_by_thread(self) -> None:
        manager = RunManager()
        manager.create(thread_id="thread-1", run_id="run-1")
        manager.create(thread_id="thread-2", run_id="run-2")

        self.assertEqual([run.run_id for run in manager.list_by_thread("thread-1")], ["run-1"])


if __name__ == "__main__":
    unittest.main()
