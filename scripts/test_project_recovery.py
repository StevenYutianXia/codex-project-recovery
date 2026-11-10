"""Behavior tests using isolated fake homes and SQLite databases only."""
import copy
from contextlib import closing
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import time
import unittest
from unittest.mock import patch

import project_recovery as r


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.ctx = r.Context(self.temp.name)
        self.ctx.codex.mkdir()
        self.ctx.data.mkdir(parents=True)
        self.root = str(Path(self.temp.name) / 'offline-volume/course')
        self.project = {'id': 'course', 'name': 'Course', 'rootPaths': [self.root], 'createdAt': 1, 'updatedAt': 1}
        with closing(sqlite3.connect(self.ctx.db)) as con, con:
            con.executescript('CREATE TABLE projects(id TEXT PRIMARY KEY, name TEXT);'
                              'CREATE TABLE threads(id TEXT PRIMARY KEY, project_id TEXT);')
            con.executemany('INSERT INTO projects VALUES(?, ?)', [('server-course', 'Course'), ('server-new', 'New')])
            con.executemany('INSERT INTO threads VALUES(?, ?)', [('chat1', None), ('chat2', None)])
        self.state = {r.ACCOUNT: {'owner': 'account-a', 'inactive': {'unused': {'globals': {'keep': 4}, 'atoms': {}}}},
                      'local-projects': {'course': self.project, 'g-p-cloud': {'ignored': True}},
                      'thread-project-assignments': {'chat1': {'projectKind': 'local', 'projectId': 'course'}},
                      'app-server-project-id-by-legacy-project-id-by-host': {self.ctx.host: {'course': 'server-course'}},
                      'project-order': ['g-p-cloud', 'course'], r.ATOMS: {}, 'unrelated': {'keep': 'value'}}
        self.snapshot = r.collect(self.ctx, self.state, {'build': r.TESTED_BUILD})

    def target(self):
        new = {'id': 'new', 'name': 'New', 'rootPaths': [str(Path(self.temp.name) / 'new')]}
        return {r.ACCOUNT: {'owner': 'account-b', 'inactive': {'account-a': {'globals': self.state, 'atoms': {'keep': 1}}}},
                r.ATOMS: {'theme': 'keep'}, 'local-projects': {'new': new},
                'thread-project-assignments': {'chat2': {'projectKind': 'local', 'projectId': 'new'}},
                'unrelated': {'keep': True}, 'project-order': ['new']}

    def test_snapshot_excludes_cloud_and_unrelated_values(self):
        self.assertEqual(set(self.snapshot['projects']), {'course'})
        self.assertNotIn('unrelated', self.snapshot)
        self.assertNotIn(r.ACCOUNT, self.snapshot)

    def test_merge_preserves_target_other_accounts_and_offline_paths(self):
        original = self.target()
        updated, summary = r.make_plan(self.ctx, original, self.snapshot, 'account-b')
        self.assertEqual(updated[r.ACCOUNT], original[r.ACCOUNT])
        self.assertEqual(updated['unrelated'], original['unrelated'])
        self.assertEqual(updated[r.ATOMS]['theme'], 'keep')
        self.assertEqual(set(updated['local-projects']), {'course', 'new'})
        self.assertEqual(set(updated['thread-project-assignments']), {'chat1', 'chat2'})
        self.assertEqual(updated['project-order'], ['new', 'course'])
        self.assertEqual(summary['unavailable_project_paths'], ['Course'])
        self.assertFalse(Path(self.root).exists())

    def test_repeat_recovery_is_idempotent(self):
        once, _ = r.make_plan(self.ctx, self.target(), self.snapshot, 'account-b')
        twice, summary = r.make_plan(self.ctx, once, self.snapshot, 'account-b')
        self.assertEqual(once, twice)
        self.assertFalse(summary['changes_needed'])

    def test_logged_out_target_uses_inactive_partition_only(self):
        state = self.target()
        state[r.ACCOUNT]['owner'] = None
        state[r.ACCOUNT]['inactive']['account-b'] = {'globals': {}, 'atoms': {}}
        updated, _ = r.make_plan(self.ctx, state, self.snapshot, 'account-b')
        for k in state:
            if k != r.ACCOUNT:
                self.assertEqual(updated[k], state[k])
        self.assertIn('course', updated[r.ACCOUNT]['inactive']['account-b']['globals']['local-projects'])
        self.assertEqual(updated[r.ACCOUNT]['inactive']['account-a'], state[r.ACCOUNT]['inactive']['account-a'])

    def test_switch_to_unexpected_account_stops(self):
        with self.assertRaisesRegex(ValueError, '账号已改变'):
            r.make_plan(self.ctx, self.target(), self.snapshot, 'account-c')

    def test_existing_chat_assignment_conflict_stops(self):
        state = self.target()
        state['thread-project-assignments']['chat1'] = {'projectKind': 'local', 'projectId': 'new'}
        before = copy.deepcopy(state)
        with self.assertRaisesRegex(ValueError, '冲突'):
            r.make_plan(self.ctx, state, self.snapshot, 'account-b')
        self.assertEqual(state, before)

    def test_same_path_different_project_stops(self):
        state = self.target()
        state['local-projects']['new']['rootPaths'] = [self.root]
        with self.assertRaisesRegex(ValueError, '同一文件夹'):
            r.make_plan(self.ctx, state, self.snapshot, 'account-b')

    def test_deleted_chat_is_reported_not_recreated(self):
        with closing(sqlite3.connect(self.ctx.db)) as con, con:
            con.execute('DELETE FROM threads WHERE id="chat1"')
        updated, summary = r.make_plan(self.ctx, self.target(), self.snapshot, 'account-b')
        self.assertEqual(summary['skipped_missing_chats'], 1)
        self.assertNotIn('chat1', updated['thread-project-assignments'])

    def test_newer_database_assignment_is_used_for_backup(self):
        with closing(sqlite3.connect(self.ctx.db)) as con, con:
            con.execute('UPDATE threads SET project_id="server-course" WHERE id="chat2"')
        snap = r.collect(self.ctx, self.state, {'build': r.TESTED_BUILD})
        self.assertEqual(set(snap['assignments']), {'chat1', 'chat2'})

    def test_empty_backup_refused(self):
        state = self.target()
        state['local-projects'] = {}
        with self.assertRaisesRegex(ValueError, '空备份'):
            r.collect(self.ctx, state, {'build': r.TESTED_BUILD})

    def test_changed_schema_refused(self):
        state = self.target()
        state[r.ACCOUNT]['newField'] = True
        with self.assertRaisesRegex(ValueError, '结构已变化'):
            r.make_plan(self.ctx, state, self.snapshot, 'account-b')

    def test_apply_backup_permissions_and_database_untouched(self):
        state = self.target()
        r.atomic_json(self.ctx.state, state)
        before = self.ctx.state.read_bytes()
        db_before = self.ctx.db.read_bytes()
        run = self.ctx.data / 'runs/test'
        run.mkdir(parents=True)
        r.atomic_json(run / 'snapshot.json', self.snapshot)
        with patch.object(r, 'running', return_value=False):
            r.apply_once(self.ctx, run, {'action': 'restore', 'target_profile': 'account-b'}, before)
        self.assertEqual((run / 'before.json').read_bytes(), before)
        self.assertEqual(os.stat(run / 'before.json').st_mode & 0o777, 0o600)
        self.assertIn('course', r.read_json(self.ctx.state)['local-projects'])
        self.assertEqual(db_before, self.ctx.db.read_bytes())
        self.assertEqual(r.read_json(run / 'status.json')['phase'], 'applied_needs_app_verification')

    def test_running_app_prevents_any_write(self):
        r.atomic_json(self.ctx.state, self.target())
        before = self.ctx.state.read_bytes()
        with patch.object(r, 'running', return_value=True):
            with self.assertRaises(ValueError):
                r.apply_once(self.ctx, self.ctx.data / 'run', {}, before)
        self.assertEqual(before, self.ctx.state.read_bytes())

    def test_configuration_race_prevents_write(self):
        r.atomic_json(self.ctx.state, self.target())
        before = self.ctx.state.read_bytes()
        r.atomic_json(self.ctx.state, {'new': 'state'})
        with patch.object(r, 'running', return_value=False):
            with self.assertRaises(ValueError):
                r.apply_once(self.ctx, self.ctx.data / 'run', {}, before)
        self.assertEqual(r.read_json(self.ctx.state), {'new': 'state'})

    def test_quick_reopen_is_retried(self):
        r.atomic_json(self.ctx.state, self.target())
        # First quiet interval is interrupted; second one completes.
        seq = [False, False, True, True, False] + [False] * 8
        with patch.object(r, 'running', side_effect=seq), patch.object(r.time, 'sleep'):
            got = r.stable_exit(self.ctx, time.monotonic() + 10)
        self.assertEqual(got, self.ctx.state.read_bytes())

    def test_expired_wait_does_not_write(self):
        r.atomic_json(self.ctx.state, self.target())
        before = self.ctx.state.read_bytes()
        with self.assertRaises(TimeoutError):
            r.stable_exit(self.ctx, time.monotonic() - 1)
        self.assertEqual(before, self.ctx.state.read_bytes())

    def test_unreviewed_app_version_refuses_job(self):
        with patch.object(r, 'app_info', return_value={'build': 'future-version'}):
            with self.assertRaisesRegex(ValueError, '版本已更新'):
                r.prepare(self.ctx, self.snapshot, 'account-b')
        self.assertFalse((self.ctx.data / 'latest-run.json').exists())

    def test_worker_finishes_in_fake_home(self):
        r.atomic_json(self.ctx.state, self.target())
        run = self.ctx.data / 'runs/worker'
        run.mkdir(parents=True)
        r.atomic_json(run / 'request.json', {'action': 'restore', 'target_profile': 'account-b'})
        r.atomic_json(run / 'snapshot.json', self.snapshot)
        with patch.object(r, 'running', return_value=False), patch.object(r.time, 'sleep'), \
                patch.object(r, 'app_info', return_value={'build': r.TESTED_BUILD}):
            r.worker(self.ctx, run)
        self.assertEqual(r.read_json(run / 'status.json')['phase'], 'applied_needs_app_verification')
        self.assertEqual(r.read_json(self.ctx.state)['local-projects']['course'], self.project)

    def test_rollback_requires_unchanged_post_restore_state(self):
        r.atomic_json(self.ctx.state, self.target())
        before = self.ctx.state.read_bytes()
        first = self.ctx.data / 'runs/first'
        first.mkdir(parents=True)
        r.atomic_json(first / 'snapshot.json', self.snapshot)
        with patch.object(r, 'running', return_value=False):
            r.apply_once(self.ctx, first, {'action': 'restore', 'target_profile': 'account-b'}, before)
            current = self.ctx.state.read_bytes()
            request = {'action': 'rollback', 'target_profile': 'account-b', 'source_run': str(first)}
            second = self.ctx.data / 'runs/second'
            r.apply_once(self.ctx, second, request, current)
        self.assertEqual(r.read_json(self.ctx.state), json.loads(before))
        r.atomic_json(self.ctx.state, self.target())
        with patch.object(r, 'running', return_value=False):
            with self.assertRaisesRegex(ValueError, '新变化'):
                r.apply_once(self.ctx, self.ctx.data / 'runs/third', request, self.ctx.state.read_bytes())


if __name__ == '__main__':
    unittest.main(verbosity=2)
