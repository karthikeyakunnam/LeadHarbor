#!/usr/bin/env python3
"""Validate exports and execute their Code nodes in a restricted test context."""
import json
from pathlib import Path
import subprocess
ROOT=Path(__file__).resolve().parent.parent
for path in [ROOT/'workflow.json',*sorted((ROOT/'workflows').glob('*.json'))]:
    value=json.loads(path.read_text())
    names={n['name'] for n in value['nodes']}
    assert len(names)==len(value['nodes']), f'{path}: duplicate names'
    assert len({n['id'] for n in value['nodes']})==len(names), f'{path}: duplicate IDs'
    assert not value.get('pinData'), f'{path}: contains private test data'
    for node in value['nodes']:
        assert not node.get('credentials'), f'{path}: contains credentials'
        if node['type']=='n8n-nodes-base.code':
            result=subprocess.run(['node','--check'],input='function check(){\n'+node['parameters']['jsCode']+'\n}',text=True,capture_output=True)
            assert result.returncode==0,result.stderr
    for name,connections in value['connections'].items():
        assert name in names
        for output in connections['main']:
            for target in output:
                assert target['node'] in names
    print(f'Valid export: {path.relative_to(ROOT)} ({len(names)} nodes)')
subprocess.run(['node',str(ROOT/'tests/workflows.cjs')],check=True)
