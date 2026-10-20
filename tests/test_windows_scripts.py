"""Exercise native PowerShell wrappers only on the Windows CI runner."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

pytestmark = pytest.mark.skipif(sys.platform != 'win32', reason='Native Windows PowerShell wrapper tests')
ROOT = Path(__file__).resolve().parents[1]


def invoke(script, arguments, env, cwd):
    executable = shutil.which('pwsh')
    assert executable, 'PowerShell 7.4+ is required on the Windows test host.'
    return subprocess.run([executable, '-NoLogo', '-NoProfile', '-NonInteractive', '-File', str(script), *arguments],
                          env=env, cwd=cwd, capture_output=True, text=True, encoding='utf-8', timeout=60)


def wrapper_environment(tmp_path):
    env = os.environ.copy()
    assert env.get('UV_PROJECT_ENVIRONMENT'), 'Run Windows tests using scripts/run.ps1.'
    env['UR5E_RUNTIME_DIR'] = str(Path(env['UV_PROJECT_ENVIRONMENT']).parent)
    env['UR5E_DATA_DIR'] = str(tmp_path/'data with spaces')
    return env


def fake_uv_environment(tmp_path):
    env = os.environ.copy()
    binary = tmp_path/'fake tools'
    binary.mkdir()
    marker = tmp_path/'uv called.txt'
    (binary/'uv.cmd').write_text('@echo off\necho %* >> "%UR5E_TEST_MARKER%"\nexit /b 37\n')
    env.update(PATH=str(binary)+os.pathsep+env['PATH'], UR5E_TEST_MARKER=str(marker),
               UR5E_RUNTIME_DIR=str(tmp_path/'missing runtime'), UR5E_DATA_DIR=str(tmp_path/'data'))
    return env, marker


def test_run_preserves_arguments_paths_and_exit_code(tmp_path):
    env = wrapper_environment(tmp_path)
    code = ('import json,os,sys; print(json.dumps({"args":sys.argv[1:],'
            '"data":os.environ["UR5E_DATA_DIR"],"cwd":os.getcwd()}))')
    arguments = ['space value', 'quoted "value"', 'café']
    result = invoke(ROOT/'scripts/run.ps1', ['python','-c',code,*arguments],env,tmp_path)
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload['args'] == arguments
    assert Path(payload['data']) == Path(env['UR5E_DATA_DIR'])
    assert Path(payload['cwd']) == ROOT
    failed = invoke(ROOT/'scripts/run.ps1',['python','-c','import sys; sys.exit(37)'],env,tmp_path)
    assert failed.returncode == 37


def test_setup_failure_stops_before_import_or_launch(tmp_path):
    env, marker = fake_uv_environment(tmp_path)
    result = invoke(ROOT/'scripts/setup.ps1',[],env,tmp_path)
    assert result.returncode != 0
    assert 'Dependency installation failed' in result.stderr
    calls = marker.read_text().splitlines()
    assert len(calls) == 1 and calls[0].startswith('sync --locked --all-extras')


def test_missing_runtime_refuses_to_invoke_uv(tmp_path):
    env, marker = fake_uv_environment(tmp_path)
    result = invoke(ROOT/'scripts/run.ps1',['python','-c','print(123)'],env,tmp_path)
    assert result.returncode != 0
    assert 'environment is missing' in result.stderr
    assert not marker.exists()


def test_relative_data_override_is_rejected(tmp_path):
    env, marker = fake_uv_environment(tmp_path)
    env['UR5E_DATA_DIR'] = 'relative-data'
    result = invoke(ROOT/'scripts/run.ps1',['python'],env,tmp_path)
    assert result.returncode != 0
    assert 'absolute Windows paths' in result.stderr
    assert not marker.exists()
    assert not (tmp_path/'relative-data').exists()


def test_run_resolves_a_checkout_with_spaces(tmp_path):
    # Reuse the already installed environment, while exercising root resolution
    # from a second script location that contains spaces.
    clone = tmp_path/'checkout with spaces'
    shutil.copytree(ROOT/'scripts',clone/'scripts')
    for name in ['pyproject.toml','uv.lock','README.md']:
        shutil.copyfile(ROOT/name,clone/name)
    env = wrapper_environment(tmp_path)
    code = 'import os; print(os.getcwd())'
    result = invoke(clone/'scripts/run.ps1',['python','-c',code],env,tmp_path)
    assert result.returncode == 0, result.stderr
    assert Path(result.stdout.strip()) == clone
