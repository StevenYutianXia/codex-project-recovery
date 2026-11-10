#!/usr/bin/env python3
"""Local-only, account-scoped Codex project backup and recovery.

Does not read auth files, copy chat bodies, write SQLite, or touch project files.
The desktop app must be fully closed before a configuration write.
"""
import argparse
import copy
from contextlib import closing
import datetime as dt
import fcntl
import hashlib
import json
import os
from pathlib import Path
import plistlib
import sqlite3
import subprocess
import sys
import time
import uuid

ACCOUNT = 'electron-account-renderer-state-v1'
ATOMS = 'electron-persisted-atom-state'
# Keep runtime data outside this source repository. Override paths for a
# nonstandard installation without changing the code.
APP = os.environ.get('CODEX_RECOVERY_APP', '/Applications/ChatGPT.app/Contents/MacOS/ChatGPT')
TESTED_BUILD = '25241'
SCHEMA = 'codex-local-project-backup-v1'


def require(ok, message):
    if not ok:
        raise ValueError(message)


def now():
    return dt.datetime.now().astimezone().isoformat()


def identifier():
    return dt.datetime.now().strftime('%Y%m%d-%H%M%S') + '-' + uuid.uuid4().hex[:8]


def digest(data):
    return hashlib.sha256(data).hexdigest()


def atomic_bytes(path, content):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name('.' + path.name + '-' + uuid.uuid4().hex)
    try:
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'wb') as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def atomic_json(path, obj):
    atomic_bytes(path, (json.dumps(obj, ensure_ascii=False, indent=2) + '\n').encode())


def read_json(path):
    return json.loads(Path(path).read_bytes())


def app_info():
    p = Path(APP).parents[1] / 'Info.plist'
    data = plistlib.loads(p.read_bytes())
    require(data.get('CFBundleIdentifier') == 'com.openai.codex', '应用身份不匹配，请先检查安装位置。')
    return {'build': str(data['CFBundleVersion']), 'version': data['CFBundleShortVersionString']}


def running():
    result = subprocess.run(['/bin/ps', '-axo', 'comm='], capture_output=True, text=True, check=True)
    return APP in (s.strip() for s in result.stdout.splitlines())


class Context:
    def __init__(self, home=None):
        self.home = Path(home) if home else Path.home()
        self.codex = (Path(os.environ['CODEX_HOME']).expanduser() if home is None and
                      os.environ.get('CODEX_HOME') else self.home / '.codex')
        self.state = self.codex / '.codex-global-state.json'
        self.db = self.codex / 'state_5.sqlite'
        self.data = (Path(os.environ['CODEX_RECOVERY_DATA']).expanduser() if home is None and
                     os.environ.get('CODEX_RECOVERY_DATA') else self.home / '.codex-project-recovery')
        self.host = 'local:' + str(self.codex)

    def database(self):
        require(self.db.is_file(), '本机聊天数据库不存在，停止恢复。')
        with closing(sqlite3.connect(self.db.as_uri() + '?mode=ro', uri=True)) as con:
            projects = dict(con.execute('SELECT id, name FROM projects'))
            threads = dict(con.execute('SELECT id, project_id FROM threads'))
        return projects, threads


def validate_state(state):
    require(isinstance(state, dict), '项目配置结构不受支持。')
    account = state.get(ACCOUNT)
    require(isinstance(account, dict) and set(account) == {'owner', 'inactive'},
            '账号配置结构已变化，请先检查新版本，未修改配置。')
    require(account['owner'] is None or isinstance(account['owner'], str), '账号标识格式不受支持。')
    require(isinstance(account['inactive'], dict), '账号分区格式不受支持。')
    return account


def get_scope(state, target):
    account = validate_state(state)
    require(account['owner'] in (target, None), '等待期间登录账号已改变；本次恢复已停止。')
    if account['owner'] == target:
        return state, state.setdefault(ATOMS, {})
    profile = account['inactive'].get(target)
    require(isinstance(profile, dict) and isinstance(profile.get('globals'), dict)
            and isinstance(profile.get('atoms'), dict), '退出后未找到目标账号配置，停止恢复。')
    return profile['globals'], profile['atoms']


def local_projects(globals_):
    raw = globals_.get('local-projects', {})
    require(isinstance(raw, dict), '项目列表格式已变化，停止恢复。')
    projects = {k: copy.deepcopy(v) for k, v in raw.items() if not k.startswith('g-p-')}
    for key, project in projects.items():
        require(isinstance(project, dict) and project.get('id') == key, '项目标识不匹配。')
        require(set(project) <= {'id', 'name', 'rootPaths', 'createdAt', 'updatedAt'},
                '项目定义含未知字段，请检查新版本。')
        require(isinstance(project.get('name'), str), '项目名称缺失。')
        roots = project.get('rootPaths')
        require(isinstance(roots, list) and roots and all(isinstance(p, str) and Path(p).is_absolute() for p in roots),
                '项目文件夹不是有效的绝对路径。')
    return projects


def collect(ctx, state, info, source=None):
    """Select only local references; inactive source selection is agent-only and explicit."""
    account = validate_state(state)
    owner = source or account['owner']
    require(isinstance(owner, str) and owner, '请先完成登录，再备份项目。')
    if owner == account['owner']:
        globals_ = state
    else:
        require(owner in account['inactive'], '指定的旧账号配置不存在。')
        globals_ = account['inactive'][owner]['globals']
    projects = local_projects(globals_)
    require(projects, '此账号没有本地项目；保留已有备份，不生成空备份。')
    server_projects, threads = ctx.database()
    maps = globals_.get('app-server-project-id-by-legacy-project-id-by-host', {}).get(ctx.host, {})
    require(all(pid in maps for pid in projects), '部分项目缺少已知本机对应记录，请先检查应用内项目。')
    maps = {pid: maps[pid] for pid in projects}
    require(len(set(maps.values())) == len(maps), '本机项目对应关系有重复，停止。')
    for pid, server in maps.items():
        require(server_projects.get(server) == projects[pid]['name'], '项目对应记录不一致：' + projects[pid]['name'])
    assignments = {}
    memberships = globals_.get('thread-project-membership-host-ids', {})
    for tid, assignment in globals_.get('thread-project-assignments', {}).items():
        require(isinstance(assignment, dict), '聊天项目归属格式已变化。')
        if assignment.get('projectKind') == 'local' and assignment.get('projectId') in projects and tid in threads:
            require(memberships.get(tid, 'local') == 'local', '发现其他执行主机的聊天，停止混合备份。')
            assignments[tid] = copy.deepcopy(assignment)
    inverse = {v: k for k, v in maps.items()}
    for tid, server in threads.items():
        if server in inverse:
            val = {'projectKind': 'local', 'projectId': inverse[server]}
            require(tid not in assignments or assignments[tid] == val, '数据库与账号内聊天归属不同，需先检查。')
            assignments[tid] = val
    order = [pid for pid in globals_.get('project-order', []) if pid in projects]
    order.extend(pid for pid in projects if pid not in order)
    return {'schema': SCHEMA, 'created_at': now(), 'codex_home': str(ctx.codex),
            'source_profile': owner, 'app': info, 'projects': projects, 'assignments': assignments,
            'server_map': maps, 'project_order': order,
            'appearances': {k: v for k, v in globals_.get('project-appearances', {}).items() if k in projects},
            'thread_orders': {k: v for k, v in globals_.get('sidebar-project-thread-orders', {}).items() if k in projects}}


def load_snapshot(ctx, path=None):
    if path is None:
        pointer = read_json(ctx.data / 'latest-backup.json')
        path = pointer['path']
        raw = Path(path).read_bytes()
        require(digest(raw) == pointer['sha256'], '备份校验不通过，停止恢复。')
    snap = read_json(path)
    require(snap.get('schema') == SCHEMA and snap.get('codex_home') == str(ctx.codex),
            '这份备份不属于本机，或格式不受支持。')
    require(snap.get('projects'), '备份中没有项目，停止恢复。')
    local_projects({'local-projects': snap['projects']})
    return snap


def merge_missing(target, key, values):
    if not values:
        return
    old = target.setdefault(key, {})
    require(isinstance(old, dict), '配置格式变化：' + key)
    for name, value in values.items():
        require(name not in old or old[name] == value, '现有配置冲突，未覆盖：' + key)
        old[name] = copy.deepcopy(value)


def make_plan(ctx, original, snap, target):
    require(snap.get('schema') == SCHEMA and snap.get('codex_home') == str(ctx.codex), '备份来源不匹配。')
    result = copy.deepcopy(original)
    dest, atoms = get_scope(result, target)
    require(isinstance(atoms, dict), '侧边栏配置格式已变化。')
    projects = local_projects({'local-projects': snap['projects']})
    require(projects, '备份项目为空。')
    existing = local_projects(dest)
    db_projects, db_threads = ctx.database()
    maps = snap['server_map']
    require(set(maps) == set(projects) and len(set(maps.values())) == len(maps), '备份内项目对应关系不完整。')
    for pid, item in projects.items():
        require(db_projects.get(maps[pid]) == item['name'], '数据库中的项目身份发生变化：' + item['name'])
        if pid in existing:
            require(existing[pid]['name'] == item['name'] and existing[pid]['rootPaths'] == item['rootPaths'],
                    '项目已更名或移动，需先确认：' + item['name'])
        for other_id, other in existing.items():
            require(other_id == pid or not set(other['rootPaths']).intersection(item['rootPaths']),
                    '同一文件夹已有不同的项目入口：' + item['name'])
    assignments = {}
    skipped = []
    for tid, val in snap['assignments'].items():
        require(isinstance(val, dict) and val.get('projectKind') == 'local' and val.get('projectId') in projects,
                '备份聊天归属不完整。')
        if tid not in db_threads:
            skipped.append(tid)
            continue
        require(db_threads[tid] in (None, maps[val['projectId']]), '某条聊天已属于其他项目，请先检查。')
        assignments[tid] = val
    merge_missing(dest, 'local-projects', {k: v for k, v in projects.items() if k not in existing})
    merge_missing(dest, 'thread-project-assignments', assignments)
    merge_missing(dest, 'thread-project-membership-host-ids', {tid: 'local' for tid in assignments})
    # Existing custom ordering/appearance wins; restoration fills missing entries only.
    for key, values in [('project-appearances', snap.get('appearances', {})),
                        ('sidebar-project-thread-orders', snap.get('thread_orders', {}))]:
        require(set(values) <= set(projects), '备份包含本地项目之外的侧边栏设置。')
        merge_missing(dest, key, {k: v for k, v in values.items() if k not in dest.get(key, {})})
    host_maps = dest.setdefault('app-server-project-id-by-legacy-project-id-by-host', {})
    merge_missing(host_maps, ctx.host, maps)
    migration = dest.setdefault('app-server-projects-migration-by-host', {})
    old_migration = migration.get(ctx.host, {})
    require(not old_migration or (old_migration.get('version') == 1 and
            set(old_migration) <= {'version', 'projectsMigrated', 'threadAssignmentsMigrated'}),
            '应用的数据迁移格式已变化，停止恢复。')
    order = dest.setdefault('project-order', [])
    sidebar = atoms.setdefault('unified-sidebar-project-order-v1', [])
    require(isinstance(order, list) and isinstance(sidebar, list), '项目排序格式已变化。')
    for pid in snap['project_order']:
        require(pid in projects, '备份中的项目排序不匹配。')
        if pid not in order:
            order.append(pid)
        key = 'codex:project:' + pid
        if key not in sidebar:
            sidebar.append(key)
    atoms['sidebar-project-list-expanded-v1'] = True
    changed = result != original
    if changed:
        migration[ctx.host] = {'version': 1, 'projectsMigrated': True, 'threadAssignmentsMigrated': False}
    # Whole-result comparison also protects all unrelated globals and account profiles.
    allowed = {'local-projects', 'thread-project-assignments', 'thread-project-membership-host-ids',
               'project-appearances', 'sidebar-project-thread-orders', 'project-order',
               'app-server-project-id-by-legacy-project-id-by-host', 'app-server-projects-migration-by-host', ATOMS}
    if original[ACCOUNT]['owner'] == target:
        require(result[ACCOUNT] == original[ACCOUNT], '意外修改了其他账号配置。')
        require(all(result[k] == v for k, v in original.items() if k not in allowed), '意外修改了无关配置。')
    else:
        require(all(result[k] == v for k, v in original.items() if k != ACCOUNT), '意外修改了退出后的默认配置。')
        require(all(result[ACCOUNT]['inactive'][k] == v for k, v in original[ACCOUNT]['inactive'].items() if k != target),
                '意外修改了其他账号配置。')
    missing = [v['name'] for v in projects.values() if not all(Path(p).is_dir() for p in v['rootPaths'])]
    return result, {'project_count': len(projects), 'chat_association_count': len(assignments),
                    'skipped_missing_chats': len(skipped), 'unavailable_project_paths': missing,
                    'changes_needed': result != original}


def stable_exit(ctx, deadline):
    while time.monotonic() < deadline:
        if running():
            time.sleep(0.25)
            continue
        data = ctx.state.read_bytes()
        quiet = True
        for _ in range(8):
            time.sleep(0.25)
            if running() or ctx.state.read_bytes() != data:
                quiet = False
                break
        if quiet:
            return data
    raise TimeoutError('等待完全退出应用超过两小时；未写入配置。')


def set_status(run, phase, **extra):
    atomic_json(run / 'status.json', {'phase': phase, 'pid': os.getpid(), 'at': now(), **extra})


def apply_once(ctx, run, request, before):
    require(not running() and ctx.state.read_bytes() == before, '应用重新打开或配置仍在变化，请重试。')
    original = json.loads(before)
    if request['action'] == 'restore':
        updated, summary = make_plan(ctx, original, read_json(run / 'snapshot.json'), request['target_profile'])
    else:
        source_run = Path(request['source_run'])
        prior = read_json(source_run / 'status.json')
        require(digest(before) == prior['state_sha256'], '恢复后配置已有新变化，不能整体回退，请交给 Codex 检查。')
        get_scope(copy.deepcopy(original), request['target_profile'])
        updated = read_json(source_run / 'before.json')
        summary = {'rollback_of': str(source_run)}
    atomic_bytes(run / 'before.json', before)
    require(not running() and ctx.state.read_bytes() == before, '应用重新打开或配置仍在变化，未写入。')
    atomic_json(ctx.state, updated)
    require(read_json(ctx.state) == updated, '写入后校验失败，请保留备份并检查。')
    set_status(run, 'applied_needs_app_verification', state_sha256=digest(ctx.state.read_bytes()), **summary)
    note = ('项目配置已写入并校验。\n现在可以重新打开 Codex / ChatGPT，保持当前账号登录。\n'
            '请让 Codex 检查项目列表、原有聊天归属和硬盘路径；完成该检查后才算恢复成功。\n'
            '运行记录：' + str(run) + '\n')
    atomic_bytes(run / '完成说明.txt', note.encode())
    desktop = ctx.home / 'Desktop'
    if desktop.is_dir():
        atomic_bytes(desktop / ('Codex项目恢复完成-' + run.name + '.txt'), note.encode())


def worker(ctx, run):
    request = read_json(run / 'request.json')
    with (ctx.data / 'recovery.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            set_status(run, 'waiting_for_app_exit')
            before = stable_exit(ctx, time.monotonic() + 7200)
            require(app_info()['build'] == TESTED_BUILD, '应用版本已变化，请先检查恢复流程兼容性。')
            apply_once(ctx, run, request, before)
        except BaseException as exc:
            # Never label an already-applied write as unmodified if only notice creation failed.
            old = read_json(run / 'status.json')
            if old.get('phase') == 'applied_needs_app_verification':
                old['notice_error'] = str(exc)
                atomic_json(run / 'status.json', old)
            else:
                set_status(run, 'stopped', error=str(exc))
            raise


def prepare(ctx, snap, target, action='restore', source_run=None):
    require(app_info()['build'] == TESTED_BUILD, '应用版本已更新，请让 Codex 先检查本流程兼容性；当前配置未修改。')
    if action == 'restore':
        _, summary = make_plan(ctx, read_json(ctx.state), snap, target)
        if not summary['changes_needed']:
            return {'phase': 'already_present', **summary}
    else:
        summary = {'rollback_of': str(source_run)}
    # A lock probe prevents overlapping jobs; worker holds the same lock until completion.
    with (ctx.data / 'recovery.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        pointer = ctx.data / 'latest-run.json'
        if pointer.exists():
            prior_run = Path(read_json(pointer)['path'])
            prior_status = read_json(prior_run / 'status.json')
            if prior_status.get('phase') in ('prepared', 'waiting_for_app_exit'):
                try:
                    os.kill(prior_status['pid'], 0)
                except ProcessLookupError:
                    pass
                else:
                    raise ValueError('已有恢复程序正在等待，请先查看恢复状态。')
        run = ctx.data / 'runs' / identifier()
        run.mkdir(parents=True, mode=0o700)
        atomic_json(run / 'snapshot.json', snap)
        atomic_json(run / 'request.json', {'action': action, 'target_profile': target, 'source_run': str(source_run) if source_run else None})
        set_status(run, 'prepared', **summary)
        atomic_json(ctx.data / 'latest-run.json', {'path': str(run)})
    with (run / 'worker.log').open('ab') as log:
        os.chmod(run / 'worker.log', 0o600)
        child = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), '_worker', '--run', str(run)],
                                 stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True)
    return {'phase': 'prepared', 'run': str(run), 'pid': child.pid, **summary,
            'next': '请从屏幕顶部菜单完全退出 ChatGPT / Codex；不要只关闭窗口。等桌面出现本次完成说明，再重新打开应用检查。'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['inspect', 'backup', 'plan', 'restore', 'status', 'verify', 'rollback', '_worker'])
    parser.add_argument('--snapshot', type=Path)
    parser.add_argument('--source-profile', help='Explicit inactive local profile for backup only; opaque ID, not an account login.')
    parser.add_argument('--run', type=Path)
    args = parser.parse_args()
    ctx = Context()
    ctx.data.mkdir(parents=True, exist_ok=True, mode=0o700)
    if args.command == '_worker':
        require(args.run is not None, '缺少运行记录。')
        worker(ctx, args.run)
        return
    if args.command == 'status':
        run = args.run or Path(read_json(ctx.data / 'latest-run.json')['path'])
        print(json.dumps({'run': str(run), **read_json(run / 'status.json')}, ensure_ascii=False, indent=2))
        return
    state = read_json(ctx.state)
    if args.command == 'inspect' and ACCOUNT not in state:
        print(json.dumps({'app': app_info(), 'running': running(), 'signed_in': None,
                          'account_scoped': False,
                          'authentication': 'Check codex login status; no credential was read.',
                          'active_local_projects': len(local_projects(state)),
                          'inactive_profiles': []}, ensure_ascii=False, indent=2))
        return
    account = validate_state(state)
    owner = account['owner']
    if args.command == 'inspect':
        print(json.dumps({'app': app_info(), 'running': running(), 'signed_in': owner is not None,
                          'active_local_projects': len(local_projects(state)),
                          'inactive_profiles': [{'profile': key, 'local_projects': len(local_projects(value['globals']))}
                                                for key, value in account['inactive'].items()]}, ensure_ascii=False, indent=2))
        return
    if args.command == 'backup':
        snap = collect(ctx, state, app_info(), args.source_profile)
        # Detect an account/config switch during database reads; retry by invoking again.
        require(read_json(ctx.state) == state, '备份期间配置发生变化，请再运行一次。')
        path = ctx.data / 'backups' / (identifier() + '.json')
        atomic_json(path, snap)
        atomic_json(ctx.data / 'latest-backup.json', {'path': str(path), 'sha256': digest(path.read_bytes())})
        print(json.dumps({'backup': str(path), 'project_count': len(snap['projects']),
                          'chat_association_count': len(snap['assignments'])}, ensure_ascii=False, indent=2))
        return
    require(isinstance(owner, str) and owner, '请先登录希望使用的目标账号，再执行此操作。')
    if args.command == 'rollback':
        require(args.run is not None, '回退需要指定之前的运行记录。')
        request = read_json(args.run / 'request.json')
        require(owner == request['target_profile'], '当前账号与原恢复账号不同，停止回退。')
        prior = read_json(args.run / 'status.json')
        require(prior.get('phase') == 'applied_needs_app_verification', '这次运行没有可回退的成功写入。')
        require(digest(ctx.state.read_bytes()) == prior['state_sha256'], '配置已有后续变化，请交给 Codex 检查，未回退。')
        print(json.dumps(prepare(ctx, {}, owner, 'rollback', args.run), ensure_ascii=False, indent=2))
        return
    snap = load_snapshot(ctx, args.snapshot)
    _, summary = make_plan(ctx, state, snap, owner)
    if args.command == 'restore':
        summary = prepare(ctx, snap, owner)
    elif args.command == 'verify':
        summary['configuration_complete'] = not summary['changes_needed']
        summary['application_verification_required'] = True
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print('未完成：' + str(exc), file=sys.stderr)
        sys.exit(1)
