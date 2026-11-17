"""Candidate database contract checks. No persistent database or credentials are created."""

from pathlib import Path
import sqlite3
import unittest


class DatabaseContractTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(":memory:")
        self.addCleanup(self.db.close)
        self.db.executescript(Path(__file__).with_name("database.sql").read_text())
        for n in (1, 2):
            self.db.execute("INSERT INTO sessions VALUES (?,?,?,?)", (f"s{n}", str(n) * 64, "now", "later"))
            self.db.execute("INSERT INTO tasks(id,owner_session_id,scenario,raw_text,fields_json,created_at,updated_at) VALUES (?,?, 'poster','text','{}','now','now')", (f"t{n}", f"s{n}"))

    def job(self, key="key", task="t1", owner="s1", job="j1", status="queued"):
        self.db.execute("INSERT INTO jobs(id,owner_session_id,task_id,kind,revision,snapshot_json,idempotency_key,request_hash,status,created_at) VALUES (?,?,?,'analyze',1,'{}',?,?,?,'now')", (job, owner, task, key, "a" * 64, status))

    def test_task_cannot_reference_another_sessions_asset(self):
        self.db.execute("INSERT INTO assets VALUES ('a2','s2','preview','a2.png','image/png',10,1,1,'now')")
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.execute("UPDATE tasks SET preview_asset_id='a2',preview_revision=1 WHERE id='t1'")

    def test_job_cannot_use_another_sessions_task(self):
        with self.assertRaises(sqlite3.IntegrityError):
            self.job(task="t2")

    def test_only_one_queued_or_running_job_per_task(self):
        self.job()
        with self.assertRaises(sqlite3.IntegrityError):
            self.job(key="another", job="j2", status="running")
        self.db.execute("UPDATE jobs SET status='succeeded' WHERE id='j1'")
        self.job(key="another", job="j2")

    def test_idempotency_key_unique_per_owner_not_global(self):
        self.job(status="succeeded")
        with self.assertRaises(sqlite3.IntegrityError):
            self.job(job="j2", status="succeeded")
        self.job(owner="s2", task="t2", job="j3")


if __name__ == "__main__":
    unittest.main()
