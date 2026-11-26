#!/usr/bin/env python3
"""从 probe-report.json 生成盘点台账 `inventory.json`（资产清单 + 关键文件摘要 + 关联资产）。

为什么要单独一步：`check_coverage.py --inventory` 要知道"包内实际有哪些资产类型"，
报告里也要有一份可核对的资产清单；`probe_package.py` 只产事实、不产台账，
所以在这里把事实整理成稳定的台账结构。

用法：
  python scripts/build_inventory.py --probe-report <run>/probe-report.json \
      --package <资产根目录> --out <run>/inventory.json

输出结构：
  {"tool": ..., "package": ..., "assets": [{asset_id, asset_type, asset_version, dir,
    entry, artifacts[], cases, external_refs[], key_files[]{path,sha256,size},
    missing[], todo[]}], "counts": {...}, "skill": {...}}
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path

try:
    from _version import stamp as _stamp
except Exception:  # noqa: BLE001
    def _stamp(payload):
        return payload

MAX_HASH_BYTES = 64 * 1024 * 1024     # 与包络校验口径一致：≤64MB 实测摘要


def sha256_of(p: Path) -> tuple[str, int]:
    if not p.is_file():
        return '', 0
    size = p.stat().st_size
    if size > MAX_HASH_BYTES:
        return f'skipped_too_large:{size}', size
    h = hashlib.sha256()
    with p.open('rb') as f:
        for chunk in iter(lambda: f.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest(), size


def load(p: Path) -> dict:
    try:
        return json.loads(p.read_text(encoding='utf-8'))
    except Exception:  # noqa: BLE001
        return {}


def build(package: Path, probe: dict) -> dict:
    pkgs = (probe.get('checks') or {}).get('package_summary') or {}
    integ = (probe.get('checks') or {}).get('artifact_integrity') or {}
    cases = (probe.get('checks') or {}).get('cases_profile') or {}
    ext = (probe.get('checks') or {}).get('external_refs_profile') or {}
    verify = (probe.get('checks') or {}).get('verify_entry_profile') or {}
    assets = []
    for name in pkgs.get('asset_dirs') or []:
        d = package / name
        meta = load(d / 'asset.json')
        arts = (meta.get('spec') or {}).get('artifacts') or []
        key_files = []
        for a in arts:
            uri = str(a.get('uri') or '')
            target = (d / uri).resolve() if uri and not uri.startswith(('http://', 'https://')) else None
            sha, size = sha256_of(target) if target else ('', 0)
            key_files.append({'path': uri, 'sha256': sha or a.get('sha256') or '',
                              'size': size or a.get('size_bytes') or 0,
                              'declared_sha256': a.get('sha256') or '',
                              'declared_size': a.get('size_bytes') or 0})
        missing = [m for m in (integ.get('missing') or {}) if isinstance(integ.get('missing'), dict)
                   and name in json.dumps(m, ensure_ascii=False)] if integ.get('missing') else []
        case_info = (cases.get('assets') or {}).get(name) or {}
        todo = []
        if not (d / 'validate').is_dir():
            todo.append('无 validate/ 目录（无给定用例，只能验到声明层）')
        if not case_info.get('cases_json'):
            todo.append('无 validate/cases.json')
        if not (verify.get('assets') or {}).get(name, {}).get('runnable'):
            todo.append('无可用验证入口（validate/validate.py）')
        assets.append({
            'dir': name,
            'asset_id': meta.get('asset_id') or '',
            'asset_type': meta.get('asset_type') or '',
            'asset_version': meta.get('asset_version') or '',
            'name': meta.get('name') or '',
            'entry': (meta.get('spec') or {}).get('entry') or '',
            'key_files': key_files,
            'cases': {'declared': case_info.get('case_count'),
                      'cases_json': bool(case_info.get('cases_json'))},
            'external_refs': (ext.get('assets') or {}).get(name) or [],
            'missing': missing,
            'todo': todo,
            'evidence': f'probe-report.json · checks.package_summary.asset_dirs[{name}]',
        })
    counts = Counter(a['asset_type'] for a in assets)
    return {'tool': 'build_inventory.py', 'package': str(package),
            'probe_fingerprint': probe.get('probe_fingerprint'),
            'assets': assets, 'counts': dict(counts), 'asset_count': len(assets)}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description='从 probe-report.json 生成 inventory.json')
    ap.add_argument('--probe-report', required=True)
    ap.add_argument('--package', required=True)
    ap.add_argument('--out', required=True)
    args = ap.parse_args(argv)

    report = Path(args.probe_report).expanduser().resolve()
    package = Path(args.package).expanduser().resolve()
    if not report.is_file():
        print(f'probe-report.json 不存在：{report}', file=sys.stderr)
        return 2
    probe = load(report)
    if not probe:
        print(f'probe-report.json 无法解析：{report}', file=sys.stderr)
        return 2
    inv = _stamp(build(package, probe))
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(inv, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(f"盘点台账：{inv['asset_count']} 个资产（{', '.join(f'{k}×{v}' for k, v in inv['counts'].items())}）→ {out}")
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
