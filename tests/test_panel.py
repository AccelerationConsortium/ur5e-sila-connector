import asyncio
import math
import pytest
from unitelabs.ur5e_connector.ui.service import PanelError, PanelService, compile_steps


def test_compile_bounded():
    steps, target = compile_steps([{'joint': 'Base', 'delta': 25}], [0]*6, {})
    assert len(steps) == 3
    assert all(abs(s['delta']) <= 10 for s in steps)
    assert target[0] == pytest.approx(25)


@pytest.mark.parametrize('delta', [0, True, float('nan'), 91])
def test_compile_rejects(delta):
    with pytest.raises(PanelError):
        compile_steps([{'joint':'Base','delta':delta}], [0]*6, {})


def test_limits():
    with pytest.raises(PanelError):
        compile_steps([{'joint':'Elbow','delta':20}], [0,0,math.radians(150),0,0,0], {})


async def test_panel_sila_sequence(sila_client, tmp_path):
    port = sila_client.config.port
    panel = PanelService(tmp_path/'panel', port, port)
    await panel.open()
    try:
        state = await panel.status(force=True)
        assert state['connected'] and state['simulation']
        position = await panel.teach('Approach')
        assert position['simulation'] is True
        plan = await panel.preview({'rows':[{'joint':'Wrist1','delta':20}], 'speed':20})
        job = await panel.start(plan['id'], 'test-run')
        assert job['total'] == 2
        await asyncio.wait_for(panel.task, 10)
        assert panel.jobs['test-run']['status'] == 'completed'
        assert panel.jobs['test-run']['completed'] == 2
        duplicate = await panel.start(plan['id'], 'test-run')
        assert duplicate['status'] == 'completed'
        state = await panel.status(force=True)
        assert state['JointPositions'][3] == pytest.approx(math.radians(20))
        await panel.preview({'rows':[{'position_id':position['id']}], 'speed':10})
        panel.positions[position['id']]['simulation'] = False
        with pytest.raises(PanelError):
            await panel.preview({'rows':[{'position_id':position['id']}], 'speed':10})
    finally:
        await panel.close()


def test_http_origin_and_token_guards():
    import http.client
    import threading
    import json
    from unitelabs.ur5e_connector.ui.server import PanelServer
    loop = asyncio.new_event_loop()
    server = PanelServer(None, loop, 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = server.server_address[1]
    def request(method, path, body=None, headers=None):
        client = http.client.HTTPConnection('127.0.0.1', port)
        client.request(method, path, body, headers or {})
        result = client.getresponse()
        status, content = result.status, result.read()
        client.close()
        return status, content
    try:
        code, raw = request('GET', '/api/session')
        assert code == 200
        token = json.loads(raw)['token']
        assert request('POST','/api/runs','{}', {'Content-Type':'application/json'})[0] == 403
        headers = {'Content-Type':'application/json','X-Panel-Token':token,'Origin':'https://evil.example'}
        assert request('POST','/api/runs','{}',headers)[0] == 403
        assert request('GET','/',headers={'Host':'evil.example'})[0] == 403
        assert request('GET','/features')[0] == 404
        assert request('GET','/../../pyproject.toml')[0] == 404
    finally:
        server.shutdown()
        server.server_close()
        loop.close()


def test_dance_returns_to_start_and_preserves_phrases():
    import json
    from pathlib import Path
    routine = json.loads(Path('src/unitelabs/ur5e_connector/ui/static/routines.json').read_text())[0]
    initial = [-4.54, -.3745, -2.0312, 3.9619, 3.2046, 5.5112]
    steps, target = compile_steps(routine['rows'], initial, {})
    assert len(routine['rows']) == 24
    assert target == pytest.approx([math.degrees(q) for q in initial])
    assert len(set(s['phrase'] for s in steps)) == 4
    assert all(abs(s['delta']) <= 10 for s in steps)
    for i in range(6):
        assert max(abs(s['target_degrees'][i]-math.degrees(initial[i])) for s in steps) <= (25.00001 if i == 5 else 15.00001)


async def test_complete_dance_via_sila(sila_client, tmp_path):
    import json
    from pathlib import Path
    routine = json.loads(Path('src/unitelabs/ur5e_connector/ui/static/routines.json').read_text())[0]
    panel = PanelService(tmp_path/'dance', sila_client.config.port, sila_client.config.port)
    await panel.open()
    try:
        initial = (await panel.status(force=True))['JointPositions']
        plan = await panel.preview({'rows': routine['rows'], 'speed': 40})
        assert plan['travel_seconds_minimum'] > 0
        assert len(plan['ranges_degrees']) == 6
        await panel.start(plan['id'], 'complete-dance')
        await asyncio.wait_for(panel.task, 30)
        assert panel.jobs['complete-dance']['status'] == 'completed'
        final = (await panel.status(force=True))['JointPositions']
        assert final == pytest.approx(initial)
        with pytest.raises(PanelError):
            await panel.preview({'rows': routine['rows'], 'speed':41})
    finally:
        await panel.close()
