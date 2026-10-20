import asyncio
import csv
import json
from pathlib import Path
import runpy

import pytest

from unitelabs.ur5e_connector.io.rtde import JointSample


class FakeMonitor:
    fail_after = None
    opened = 0

    def __init__(self, host):
        self.count = 0
        self.closed = False

    async def open(self):
        type(self).opened += 1

    async def sample(self):
        await asyncio.sleep(.001)
        if self.fail_after is not None and self.count >= self.fail_after:
            raise ConnectionError('Telemetry lost')
        self.count += 1
        return JointSample(self.count*.05, (self.count*.001,)*6, (.02,)*6, 7, 1, 1)

    async def close(self):
        self.closed = True


def recorder(monkeypatch):
    namespace = runpy.run_path(str(Path('examples/record_trajectory.py').resolve()))
    monkeypatch.setitem(namespace['record'].__globals__, 'JointMonitor', FakeMonitor)
    monkeypatch.setattr(FakeMonitor, 'fail_after', None)
    monkeypatch.setattr(FakeMonitor, 'opened', 0)
    return namespace['record']


async def test_records_all_axes_and_controller_time(tmp_path, monkeypatch):
    output = tmp_path/'capture.csv'
    summary = await recorder(monkeypatch)('mock', output, .03)
    rows = list(csv.DictReader(output.open()))
    assert summary['status'] == 'duration_completed'
    assert len(rows) == summary['samples'] > 2
    assert all(f'q{i}_degrees' in rows[0] and f'qd{i}_degrees_per_second' in rows[0] for i in range(1,7))
    assert all(float(b['controller_seconds']) > float(a['controller_seconds']) for a,b in zip(rows,rows[1:]))
    assert json.loads(output.with_suffix('.summary.json').read_text()) == summary


async def test_failure_preserves_partial_record_and_marks_incomplete(tmp_path, monkeypatch):
    run = recorder(monkeypatch)
    monkeypatch.setattr(FakeMonitor, 'fail_after', 2)
    output = tmp_path/'capture.csv'
    with pytest.raises(ConnectionError, match='Telemetry lost'):
        await run('mock', output, 1)
    assert len(list(csv.DictReader(output.open()))) == 2
    summary = json.loads(output.with_suffix('.summary.json').read_text())
    assert summary['status'] == 'recording_failed'
    assert summary['samples'] == 2


async def test_cancel_preserves_partial_record(tmp_path, monkeypatch):
    run = recorder(monkeypatch)
    output = tmp_path/'capture.csv'
    task = asyncio.create_task(run('mock', output, None))
    await asyncio.sleep(.02)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    summary = json.loads(output.with_suffix('.summary.json').read_text())
    assert summary['status'] == 'operator_stopped_recording'
    assert summary['samples'] > 0


async def test_refuses_to_overwrite_evidence_before_connect(tmp_path, monkeypatch):
    run = recorder(monkeypatch)
    output = tmp_path/'capture.csv'
    output.write_text('existing evidence')
    with pytest.raises(FileExistsError):
        await run('mock', output, 1)
    assert output.read_text() == 'existing evidence'
    assert FakeMonitor.opened == 0
