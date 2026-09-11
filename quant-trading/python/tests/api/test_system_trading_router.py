from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from aipha.api import deps
from aipha.api.deps import create_access_token, get_result_store


def _bootstrap_store(tmp_path: Path):
    deps._DB_PATH = tmp_path / 'backtest_index.db'
    get_result_store.cache_clear()
    return get_result_store()


def _auth_headers(store, username: str, role: str) -> dict[str, str]:
    store.upsert_user({
        'username': username,
        'name': username,
        'password_hash': 'test-hash',
        'role': role,
    })
    token = create_access_token(username, role)
    return {'Authorization': f'Bearer {token}'}


def test_permission_matrix_can_be_persisted(tmp_path: Path, app):
    store = _bootstrap_store(tmp_path)
    client = TestClient(app)
    headers = _auth_headers(store, 'admin1', 'admin')

    resp = client.get('/api/v1/system/config/permission-matrix', headers=headers)
    assert resp.status_code == 200
    assert resp.json()['data'][0]['module'] == '因子中心'

    new_matrix = [
        {
            'module': '策略中心',
            'viewer': '只读',
            'researcher': '读写',
            'ops': '只读',
            'risk': '审批',
            'admin': '完全',
        }
    ]
    save_resp = client.put(
        '/api/v1/system/config/permission-matrix',
        headers=headers,
        json={'matrix': new_matrix},
    )
    assert save_resp.status_code == 200

    reloaded = client.get('/api/v1/system/config/permission-matrix', headers=headers)
    assert reloaded.status_code == 200
    assert reloaded.json()['data'] == new_matrix


def test_system_task_queue_and_audit_routes_return_page_data(tmp_path: Path, app):
    store = _bootstrap_store(tmp_path)
    client = TestClient(app)
    admin_headers = _auth_headers(store, 'admin1', 'admin')
    ops_headers = _auth_headers(store, 'ops1', 'ops')

    store.upsert_backtest_run({
        'run_id': 'bt_real_001',
        'strategy_name': 'v19_hgro',
        'run_type': 'backtest',
        'created_at': '2026-03-15 09:30:00',
        'output_dir': 'output/v19_hgro_real',
    })
    store.upsert_wf_result({
        'wf_id': 'wf_real_001',
        'strategy_name': 'v19_hgro',
        'created_at': '2026-03-15 09:40:00',
    })
    store.upsert_sweep_run({
        'sweep_id': 'sweep_real_001',
        'strategy_name': 'v19_hgro',
        'total_tasks': 12,
        'created_at': '2026-03-15 09:50:00',
        'output_dir': 'output/sweep_real_001',
    })
    store.set_system_setting('async_tasks', [
        {
            'task_id': 'task_running_001',
            'type': 'data_update',
            'status': 'running',
            'progress': 55,
            'description': 'TuShare 增量更新',
            'created_by': 'ops1',
            'created_at': '2026-03-15 10:00:00',
            'started_at': '2026-03-15 10:01:00',
            'completed_at': None,
            'duration_seconds': None,
            'result_id': None,
            'error_message': None,
        },
        {
            'task_id': 'task_failed_001',
            'type': 'backtest',
            'status': 'failed',
            'progress': 80,
            'description': '回测任务失败',
            'created_by': 'researcher1',
            'created_at': '2026-03-15 08:00:00',
            'started_at': '2026-03-15 08:01:00',
            'completed_at': '2026-03-15 08:30:00',
            'duration_seconds': 1740,
            'result_id': None,
            'error_message': 'Sharpe 计算超时',
        },
    ], updated_by='ops1')

    tasks_resp = client.get('/api/v1/system/tasks', headers=admin_headers)
    assert tasks_resp.status_code == 200
    tasks = tasks_resp.json()['data']
    assert {task['task_id'] for task in tasks} >= {'task_running_001', 'bt_real_001', 'wf_real_001', 'sweep_real_001'}
    task_id = tasks[0]['task_id']

    log_resp = client.get(f'/api/v1/system/tasks/{task_id}/log', headers=admin_headers)
    assert log_resp.status_code == 200
    assert len(log_resp.json()['data']['lines']) > 0

    retry_resp = client.post('/api/v1/system/tasks/task_failed_001/retry', headers=ops_headers)
    assert retry_resp.status_code == 200
    assert retry_resp.json()['data']['status'] == 'pending'

    cancel_resp = client.delete('/api/v1/system/tasks/task_running_001', headers=ops_headers)
    assert cancel_resp.status_code == 200

    audit_resp = client.get('/api/v1/system/audit?action=run', headers=admin_headers)
    assert audit_resp.status_code == 200
    audit_rows = audit_resp.json()['data']
    assert any(row['resource_id'] == 'task_failed_001' for row in audit_rows)
    resource_id = audit_rows[0]['resource_id']

    trace_resp = client.get(f'/api/v1/system/audit?resource_id={resource_id}', headers=admin_headers)
    assert trace_resp.status_code == 200
    assert all(row['resource_id'] == resource_id for row in trace_resp.json()['data'])

    rules_resp = client.get('/api/v1/system/config/market-rules', headers=admin_headers)
    assert rules_resp.status_code == 200
    rules = rules_resp.json()['data']
    assert isinstance(rules, list)
    assert any(item['asset_type'] == 'cn_equity' for item in rules)

    update_rules_resp = client.put('/api/v1/system/config/market-rules/cn_equity', headers=admin_headers, json={'commission_rate': 0.00012, 'slippage_bps': 12})
    assert update_rules_resp.status_code == 200

    reloaded_rules_resp = client.get('/api/v1/system/config/market-rules', headers=admin_headers)
    cn_equity = next(item for item in reloaded_rules_resp.json()['data'] if item['asset_type'] == 'cn_equity')
    assert cn_equity['commission_rate'] == 0.00012
    assert cn_equity['slippage_bps'] == 12

    universe_resp = client.get('/api/v1/system/config/universe', headers=admin_headers)
    assert universe_resp.status_code == 200
    universe_rows = universe_resp.json()['data']
    assert any(item['name'] == '手动黑名单' and 'params' in item for item in universe_rows)

    toggle_resp = client.put('/api/v1/system/config/universe/ST%E6%8E%92%E9%99%A4', headers=admin_headers, json={'enabled': False})
    assert toggle_resp.status_code == 200
    reloaded_universe = client.get('/api/v1/system/config/universe', headers=admin_headers)
    st_rule = next(item for item in reloaded_universe.json()['data'] if item['name'] == 'ST排除')
    assert st_rule['enabled'] is False

    env_resp = client.get('/api/v1/system/config/env', headers=admin_headers)
    assert env_resp.status_code == 200
    env_rows = env_resp.json()['data']
    assert any(item['key'] == 'AIPHA_ENV' for item in env_rows)

    env_update = client.put('/api/v1/system/config/env/AIPHA_ENV', headers=admin_headers, json={'value': 'paper'})
    assert env_update.status_code == 200
    reloaded_env = client.get('/api/v1/system/config/env', headers=admin_headers)
    a_env = next(item for item in reloaded_env.json()['data'] if item['key'] == 'AIPHA_ENV')
    assert a_env['value'] == 'paper'


def test_trading_create_supports_combo_and_custom_strategies(tmp_path: Path, app):
    store = _bootstrap_store(tmp_path)
    client = TestClient(app)
    researcher_headers = _auth_headers(store, 'researcher1', 'researcher')
    ops_headers = _auth_headers(store, 'ops1', 'ops')

    store.upsert_paper_portfolio({
        'portfolio_id': 'combo_demo',
        'display_name': 'Demo Combo',
        'paper_mode': 'multi_strategy',
        'paper_status': 'registered',
        'strategies': ['alpha_a', 'alpha_b'],
        'weights': {'alpha_a': 0.6, 'alpha_b': 0.4},
    })

    combo_resp = client.post(
        '/api/v1/trading/paper',
        headers=researcher_headers,
        json={'name': '组合模拟盘', 'combo_id': 'combo_demo', 'initial_capital': 2000000},
    )
    assert combo_resp.status_code == 200
    combo_data = combo_resp.json()['data']
    assert combo_data['strategy_name'] == '组合:combo_demo'
    assert combo_data['strategy_names'] == ['alpha_a', 'alpha_b']
    assert combo_data['weights']['alpha_a'] == 0.6

    custom_resp = client.post(
        '/api/v1/trading/paper',
        headers=researcher_headers,
        json={
            'name': '多策略模拟盘',
            'initial_capital': 1000000,
            'strategies': [
                {'strategy_name': 'alpha_a', 'weight': 2},
                {'strategy_name': 'alpha_b', 'weight': 1},
            ],
        },
    )
    assert custom_resp.status_code == 200
    custom_data = custom_resp.json()['data']
    assert custom_data['source_type'] == 'custom'
    assert custom_data['weights']['alpha_a'] == 2 / 3
    assert custom_data['weights']['alpha_b'] == 1 / 3

    pause_resp = client.put(
        f"/api/v1/trading/paper/{custom_data['id']}/action",
        headers=ops_headers,
        json={'action': 'pause'},
    )
    assert pause_resp.status_code == 200

    list_resp = client.get('/api/v1/trading/paper', headers=researcher_headers)
    assert list_resp.status_code == 200
    rows = list_resp.json()['data']
    paused = next(item for item in rows if item['id'] == custom_data['id'])
    assert paused['status'] == 'paused'
    assert 'positions_count' in paused
    assert 'total_trades' in paused

    positions_resp = client.get(f"/api/v1/trading/paper/{custom_data['id']}/positions", headers=researcher_headers)
    assert positions_resp.status_code == 200
    positions = positions_resp.json()['data']
    assert isinstance(positions, list)

    orders_resp = client.get(f"/api/v1/trading/paper/{custom_data['id']}/orders", headers=researcher_headers)
    assert orders_resp.status_code == 200
    orders = orders_resp.json()['data']
    assert isinstance(orders, list)

    pnl_resp = client.get(f"/api/v1/trading/paper/{custom_data['id']}/pnl", headers=researcher_headers)
    assert pnl_resp.status_code == 200
    pnl = pnl_resp.json()['data']
    assert isinstance(pnl, list)


def test_strategy_lifecycle_checklist_and_log_are_persisted(tmp_path: Path, app):
    store = _bootstrap_store(tmp_path)
    client = TestClient(app)
    researcher_headers = _auth_headers(store, 'researcher1', 'researcher')
    risk_headers = _auth_headers(store, 'risk1', 'risk_approver')

    checklist_resp = client.put(
        '/api/v1/strategies/cb_clause_play/lifecycle/checklist',
        headers=risk_headers,
        json={'checklist': {'cb_clause_play_S1_0': True, 'cb_clause_play_S1_1': True}},
    )
    assert checklist_resp.status_code == 200

    board_resp = client.get('/api/v1/strategies/lifecycle/board?show_archived=true', headers=researcher_headers)
    assert board_resp.status_code == 200
    s1_items = board_resp.json()['data']['stages']['S1']
    target = next((item for item in s1_items if item['name'] == 'cb_clause_play'), None)
    assert target is not None, f"cb_clause_play not found in S1; S1 items: {[i['name'] for i in s1_items]}"
    assert target['checklist_state']['cb_clause_play_S1_0'] is True

    approve_resp = client.post(
        '/api/v1/strategies/cb_clause_play/lifecycle/approve',
        headers=risk_headers,
        json={'action': 'approve', 'comment': 'ready for backtest'},
    )
    assert approve_resp.status_code == 200

    log_resp = client.get('/api/v1/strategies/cb_clause_play/lifecycle/log', headers=researcher_headers)
    assert log_resp.status_code == 200
    entries = log_resp.json()['data']['entries']
    assert entries[0]['action'] == 'approve'
    assert entries[0]['to_stage'] == 'S2'
    assert entries[0]['comment'] == 'ready for backtest'


def test_combo_routes_return_normalized_structure_and_persist_versions(tmp_path: Path, app):
    store = _bootstrap_store(tmp_path)
    client = TestClient(app)
    researcher_headers = _auth_headers(store, 'researcher1', 'researcher')

    create_resp = client.post(
        '/api/v1/portfolios/combos',
        headers=researcher_headers,
        json={
            'name': '组合A',
            'description': '多策略组合',
            'method': 'risk_parity',
            'strategies': [
                {'strategy_name': 'alpha_a', 'weight': 0.7},
                {'strategy_name': 'alpha_b', 'weight': 0.3},
            ],
            'constraints': {'max_weight': 0.8, 'max_correlation': 0.6},
        },
    )
    assert create_resp.status_code == 200
    combo = create_resp.json()['data']
    assert combo['name'] == '组合A'
    assert combo['strategy_count'] == 2
    assert combo['weights']['alpha_a'] == 0.7
    assert combo['status'] == 'active'

    combo_id = combo['id']
    list_resp = client.get('/api/v1/portfolios/combos', headers=researcher_headers)
    assert list_resp.status_code == 200
    listed = next(item for item in list_resp.json()['data'] if item['id'] == combo_id)
    assert listed['description'] == '多策略组合'

    update_resp = client.put(
        f'/api/v1/portfolios/combos/{combo_id}/weights',
        headers=researcher_headers,
        json={'weights': [{'strategy_name': 'alpha_a', 'weight': 0.5}, {'strategy_name': 'alpha_b', 'weight': 0.5}]},
    )
    assert update_resp.status_code == 200

    detail_resp = client.get(f'/api/v1/portfolios/combos/{combo_id}', headers=researcher_headers)
    assert detail_resp.status_code == 200
    detail = detail_resp.json()['data']
    assert detail['weights']['alpha_a'] == 0.5
    assert len(detail['strategies']) == 2

    versions_resp = client.get(f'/api/v1/portfolios/combos/{combo_id}/versions', headers=researcher_headers)
    assert versions_resp.status_code == 200
    versions = versions_resp.json()['data']
    assert len(versions) >= 2
    assert versions[0]['weights']['alpha_a'] == 0.5

    preview_resp = client.post(
        '/api/v1/portfolios/combos/preview',
        headers=researcher_headers,
        json={'strategies': [{'strategy_name': 'alpha_a', 'weight': 1.0}], 'method': 'equal'},
    )
    assert preview_resp.status_code == 200
    assert len(preview_resp.json()['data']['dates']) == 10

    corr_resp = client.post(
        '/api/v1/portfolios/combos/strategy-correlation',
        headers=researcher_headers,
        json={'strategy_names': ['alpha_a', 'alpha_b']},
    )
    assert corr_resp.status_code == 200
    assert corr_resp.json()['data']['labels'] == ['alpha_a', 'alpha_b']

    risk_resp = client.get(f'/api/v1/portfolios/combos/{combo_id}/risk', headers=researcher_headers)
    assert risk_resp.status_code == 200
    risk_data = risk_resp.json()['data']
    assert risk_data['total_var'] > 0
    assert len(risk_data['strategy_contributions']) == 2

    corr_detail_resp = client.get(f'/api/v1/portfolios/combos/{combo_id}/correlation', headers=researcher_headers)
    assert corr_detail_resp.status_code == 200
    assert corr_detail_resp.json()['data']['labels'] == ['alpha_a', 'alpha_b']

    stress_resp = client.get(f'/api/v1/portfolios/combos/{combo_id}/stress-test', headers=researcher_headers)
    assert stress_resp.status_code == 200
    assert len(stress_resp.json()['data']) >= 1

    var_resp = client.get(f'/api/v1/portfolios/combos/{combo_id}/var-distribution', headers=researcher_headers)
    assert var_resp.status_code == 200
    assert var_resp.json()['data']['var_95'] > 0

    combo_backtest_resp = client.post(f'/api/v1/portfolios/combos/{combo_id}/backtest', headers=researcher_headers)
    assert combo_backtest_resp.status_code == 200
    assert combo_backtest_resp.json()['data']['combo_id'] == combo_id


def test_factor_routes_return_enriched_data_for_pages(tmp_path: Path, app):
    store = _bootstrap_store(tmp_path)
    client = TestClient(app)
    researcher_headers = _auth_headers(store, 'researcher1', 'researcher')

    list_resp = client.get('/api/v1/factors', headers=researcher_headers)
    assert list_resp.status_code == 200
    factors = list_resp.json()['data']
    assert len(factors) > 0
    factor_name = factors[0]['name']
    assert 'direction' in factors[0]

    detail_resp = client.get(f'/api/v1/factors/{factor_name}', headers=researcher_headers)
    assert detail_resp.status_code == 200
    assert detail_resp.json()['data']['name'] == factor_name

    ic_resp = client.get(f'/api/v1/factors/{factor_name}/ic-series', headers=researcher_headers)
    assert ic_resp.status_code == 200
    assert len(ic_resp.json()['data']) == 60

    source_resp = client.get(f'/api/v1/factors/{factor_name}/source', headers=researcher_headers)
    assert source_resp.status_code == 200
    assert 'source' in source_resp.json()['data']

    group_resp = client.get(f'/api/v1/factors/{factor_name}/group-returns', headers=researcher_headers)
    assert group_resp.status_code == 200
    assert len(group_resp.json()['data']) == 5

    corr_resp = client.get(f'/api/v1/factors/correlation?names={factor_name},{factor_name}', headers=researcher_headers)
    assert corr_resp.status_code == 200
    assert corr_resp.json()['data'][0]['correlation'] == 1.0

    ortho_resp = client.post('/api/v1/factors/orthogonalize', headers=researcher_headers, json={'names': [factor_name]})
    assert ortho_resp.status_code == 200
    assert ortho_resp.json()['data']['labels'] == [factor_name]

    health_resp = client.get('/api/v1/factors/monitor/health', headers=researcher_headers)
    assert health_resp.status_code == 200
    assert len(health_resp.json()['data']) > 0

    events_resp = client.get('/api/v1/factors/monitor/decay-events', headers=researcher_headers)
    assert events_resp.status_code == 200
    assert isinstance(events_resp.json()['data'], list)


def test_backtest_and_data_update_submission_routes_persist_async_tasks(tmp_path: Path, app):
    store = _bootstrap_store(tmp_path)
    client = TestClient(app)
    researcher_headers = _auth_headers(store, 'researcher1', 'researcher')

    run_resp = client.post('/api/v1/backtests/run', headers=researcher_headers, json={'strategy_name': 'v21_vds'})
    assert run_resp.status_code == 200
    run_data = run_resp.json()['data']
    assert run_data['task_id'].startswith('task_')
    assert run_data['run_id'].startswith('run_')

    sweep_resp = client.post('/api/v1/backtests/sweep', headers=researcher_headers, json={'strategy_name': 'v21_vds', 'params': {'top_k': {'min': 10, 'max': 20, 'step': 5}}})
    assert sweep_resp.status_code == 200
    sweep_task_id = sweep_resp.json()['data']['task_id']
    sweep_detail = client.get(f'/api/v1/backtests/sweep/{sweep_task_id}', headers=researcher_headers)
    assert sweep_detail.status_code == 200
    assert len(sweep_detail.json()['data']['results']) == 1

    wf_resp = client.post('/api/v1/backtests/wf', headers=researcher_headers, json={'strategy_name': 'v21_vds', 'n_splits': 4, 'train_ratio': 0.7})
    assert wf_resp.status_code == 200
    wf_task_id = wf_resp.json()['data']['task_id']
    wf_detail = client.get(f'/api/v1/backtests/wf/{wf_task_id}', headers=researcher_headers)
    assert wf_detail.status_code == 200
    assert len(wf_detail.json()['data']['folds']) == 4

    data_resp = client.post('/api/v1/data/update', headers=researcher_headers, json={'source': 'tushare'})
    assert data_resp.status_code == 200
    assert data_resp.json()['data']['task_id'].startswith('data_')

    tasks_resp = client.get('/api/v1/system/tasks', headers=researcher_headers)
    assert tasks_resp.status_code == 200
    task_ids = [row['task_id'] for row in tasks_resp.json()['data']]
    assert run_data['task_id'] in task_ids
    assert sweep_task_id in task_ids
    assert wf_task_id in task_ids
