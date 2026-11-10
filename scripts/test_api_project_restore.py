"""API-mode recovery tests; all state and database files live in a temp home."""
from contextlib import closing
import copy
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

import api_project_restore as api
import project_recovery as core


class ApiRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.ctx = core.Context(self.temp.name)
        self.ctx.codex.mkdir()
        self.ctx.data.mkdir()
        path = str(Path(self.temp.name) / 'course')
        project = {'id': 'course', 'name': 'Course', 'rootPaths': [path]}
        with closing(sqlite3.connect(self.ctx.db)) as con, con:
            con.executescript('CREATE TABLE projects(id TEXT PRIMARY KEY, name TEXT);'
                              'CREATE TABLE threads(id TEXT PRIMARY KEY, project_id TEXT);')
            con.execute('INSERT INTO projects VALUES(?, ?)', ('server-course', 'Course'))
            con.execute('INSERT INTO threads VALUES(?, ?)', ('chat1', None))
        source = {core.ACCOUNT: {'owner': 'old-account', 'inactive': {}},
                  'local-projects': {'course': project},
                  'thread-project-assignments': {'chat1': {'projectKind': 'local', 'projectId': 'course'}},
                  'app-server-project-id-by-legacy-project-id-by-host': {
                      self.ctx.host: {'course': 'server-course'}},
                  'project-order': ['course']}
        self.snapshot = core.collect(self.ctx, source, {'build': api.BUILD})
        self.empty_api = {core.ACCOUNT: {'owner': None, 'inactive': {
            'old-account': {'globals': source, 'atoms': {}}}},
            core.ATOMS: {'theme': 'keep'}, 'unrelated': {'keep': True}}

    def test_restores_unscoped_projects_and_preserves_other_state(self):
        original = copy.deepcopy(self.empty_api)
        with patch.object(api, 'app_info', return_value={'build': api.BUILD}), \
                patch.object(api, 'api_auth_active', return_value=True):
            result, summary = api.plan(self.ctx, original, self.snapshot)
            again, repeat = api.plan(self.ctx, result, self.snapshot)
        self.assertNotIn(core.ACCOUNT, result)
        self.assertEqual(result['unrelated'], original['unrelated'])
        self.assertEqual(result[core.ATOMS]['theme'], 'keep')
        self.assertEqual(result['thread-project-assignments']['chat1']['projectId'], 'course')
        self.assertTrue(summary['changes_needed'])
        self.assertIs(result, again)
        self.assertFalse(repeat['changes_needed'])

    def test_logged_out_state_is_not_treated_as_api_login(self):
        with patch.object(api, 'app_info', return_value={'build': api.BUILD}), \
                patch.object(api, 'api_auth_active', return_value=False):
            with self.assertRaisesRegex(ValueError, 'API 密钥登录'):
                api.plan(self.ctx, self.empty_api, self.snapshot)

    def test_unreviewed_build_stops_before_modification(self):
        original = copy.deepcopy(self.empty_api)
        with patch.object(api, 'app_info', return_value={'build': 'future'}):
            with self.assertRaisesRegex(ValueError, '版本已变化'):
                api.plan(self.ctx, original, self.snapshot)
        self.assertEqual(original, self.empty_api)

    def test_partial_existing_data_is_not_overwritten(self):
        state = copy.deepcopy(self.empty_api)
        state.pop(core.ACCOUNT)
        state['local-projects'] = {'other': {'name': 'Other'}}
        with patch.object(api, 'app_info', return_value={'build': api.BUILD}), \
                patch.object(api, 'api_auth_active', return_value=True):
            with self.assertRaisesRegex(ValueError, '不一致'):
                api.plan(self.ctx, state, self.snapshot)


if __name__ == '__main__':
    unittest.main()
