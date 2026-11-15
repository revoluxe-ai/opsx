"""Run portable core contracts without a model, donor files or project services."""
from pathlib import Path
import os
import subprocess
import sys

root = Path(__file__).resolve().parents[1]
env = dict(os.environ)
env['PYTHONPATH'] = os.pathsep.join(str(root / p) for p in (
    '.', 'opsx/opsx_runtime', 'opsx/configs', '.cursor/pipeline', 'tools/cursor_executor'))
paths = [
    'opsx/opsx_runtime/workflows/implementation/tests/test_routing_contract.py',
    'opsx/opsx_runtime/agents/implementation/tests/test_hop_lane_status.py',
    'opsx/opsx_runtime/workflows/tests/test_general_adapter_provider_boundary.py',
    'opsx/opsx_runtime/agents/tests/test_legacy_opsx_tools_paths.py',
    'opsx/opsx_runtime/agents/adapters/cursor_sdk/tests/test_cursor_provider_access_denied.py',
    'opsx/opsx_runtime/agents/adapters/cursor_sdk/tests/test_budget_ledger.py',
    'opsx/opsx_runtime/workflows/implementation/tests/test_stage_receipt_writer_hop_decision.py',
    'opsx/opsx_runtime/agents/adapters/cursor_sdk/tests/test_impl_verify_tool_policy.py',
    'opsx/opsx_runtime/agents/agent_tools/deterministic/tests/test_registry.py',
]
if __name__ == '__main__':
    raise SystemExit(subprocess.call([sys.executable, '-m', 'pytest', *paths, '-q',
                                     '-m', 'not live_agent', '--timeout=30'], cwd=root, env=env))
