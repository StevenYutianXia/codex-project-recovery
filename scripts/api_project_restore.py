#!/usr/bin/env python3
"""Restore local Codex projects while the desktop app uses API-key authentication.

This is intentionally limited to desktop build 25894. The app's account-state
constructor clears account-scoped project data when its owner is null. API-key
authentication has no ChatGPT principal, so this restores the legacy unscoped
project data and retains the entire prior account state in the recovery run.
"""
import argparse
import copy
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

from project_recovery import (ACCOUNT, APP, Context, app_info, atomic_bytes, atomic_json,
                              collect, digest, identifier, load_snapshot, make_plan,
                              now, read_json, require, running, stable_exit)

BUILD = '25894'


def api_auth_active():
    bundled = Path(APP).parents[1] / 'Resources/codex-cli/CodexCLI.app/Contents/MacOS/codex'
    cli = str(bundled) if bundled.is_file() else shutil.which('codex')
    require(cli, '找不到 Codex CLI，无法核对 API 登录方式；未修改配置。')
    result = subprocess.run([cli, 'login', 'status'], capture_output=True, text=True, timeout=15)
    # Never print the status line: it may contain part of the API key.
    return result.returncode == 0 and result.stdout.startswith('API key configured')


def plan(ctx, state, snap):
    require(app_info()['build'] == BUILD, '桌面应用版本已变化，未修改配置。')
    require(api_auth_active(), '未确认当前是 API 密钥登录；未修改配置。')
    account = state.get(ACCOUNT)
    if account is None and state.get('local-projects'):
        projects = state['local-projects']
        assignments = state.get('thread-project-assignments', {})
        maps = state.get('app-server-project-id-by-legacy-project-id-by-host', {}).get(ctx.host, {})
        sidebar = state.get('electron-persisted-atom-state', {}).get('unified-sidebar-project-order-v1', [])
        require(all(projects.get(k, {}).get('name') == v['name'] and
                    projects.get(k, {}).get('rootPaths') == v['rootPaths']
                    for k, v in snap['projects'].items()) and
                all(assignments.get(k) == v for k, v in snap['assignments'].items()) and
                all(maps.get(k) == v for k, v in snap['server_map'].items()) and
                all('codex:project:' + k in sidebar for k in snap['projects']),
                'API 模式已有部分项目配置，但与备份不一致；请让 Codex 检查，未覆盖。')
        return state, {'project_count': len(snap['projects']),
                       'chat_association_count': len(snap['assignments']),
                       'skipped_missing_chats': 0, 'unavailable_project_paths': [],
                       'changes_needed': False}
    require(isinstance(account, dict) and account.get('owner') is None
            and isinstance(account.get('inactive'), dict), '当前不是已确认的 API 模式空账号分区。')
    require(not state.get('local-projects'), '当前已有本地项目，停止以免重复。')
    artificial = copy.deepcopy(state)
    artificial[ACCOUNT]['owner'] = 'api-local-plan-only'
    candidate, summary = make_plan(ctx, artificial, snap, 'api-local-plan-only')
    candidate.pop(ACCOUNT)
    require(all(candidate.get(k) == v for k, v in state.items() if k != ACCOUNT and k not in {
        'local-projects', 'thread-project-assignments', 'thread-project-membership-host-ids',
        'project-appearances', 'sidebar-project-thread-orders', 'project-order',
        'app-server-project-id-by-legacy-project-id-by-host',
        'app-server-projects-migration-by-host', 'electron-persisted-atom-state'}),
        '意外修改了无关配置。')
    require(ACCOUNT not in candidate and len(candidate['local-projects']) == summary['project_count'],
            '恢复结果结构不正确。')
    return candidate, summary


def status(run, phase, **extra):
    atomic_json(run / 'status.json', {'phase': phase, 'at': now(), 'pid': os.getpid(), **extra})


def worker(ctx, run):
    import fcntl
    with (ctx.data / 'recovery.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            status(run, 'waiting_for_app_exit')
            before = stable_exit(ctx, time.monotonic() + 7200)
            original = json.loads(before)
            snap = read_json(run / 'snapshot.json')
            candidate, summary = plan(ctx, original, snap)
            atomic_bytes(run / 'before.json', before)
            require(not running() and ctx.state.read_bytes() == before,
                    '应用已重新打开或配置仍在变化；未写入。')
            atomic_json(ctx.state, candidate)
            require(read_json(ctx.state) == candidate, '写入校验失败。')
            status(run, 'applied_needs_app_verification',
                   state_sha256=digest(ctx.state.read_bytes()), **summary)
            note = ('API 模式本地项目配置已写入并校验。\n现在可重新打开 Codex，'
                    '请继续在原聊天中让 Codex 核对项目列表和聊天归属。\n'
                    '如重开后未显示，保留本次运行目录中的 before.json 以便回退。\n'
                    '运行记录：' + str(run) + '\n')
            atomic_bytes(run / '完成说明.txt', note.encode())
            desktop = ctx.home / 'Desktop'
            if desktop.is_dir():
                atomic_bytes(desktop / ('Codex项目恢复完成-' + run.name + '.txt'), note.encode())
        except BaseException as exc:
            previous = read_json(run / 'status.json')
            if previous.get('phase') == 'applied_needs_app_verification':
                previous['notice_error'] = str(exc)
                atomic_json(run / 'status.json', previous)
            else:
                status(run, 'stopped', error=str(exc))
            raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['backup', 'plan', 'restore', 'status', '_worker'])
    parser.add_argument('--snapshot', type=Path, help='Use a specific verified local backup.')
    parser.add_argument('--run', type=Path)
    args = parser.parse_args()
    ctx = Context()
    ctx.data.mkdir(parents=True, exist_ok=True, mode=0o700)
    if args.command == '_worker':
        require(args.run is not None, '缺少运行目录。')
        worker(ctx, args.run)
        return
    if args.command == 'status':
        run = args.run or Path(read_json(ctx.data / 'latest-api-run.json')['path'])
        print(json.dumps({'run': str(run), **read_json(run / 'status.json')}, ensure_ascii=False, indent=2))
        return
    if args.command == 'backup':
        require(app_info()['build'] == BUILD, '桌面应用版本已变化，未备份；请先检查格式。')
        state = read_json(ctx.state)
        require(api_auth_active(), '未确认当前是 API 密钥登录；未备份。')
        require(ACCOUNT not in state and state.get('local-projects'),
                '请在 API 模式项目已恢复后备份；当前状态不适用。')
        artificial = copy.deepcopy(state)
        artificial[ACCOUNT] = {'owner': 'api-local-backup-only', 'inactive': {}}
        snap = collect(ctx, artificial, app_info(), 'api-local-backup-only')
        require(read_json(ctx.state) == state, '备份期间配置发生变化，请重试。')
        path = ctx.data / 'backups' / (identifier() + '.json')
        atomic_json(path, snap)
        atomic_json(ctx.data / 'latest-backup.json', {'path': str(path), 'sha256': digest(path.read_bytes())})
        print(json.dumps({'backup': str(path), 'project_count': len(snap['projects']),
                          'chat_association_count': len(snap['assignments'])},
                         ensure_ascii=False, indent=2))
        return
    snap = load_snapshot(ctx, args.snapshot)
    candidate, summary = plan(ctx, read_json(ctx.state), snap)
    if args.command == 'plan':
        print(json.dumps({'app': app_info(), 'candidate_project_count': len(candidate['local-projects']),
                          **summary}, ensure_ascii=False, indent=2))
        return
    if not summary['changes_needed']:
        print(json.dumps({'phase': 'already_present', **summary}, ensure_ascii=False, indent=2))
        return
    import fcntl
    with (ctx.data / 'recovery.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        run = ctx.data / 'runs' / ('api-' + identifier())
        run.mkdir(parents=True, mode=0o700)
        atomic_json(run / 'snapshot.json', snap)
        atomic_json(run / 'request.json', {'action': 'api_restore', 'build': BUILD})
        status(run, 'prepared', **summary)
        atomic_json(ctx.data / 'latest-api-run.json', {'path': str(run)})
    with (run / 'worker.log').open('ab') as log:
        os.chmod(run / 'worker.log', 0o600)
        child = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), '_worker', '--run', str(run)],
                                 stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True)
    print(json.dumps({'phase': 'prepared', 'run': str(run), 'pid': child.pid, **summary},
                     ensure_ascii=False, indent=2))


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print('未完成：' + str(exc), file=sys.stderr)
        sys.exit(1)
