"""data 资产专项检查：metadata.json 的声明 vs 实体（记录数/列名/bbox/网格/公式复算）。"""
from __future__ import annotations

import re

from pathlib import Path

from .base import Ctx, load

NAME = 'data'
APPLIES_TO = ('data',)

def probe_data_declarations(assets: list, metas: dict) -> dict:
    """data 资产：metadata.json 的声明 vs 实体（记录数/列名/bbox/网格/公式复算）。"""
    import csv as _csv
    import math as _math

    rows = {}
    for d in assets:
        if str((metas.get(d.name) or {}).get('asset_type')) != 'data':
            continue
        meta = load(d / 'metadata.json')
        if not isinstance(meta, dict):
            rows[d.name] = {'has_metadata': False}
            continue
        payload = None
        for cand in sorted(d.rglob('*.csv')):
            payload = cand
            break
        info = {'has_metadata': True, 'payload': str(payload.relative_to(d)) if payload else None,
                'records_mismatch': None, 'columns_mismatch': None,
                'bbox_mismatch': None, 'grid_incomplete': None, 'formula_max_dev': None,
                'formula_tolerance': (meta.get('formula') or {}).get('tolerance')}
        if payload is None:
            rows[d.name] = info
            continue
        with payload.open(encoding='utf-8', errors='replace') as f:
            data = list(_csv.DictReader(f))
        if isinstance(meta.get('records'), int):
            info['records_mismatch'] = None if len(data) == meta['records'] else {'declared': meta['records'], 'actual': len(data)}
        if isinstance(meta.get('columns'), list):
            cols = list(data[0].keys()) if data else []
            info['columns_mismatch'] = None if cols == meta['columns'] else {'declared': meta['columns'], 'actual': cols}
        if isinstance(meta.get('bbox'), dict) and data and 'x' in data[0] and 'y' in data[0]:
            xs = [float(r['x']) for r in data]
            ys = [float(r['y']) for r in data]
            bx, by = meta['bbox'].get('x'), meta['bbox'].get('y')
            if bx and by and ([min(xs), max(xs)] != bx or [min(ys), max(ys)] != by):
                info['bbox_mismatch'] = {'declared': meta['bbox'], 'actual': {'x': [min(xs), max(xs)], 'y': [min(ys), max(ys)]}}
        grid = meta.get('grid') or {}
        if grid.get('nx') and grid.get('ny') and data and 'x' in data[0] and 'y' in data[0]:
            cells = {(int(float(r['x'])), int(float(r['y']))) for r in data}
            want = {(x, y) for x in range(int(grid['nx'])) for y in range(int(grid['ny']))}
            if cells != want:
                info['grid_incomplete'] = {'missing': len(want - cells), 'extra': len(cells - want)}
        formula = meta.get('formula') or {}
        if formula.get('expr') and data and {'x', 'y'} <= set(data[0]) and 'value' in data[0]:
            ns0 = {'sin': _math.sin, 'cos': _math.cos, 'sqrt': _math.sqrt, 'pi': _math.pi, 'exp': _math.exp}
            base = int(formula.get('i_base', 1))
            worst = 0.0
            try:
                for k, r in enumerate(data, start=base):
                    env = dict(ns0, x=float(r['x']), y=float(r['y']), i=k)
                    worst = max(worst, abs(eval(formula['expr'], {'__builtins__': {}}, env) - float(r['value'])))
                info['formula_max_dev'] = worst
            except Exception as exc:  # noqa: BLE001
                info['formula_error'] = str(exc)[:120]
        rows[d.name] = info
    return {'assets': dict(sorted(rows.items()))}



def probe(ctx: Ctx) -> dict:
    return {'data_declaration_profile': probe_data_declarations(ctx.assets, ctx.metas)}
