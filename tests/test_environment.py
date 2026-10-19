"""Environment selection is explicit and never installs into an accidental Python."""
from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import zipfile

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts' / 'manage.py'
_spec = importlib.util.spec_from_file_location('inkreference_manage', SCRIPT)
manage = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = manage
_spec.loader.exec_module(manage)


def args(action='open', *options):
    return manage.parser().parse_args([action, *options])


def details(**extra):
    return {'version': [3, 12, 0], 'pip': True, 'app': True, 'desktop': True, 'missing': [], **extra}


def source_tree(root):
    for name in ('pyproject.toml', 'requirements-lock.txt', 'frontend/package-lock.json', 'frontend/dist/index.html'):
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('{}' if name.endswith('json') else 'fixture', encoding='utf-8')


def test_default_environment_is_project_venv_even_with_an_active_environment(tmp_path, monkeypatch):
    target = manage.venv_python(tmp_path)
    target.parent.mkdir(parents=True)
    target.touch()
    monkeypatch.setenv('CONDA_PREFIX', str(tmp_path / 'developer conda'))
    monkeypatch.setenv('VIRTUAL_ENV', str(tmp_path / 'active other environment'))
    seen = []
    monkeypatch.setattr(manage, 'probe', lambda runtime: seen.append(runtime.executable) or details())
    selected = manage.resolve_runtime(args(), tmp_path)
    assert selected.executable == str(target)
    assert seen == [str(target)]


def test_current_environment_explicitly_bypasses_project_venv(tmp_path):
    target = manage.venv_python(tmp_path)
    target.parent.mkdir(parents=True)
    target.write_text('not an executable', encoding='utf-8')
    runtime = manage.resolve_runtime(args('open', '--current-environment'), tmp_path)
    assert runtime.executable == sys.executable


def test_missing_venv_never_falls_back_to_global_python(tmp_path):
    with pytest.raises(manage.EnvironmentError, match='install command first'):
        manage.resolve_runtime(args(), tmp_path)
    assert not (tmp_path / '.venv').exists()


def test_real_venv_with_unicode_spaces_and_missing_pip(tmp_path):
    root = tmp_path / '中文 project with spaces'
    root.mkdir()
    target = manage.venv_python(root)
    subprocess.run([sys.executable, '-m', 'venv', '--without-pip', str(root / '.venv')], check=True, capture_output=True)
    runtime = manage.resolve_runtime(args(), root)
    inspected = manage.probe(runtime)
    assert Path(inspected['executable']) == target
    assert inspected['prefix'] != inspected['base_prefix']
    assert inspected['pip'] is False
    with pytest.raises(manage.EnvironmentError, match='pip is missing'):
        manage.checked_runtime(runtime, needs_pip=True)


def test_unsupported_environment_is_diagnosed_without_modification(tmp_path, monkeypatch):
    target = manage.venv_python(tmp_path)
    target.parent.mkdir(parents=True)
    target.write_text('preserve this environment', encoding='utf-8')
    monkeypatch.setattr(manage, 'probe', lambda runtime: details(version=[3, 14, 0]))
    with pytest.raises(manage.EnvironmentError, match='was not changed'):
        manage.resolve_runtime(args(), tmp_path)
    assert target.read_text() == 'preserve this environment'


def test_explicit_python_does_not_silently_select_another_version(monkeypatch):
    monkeypatch.setattr(manage, 'probe', lambda runtime: details(version=[3, 10, 0]))
    with pytest.raises(manage.EnvironmentError, match='Python 3.10.0'):
        manage.creation_python(sys.executable)


def test_mutually_exclusive_environment_options():
    with pytest.raises(SystemExit):
        args('install', '--current-environment', '--conda-environment', 'InkReference')
    with pytest.raises(SystemExit):
        args('open', '--python', sys.executable, '--current-environment')


def test_runtime_preserves_argument_boundaries_and_does_not_change_process_environment(monkeypatch):
    monkeypatch.setenv('PYTHONHOME', 'unrelated global runtime')
    runtime = manage.Runtime('/Python with spaces/python', label='explicit')
    assert runtime.command('-m', 'inkreference', '--data-dir', '/中文 data/a & b') == [
        '/Python with spaces/python', '-m', 'inkreference', '--data-dir', '/中文 data/a & b',
    ]
    assert 'PYTHONHOME' not in runtime.environment()
    assert os.environ['PYTHONHOME'] == 'unrelated global runtime'


def test_missing_node_reports_action_before_creating_environment(tmp_path, monkeypatch):
    source_tree(tmp_path)
    monkeypatch.setattr(manage, 'creation_python', lambda value: manage.Runtime(sys.executable))
    monkeypatch.setattr(manage.shutil, 'which', lambda name: None)
    calls = []
    monkeypatch.setattr(manage, 'run_phase', lambda *a, **kw: calls.append(a))
    with pytest.raises(manage.EnvironmentError, match='Node.js 22 or 24'):
        manage.install(args('install'), tmp_path)
    assert calls == []
    assert not (tmp_path / '.venv').exists()


@pytest.mark.parametrize('version', ['v20.20.0', 'v23.0.0', 'v25.1.0', 'invalid'])
def test_incompatible_node_requires_user_selected_toolchain(version, monkeypatch):
    monkeypatch.setattr(manage.shutil, 'which', lambda name: '/tools/' + name)
    monkeypatch.setattr(manage, 'capture', lambda command, **kw: version if 'node' in command[0] else '11.0.0')
    with pytest.raises(manage.EnvironmentError, match='no global tools were changed'):
        manage.frontend_tools(manage.Runtime(sys.executable))


def test_conda_is_only_consulted_when_explicit_and_missing_name_is_not_created(tmp_path, monkeypatch):
    conda = tmp_path / 'Conda tools' / 'conda'
    conda.parent.mkdir()
    conda.touch()
    monkeypatch.setenv('CONDA_EXE', str(conda))
    commands = []
    monkeypatch.setattr(manage, 'capture', lambda command, **kw: commands.append(command) or json.dumps({'envs': [str(tmp_path / 'base')]}))
    with pytest.raises(manage.EnvironmentError, match='does not exist'):
        manage.resolve_conda('MissingEnvironment')
    assert commands == [[str(conda), 'env', 'list', '--json']]


def test_existing_conda_environment_uses_conda_run_without_activate_or_changes(tmp_path, monkeypatch):
    conda = tmp_path / 'Conda tools' / 'conda'
    conda.parent.mkdir()
    conda.touch()
    monkeypatch.setenv('CONDA_EXE', str(conda))
    monkeypatch.setattr(manage, 'capture', lambda command, **kw: json.dumps({'envs': [str(tmp_path / 'envs/InkReference')]}))
    runtime = manage.resolve_conda('InkReference')
    assert runtime.command('-m', 'pip', '--version') == [str(conda), 'run', '--no-capture-output', '-n', 'InkReference', 'python', '-m', 'pip', '--version']
    assert runtime.tool_command('npm', 'ci') == [str(conda), 'run', '--no-capture-output', '-n', 'InkReference', 'npm', 'ci']


def test_install_targets_selected_environment_and_isolates_optional_mineru_constraints(tmp_path, monkeypatch):
    root = tmp_path / '中文 project with spaces'
    frontend_source(root)
    (root / 'requirements-mineru.txt').write_text('mineru==4.0.12')
    runtime = manage.Runtime('/selected python/python', label='explicit developer environment')
    monkeypatch.setattr(manage, 'resolve_runtime', lambda *a: runtime)
    monkeypatch.setattr(manage, 'checked_runtime', lambda *a, **kw: runtime)
    monkeypatch.setattr(manage, 'frontend_tools', lambda selected: (['node'], ['npm'], 'v24.0.0'))
    monkeypatch.setattr(manage, 'repair_packaging', lambda selected, project_root: False)
    calls = []
    monkeypatch.setattr(manage, 'run_phase', lambda command, description, **kw: calls.append((command, kw)))
    assert manage.install(args('install', '--current-environment', '--mineru', '--dev'), root) == 0
    assert len(calls) == 4
    base, mineru, npm_install, npm_build = [call[0] for call in calls]
    assert base == runtime.command('-m', 'pip', 'install', '-c', str(root / 'requirements-lock.txt'), '-e', '.[dev,desktop]')
    assert mineru == runtime.command('-m', 'pip', 'install', '-r', str(root / 'requirements-mineru.txt'))
    assert '-c' not in mineru
    assert npm_install == ['npm', 'ci']
    assert npm_build == ['npm', 'run', 'build']
    assert calls[2][1]['cwd'] == root / 'frontend'
    assert all(call[1]['runtime'] == runtime for call in calls)
    assert not (root / '.venv').exists()
    assert manage.frontend_status(root)['state'] == 'current'
    assert (root / 'frontend/dist' / manage.FRONTEND_STAMP).is_file()


def _packaging_wheel(directory):
    """Build an offline wheel from the already installed dependency."""
    import packaging
    package = Path(packaging.__file__).parent
    version = packaging.__version__
    metadata = f'packaging-{version}.dist-info'
    files = {
        f'packaging/{path.relative_to(package).as_posix()}': path.read_bytes()
        for path in package.rglob('*')
        if path.is_file() and '__pycache__' not in path.parts and path.suffix != '.pyc'
    }
    files[f'{metadata}/METADATA'] = f'Metadata-Version: 2.3\nName: packaging\nVersion: {version}\n'.encode()
    files[f'{metadata}/WHEEL'] = b'Wheel-Version: 1.0\nGenerator: offline-test\nRoot-Is-Purelib: true\nTag: py3-none-any\n'
    files[f'{metadata}/RECORD'] = ''.join(f'{name},,\n' for name in (*files, f'{metadata}/RECORD')).encode()
    wheel = directory / f'packaging-{version}-py3-none-any.whl'
    with zipfile.ZipFile(wheel, 'w') as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    return wheel, version


@pytest.mark.parametrize('damage', ['record', 'metadata', 'stale_metadata'])
def test_real_selected_venv_packaging_repair_is_offline_and_idempotent(tmp_path, monkeypatch, damage):
    root = tmp_path / '中文 repair environment'
    root.mkdir()
    wheel, version = _packaging_wheel(root)
    (root / 'requirements-lock.txt').write_text(f'packaging=={version}\n', encoding='utf-8')
    subprocess.run([sys.executable, '-m', 'venv', str(root / '.venv')], check=True, capture_output=True)
    runtime = manage.Runtime(str(manage.venv_python(root)), label='isolated repair fixture')
    subprocess.run(runtime.command('-m', 'pip', 'install', '--no-index', str(wheel)), check=True, capture_output=True)
    initial = manage.packaging_status(runtime)
    metadata = Path(initial['entries'][0]['path'])
    if damage == 'record':
        (metadata / 'RECORD').unlink()
    elif damage == 'metadata':
        (metadata / 'METADATA').unlink()
    else:
        stale = metadata.with_name('packaging-0.0.dist-info')
        stale.mkdir()
        (stale / 'METADATA').write_text('Name: packaging\n', encoding='utf-8')
    monkeypatch.setenv('PIP_NO_INDEX', '1')
    monkeypatch.setenv('PIP_FIND_LINKS', root.as_uri())
    monkeypatch.setenv('PIP_DISABLE_PIP_VERSION_CHECK', '1')
    assert manage.repair_packaging(runtime, root) is True
    fixed = manage.packaging_status(runtime)
    assert fixed['version'] == fixed['active_version'] == version
    assert len(fixed['entries']) == 1
    assert not fixed['entries'][0]['issues']
    assert not list(metadata.parent.glob('.inkreference-packaging-*'))
    monkeypatch.setattr(manage, 'run_phase', lambda *a, **kw: pytest.fail('healthy installation must not be overwritten'))
    assert manage.repair_packaging(runtime, root) is False


def test_packaging_repair_refuses_external_metadata_before_install(tmp_path, monkeypatch):
    outside = tmp_path / 'outside' / 'packaging-1.dist-info'
    outside.mkdir(parents=True)
    monkeypatch.setattr(manage, 'packaging_status', lambda runtime: {
        'roots': [str(tmp_path / 'selected')],
        'entries': [{'path': str(outside), 'version': None, 'issues': ['missing RECORD']}],
        'module': None, 'active_path': str(outside), 'active_version': None,
    })
    monkeypatch.setattr(manage, 'run_phase', lambda *a, **kw: pytest.fail('must not install outside selected environment'))
    with pytest.raises(manage.EnvironmentError, match='outside the selected environment'):
        manage.repair_packaging(manage.Runtime(sys.executable, label='selected'), tmp_path)
    assert outside.is_dir()


def test_healthy_inherited_packaging_does_not_trigger_repair(tmp_path, monkeypatch):
    monkeypatch.setattr(manage, 'packaging_status', lambda runtime: {
        'roots': [str(tmp_path / 'venv')], 'entries': [], 'active_valid': True,
        'module': str(tmp_path / 'system' / 'packaging' / '__init__.py'),
        'active_path': str(tmp_path / 'system' / 'packaging-26.3.dist-info'),
        'active_version': '26.3', 'version': '26.3', 'error': None,
    })
    monkeypatch.setattr(manage, 'run_phase', lambda *a, **kw: pytest.fail('healthy inherited package must not be overwritten'))
    assert manage.repair_packaging(manage.Runtime(sys.executable, label='selected'), tmp_path) is False


def test_failed_packaging_repair_restores_metadata_and_stops(tmp_path, monkeypatch):
    metadata = tmp_path / 'site-packages' / 'packaging-1.dist-info'
    metadata.mkdir(parents=True)
    (metadata / 'METADATA').write_text('broken original', encoding='utf-8')
    (tmp_path / 'requirements-lock.txt').write_text('packaging==26.3\n', encoding='utf-8')
    monkeypatch.setattr(manage, 'packaging_status', lambda runtime: {
        'roots': [str(metadata.parent)], 'entries': [{'path': str(metadata), 'version': None, 'issues': ['missing RECORD']}],
        'module': None, 'active_path': str(metadata), 'active_version': None,
    })
    def fail_install(*a, **kw):
        raise manage.EnvironmentError('network unavailable')
    monkeypatch.setattr(manage, 'run_phase', fail_install)
    with pytest.raises(manage.EnvironmentError, match='network unavailable'):
        manage.repair_packaging(manage.Runtime(sys.executable, label='selected'), tmp_path)
    assert (metadata / 'METADATA').read_text(encoding='utf-8') == 'broken original'
    assert not list(metadata.parent.glob('.inkreference-packaging-*'))


def test_incomplete_checkout_fails_before_installing_anything(tmp_path, monkeypatch):
    monkeypatch.setattr(manage, 'creation_python', lambda value: pytest.fail('must not select/install Python'))
    with pytest.raises(manage.EnvironmentError, match='complete checkout'):
        manage.install(args('install'), tmp_path)


@pytest.mark.parametrize('platform,options,desktop_available,expects_desktop', [
    ('win32', [], True, True),
    ('darwin', [], True, True),
    ('darwin', ['--no-open'], True, False),
    ('win32', ['--browser'], True, False),
    ('darwin', [], False, False),
    ('linux', [], True, False),
])
def test_open_platform_and_browser_selection(tmp_path, monkeypatch, platform, options, desktop_available, expects_desktop):
    source_tree(tmp_path)
    monkeypatch.setattr(manage.sys, 'platform', platform)
    runtime = manage.Runtime('/selected Python/python', label='selected')
    monkeypatch.setattr(manage, 'resolve_runtime', lambda *a: runtime)
    monkeypatch.setattr(manage, 'probe', lambda selected: details(desktop=desktop_available))
    calls = []
    monkeypatch.setattr(manage.subprocess, 'call', lambda command, **kw: calls.append((command, kw)) or 0)
    data = str(tmp_path / '中文 user data')
    assert manage.open_app(args('open', *options, '--port', '8123', '--data-dir', data), tmp_path) == 0
    command, kwargs = calls[0]
    assert ('--desktop' in command) is expects_desktop
    assert ('--no-open' in command) == ('--no-open' in options)
    assert command[:6] == ['/selected Python/python', '-m', 'inkreference', '--host', '127.0.0.1', '--port']
    assert command[-2:] == ['--data-dir', data]
    assert kwargs['cwd'] == tmp_path


def test_open_missing_dependencies_has_actionable_message(tmp_path, monkeypatch):
    source_tree(tmp_path)
    monkeypatch.setattr(manage, 'resolve_runtime', lambda *a: manage.Runtime(sys.executable, label='selected'))
    monkeypatch.setattr(manage, 'probe', lambda selected: details(missing=['uvicorn']))
    monkeypatch.setattr(manage.subprocess, 'call', lambda *a, **kw: pytest.fail('must not launch broken app'))
    with pytest.raises(manage.EnvironmentError, match='uvicorn'):
        manage.open_app(args(), tmp_path)


def test_doctor_does_not_require_node_for_prebuilt_interface_or_disclose_environment_keys(tmp_path, monkeypatch, capsys):
    source_tree(tmp_path)
    monkeypatch.setenv('OPENAI_API_KEY', 'private-test-key-never-print')
    monkeypatch.setattr(manage, 'resolve_runtime', lambda *a: manage.Runtime(sys.executable, label='selected'))
    monkeypatch.setattr(manage, 'probe', lambda selected: details())
    def missing_node(selected):
        raise manage.EnvironmentError('Node.js not installed')
    monkeypatch.setattr(manage, 'frontend_tools', missing_node)
    assert manage.doctor(args('doctor', '--json'), tmp_path) == 0
    output = capsys.readouterr().out
    report = json.loads(output)
    assert report['ok'] is True
    assert report['checks']['frontend_tools']['required_for'] == 'source builds only'
    assert 'private-test-key' not in output
    assert 'OPENAI_API_KEY' not in output


def test_doctor_fails_when_application_dependencies_are_missing(tmp_path, monkeypatch, capsys):
    source_tree(tmp_path)
    monkeypatch.setattr(manage, 'resolve_runtime', lambda *a: manage.Runtime(sys.executable))
    monkeypatch.setattr(manage, 'probe', lambda selected: details(app=False, missing=['fastapi']))
    monkeypatch.setattr(manage, 'frontend_tools', lambda selected: (['node'], ['npm'], 'v22.0.0'))
    assert manage.doctor(args('doctor', '--json'), tmp_path) == 1
    assert json.loads(capsys.readouterr().out)['checks']['python']['ok'] is False


@pytest.mark.skipif(sys.platform == 'win32' or shutil.which('bash') is None, reason='POSIX shell wrappers')
@pytest.mark.parametrize('wrapper,action', [('install.sh', 'install'), ('open.sh', 'open')])
def test_shell_wrappers_preserve_spaces_and_unicode_without_installing(tmp_path, wrapper, action):
    root = tmp_path / '中文 checkout & spaces'
    scripts = root / 'scripts'
    scripts.mkdir(parents=True)
    shutil.copyfile(SCRIPT.parent / wrapper, scripts / wrapper)
    (scripts / 'manage.py').write_text('import json,sys; print(json.dumps(sys.argv[1:]))', encoding='utf-8')
    data = str(tmp_path / '中文 user data & spaces')
    result = subprocess.run(['bash', str(scripts / wrapper), '--python', sys.executable, '--data-dir', data], text=True, capture_output=True, check=True)
    assert json.loads(result.stdout) == [action, '--python', sys.executable, '--data-dir', data]


@pytest.mark.skipif(sys.platform == 'win32' or shutil.which('bash') is None, reason='POSIX shell wrappers')
@pytest.mark.parametrize('wrapper,action', [('install.sh', 'install'), ('open.sh', 'open')])
def test_shell_wrappers_zero_arguments_use_project_venv(tmp_path, wrapper, action):
    root = tmp_path / '中文 checkout with spaces'
    scripts = root / 'scripts'
    scripts.mkdir(parents=True)
    shutil.copyfile(SCRIPT.parent / wrapper, scripts / wrapper)
    (scripts / 'manage.py').write_text('import json,sys; print(json.dumps(sys.argv[1:]))', encoding='utf-8')
    target = root / '.venv/bin/python'
    target.parent.mkdir(parents=True)
    target.symlink_to(sys.executable)
    result = subprocess.run(['bash', str(scripts / wrapper)], text=True, capture_output=True, check=True)
    assert json.loads(result.stdout) == [action]


def frontend_source(root):
    source_tree(root)
    source = root / 'frontend/src/App.tsx'
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text('export default function App() { return "blue interface"; }', encoding='utf-8')
    return source


def frontend_dependencies(root):
    for relative in ('node_modules/vite/bin/vite.js', 'node_modules/typescript/bin/tsc'):
        path = root / 'frontend' / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()


def test_frontend_receipt_detects_content_changes_independently_of_timestamps(tmp_path):
    source = frontend_source(tmp_path)
    manage.stamp_frontend(tmp_path, manage.frontend_fingerprint(tmp_path))
    assert manage.frontend_status(tmp_path)['state'] == 'current'
    original_time = source.stat().st_mtime_ns
    source.write_text('export default function App() { return "updated interface"; }', encoding='utf-8')
    os.utime(source, ns=(original_time, original_time))
    assert manage.frontend_status(tmp_path)['state'] == 'stale'
    manage.stamp_frontend(tmp_path, manage.frontend_fingerprint(tmp_path))
    # An old dist/index.html cannot be trusted merely because the source receipt exists.
    (tmp_path / 'frontend/dist/index.html').write_text('replaced old interface', encoding='utf-8')
    assert manage.frontend_status(tmp_path)['state'] == 'stale'


@pytest.mark.parametrize('filename', ['package-lock.json', 'package.json', 'index.html', 'vite.config.ts', 'tsconfig.app.json', 'public/icon.svg', 'src/reader.css'])
def test_frontend_fingerprint_includes_build_inputs_not_generated_output(tmp_path, filename):
    frontend_source(tmp_path)
    before = manage.frontend_fingerprint(tmp_path)
    changed = tmp_path / 'frontend' / filename
    changed.parent.mkdir(parents=True, exist_ok=True)
    changed.write_text('changed build input', encoding='utf-8')
    assert manage.frontend_fingerprint(tmp_path) != before
    after = manage.frontend_fingerprint(tmp_path)
    generated = tmp_path / 'frontend/dist/generated.js'
    generated.write_text('new output', encoding='utf-8')
    assert manage.frontend_fingerprint(tmp_path) == after


@pytest.mark.parametrize('platform,npm', [('win32', 'npm.cmd'), ('darwin', 'npm')])
def test_open_rebuilds_changed_source_before_native_launch_without_install(tmp_path, monkeypatch, platform, npm):
    source = frontend_source(tmp_path)
    frontend_dependencies(tmp_path)
    manage.stamp_frontend(tmp_path, manage.frontend_fingerprint(tmp_path))
    source.write_text('updated frontend source', encoding='utf-8')
    monkeypatch.setattr(manage.sys, 'platform', platform)
    runtime = manage.Runtime('/中文 Python with spaces/python', label='selected')
    monkeypatch.setattr(manage, 'resolve_runtime', lambda *a: runtime)
    monkeypatch.setattr(manage, 'probe', lambda selected: details())
    monkeypatch.setattr(manage, 'frontend_tools', lambda selected: (['node'], [npm], 'v24.0.0'))
    operations = []
    def build(command, description, **kwargs):
        operations.append(('build', command, kwargs))
        (tmp_path / 'frontend/dist/index.html').write_text('new interface built', encoding='utf-8')
    monkeypatch.setattr(manage, 'run_phase', build)
    monkeypatch.setattr(manage.subprocess, 'call', lambda command, **kw: operations.append(('launch', command, kw)) or 0)
    assert manage.open_app(args('open', '--current-environment'), tmp_path) == 0
    assert [operation[0] for operation in operations] == ['build', 'launch']
    assert operations[0][1] == [npm, 'run', 'build']
    assert operations[0][2]['cwd'] == tmp_path / 'frontend'
    assert '--desktop' in operations[1][1]
    assert manage.frontend_status(tmp_path)['state'] == 'current'
    operations.clear()
    assert manage.open_app(args('open', '--current-environment'), tmp_path) == 0
    assert [operation[0] for operation in operations] == ['launch']


def test_unstamped_checkout_requires_one_build_even_when_source_mtime_is_older(tmp_path, monkeypatch):
    source = frontend_source(tmp_path)
    frontend_dependencies(tmp_path)
    os.utime(source, ns=(1, 1))
    builds = []
    monkeypatch.setattr(manage, 'frontend_tools', lambda selected: (['node'], ['npm'], 'v22.0.0'))
    monkeypatch.setattr(manage, 'run_phase', lambda command, description, **kw: builds.append(command))
    assert manage.frontend_status(tmp_path)['state'] == 'stale'
    manage.ensure_frontend(tmp_path, manage.Runtime(sys.executable))
    manage.ensure_frontend(tmp_path, manage.Runtime(sys.executable))
    assert builds == [['npm', 'run', 'build']]
    assert manage.frontend_status(tmp_path)['state'] == 'current'


def test_stale_interface_without_dependencies_reports_install_and_never_launches(tmp_path, monkeypatch):
    frontend_source(tmp_path)
    runtime = manage.Runtime(sys.executable, label='selected')
    monkeypatch.setattr(manage, 'resolve_runtime', lambda *a: runtime)
    monkeypatch.setattr(manage, 'probe', lambda selected: details())
    monkeypatch.setattr(manage, 'run_phase', lambda *a, **kw: pytest.fail('must not install/build without dependencies'))
    monkeypatch.setattr(manage.subprocess, 'call', lambda *a, **kw: pytest.fail('must not launch old interface'))
    with pytest.raises(manage.EnvironmentError, match='frontend dependencies are missing.*Run install'):
        manage.open_app(args('open', '--current-environment'), tmp_path)
    assert not (tmp_path / 'frontend/dist' / manage.FRONTEND_STAMP).exists()


def test_failed_frontend_rebuild_does_not_launch_or_mark_old_dist_current(tmp_path, monkeypatch):
    source = frontend_source(tmp_path)
    frontend_dependencies(tmp_path)
    manage.stamp_frontend(tmp_path, manage.frontend_fingerprint(tmp_path))
    receipt = tmp_path / 'frontend/dist' / manage.FRONTEND_STAMP
    original_receipt = receipt.read_bytes()
    source.write_text('changed source after pull', encoding='utf-8')
    runtime = manage.Runtime(sys.executable, label='selected')
    monkeypatch.setattr(manage, 'resolve_runtime', lambda *a: runtime)
    monkeypatch.setattr(manage, 'probe', lambda selected: details())
    monkeypatch.setattr(manage, 'frontend_tools', lambda selected: (['node'], ['npm'], 'v22.0.0'))
    def broken_build(*a, **kw):
        raise manage.EnvironmentError('synthetic build failure')
    monkeypatch.setattr(manage, 'run_phase', broken_build)
    monkeypatch.setattr(manage.subprocess, 'call', lambda *a, **kw: pytest.fail('must not launch old interface'))
    with pytest.raises(manage.EnvironmentError, match='old interface was not launched.*Run install'):
        manage.open_app(args('open', '--current-environment'), tmp_path)
    assert receipt.read_bytes() == original_receipt
    assert manage.frontend_status(tmp_path)['state'] == 'stale'


def test_source_change_during_build_never_gets_a_current_receipt(tmp_path, monkeypatch):
    source = frontend_source(tmp_path)
    def edit_during_build(*a, **kw):
        source.write_text('source was edited while building', encoding='utf-8')
    monkeypatch.setattr(manage, 'run_phase', edit_during_build)
    with pytest.raises(manage.EnvironmentError, match='source changed during the build'):
        manage.build_frontend(tmp_path, manage.Runtime(sys.executable), ['npm'])
    assert not (tmp_path / 'frontend/dist' / manage.FRONTEND_STAMP).exists()


def test_doctor_reports_stale_interface_without_rebuilding(tmp_path, monkeypatch, capsys):
    frontend_source(tmp_path)
    monkeypatch.setattr(manage, 'resolve_runtime', lambda *a: manage.Runtime(sys.executable))
    monkeypatch.setattr(manage, 'probe', lambda selected: details())
    monkeypatch.setattr(manage, 'frontend_tools', lambda selected: (['node'], ['npm'], 'v22.0.0'))
    monkeypatch.setattr(manage, 'run_phase', lambda *a, **kw: pytest.fail('doctor must not rebuild'))
    assert manage.doctor(args('doctor', '--json'), tmp_path) == 1
    report = json.loads(capsys.readouterr().out)
    assert report['checks']['interface']['state'] == 'stale'
    assert 'Open rebuilds' in report['checks']['interface']['message']
