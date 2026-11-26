#!/usr/bin/env python3
"""并发跑各资产自带用例：每资产写分片，最后按 case_id 排序合并成 cases.json。

为什么需要它：逐资产用例彼此独立，串行跑是纯浪费；但并发写同一个 cases.json 会互相踩。
所以约定：**每资产一个分片 `cases-<资产目录>.json`，合并时按 (asset, case_id) 排序**——
既并发，又保证同一包两次运行得到逐字节一致的 cases.json。

并发安全边界（脚本不替你决定的部分）：
  * 本脚本只跑**轻量用例**（资产自带 validate/validate.py）；
  * `harbor_run.py`（起 docker）与 `sage_probe.py`（外部服务限流）请**串行**，不要用本脚本并发。

用法：
  python scripts/run_cases.py --package <资产根目录> --run-dir <run> [--jobs 4] [--timeout 1800] \
      [--artifacts <run>/artifacts]
  python scripts/run_cases.py --package <资产根目录> --run-dir <run> --jobs 1 --only grid-samples

产物：
  <run>/cases-<资产目录>.json      分片（每 case：rc/stdout 尾部/是否通过/证据路径）
  <run>/cases.json                 合并结果（按 asset + case_id 排序）
  <run>/case-runs/<资产目录>/<case>.log
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

try:
    from _version import stamp as _stamp
except Exception:  # noqa: BLE001
    def _stamp(payload):
        return payload

TAIL = 2000


def load(p: Path) -> dict:
    try:
        return json.loads(p.read_text(encoding='utf-8'))
    except Exception:  # noqa: BLE001
        return {}


def discover(package: Path, only: list[str] | None = None) -> list[dict]:
    out = []
    for d in sorted(p for p in package.iterdir() if p.is_dir() and not p.name.startswith('.')):
        if only and d.name not in only:
            continue
        cases_f = d / 'validate' / 'cases.json'
        runner = d / 'validate' / 'validate.py'
        if not cases_f.is_file():
            continue
        doc = load(cases_f)
        cases = doc.get('cases') or []
        if not isinstance(cases, list) or not cases:
            continue
        meta = load(d / 'asset.json')
        out.append({'dir': d, 'name': d.name, 'runner': runner if runner.is_file() else None,
                    'cases': cases, 'asset_id': meta.get('asset_id') or '',
                    'asset_type': meta.get('asset_type') or ''})
    return out


def run_one(asset: dict, run_dir: Path, timeout: int, artifacts: Path | None = None) -> dict:
    logs = run_dir / 'case-runs' / asset['name']
    logs.mkdir(parents=True, exist_ok=True)
    results = []
    for case in asset['cases']:
        cid = str(case.get('id') or case.get('case_id') or '?')
        log = logs / f"{cid}.log"
        if asset['runner'] is None:
            results.append({'case_id': cid, 'asset': asset['name'], 'status': 'not_evaluated',
                            'rc': None, 'reason': 'no_runner', 'log': str(log.relative_to(run_dir))})
            continue
        # 产物目录优先用 <artifacts>/<资产目录>（第二步跑出来的实际输出），否则退回资产目录本身
        art_dir = (artifacts / asset['name']) if (artifacts and (artifacts / asset['name']).is_dir()) else asset['dir']
        cmd = [sys.executable, str(asset['runner']), '--case', cid, '--artifact', str(art_dir)]
        t0 = time.time()
        try:
            p = subprocess.run(cmd, cwd=str(asset['dir']), capture_output=True, text=True, timeout=timeout)
            out, rc, timed_out = (p.stdout or '') + (p.stderr or ''), p.returncode, False
        except subprocess.TimeoutExpired:
            out, rc, timed_out = f'timeout after {timeout}s', None, True
        except Exception as exc:  # noqa: BLE001
            out, rc, timed_out = f'{type(exc).__name__}: {exc}', None, False
        log.write_text(out[-8000:], encoding='utf-8')
        results.append({'case_id': cid, 'asset': asset['name'], 'asset_id': asset['asset_id'],
                        'status': ('not_evaluated' if timed_out else ('pass' if rc == 0 else 'fail')),
                        'rc': rc, 'timed_out': timed_out,
                        'seconds': round(time.time() - t0, 2),
                        'stdout_tail': out[-TAIL:].strip(),
                        'artifact': str(art_dir), 'log': str(log.relative_to(run_dir))})
    shard = {'tool': 'run_cases.py', 'asset': asset['name'], 'asset_id': asset['asset_id'],
             'asset_type': asset['asset_type'], 'runner': bool(asset['runner']),
             'cases': results}
    (run_dir / f"cases-{asset['name']}.json").write_text(
        json.dumps(shard, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return shard


def merge(run_dir: Path, shards: list[dict]) -> dict:
    rows = [c for s in shards for c in s['cases']]
    rows.sort(key=lambda c: (c.get('asset') or '', c.get('case_id') or ''))
    counts = {k: sum(1 for r in rows if r['status'] == k) for k in ('pass', 'fail', 'not_evaluated')}
    merged = {'tool': 'run_cases.py', 'cases': rows,
              'assets': [{'asset': s['asset'], 'asset_id': s['asset_id'], 'asset_type': s['asset_type'],
                          'runner': s['runner'], 'cases': len(s['cases'])} for s in
                         sorted(shards, key=lambda s: s['asset'])],
              'totals': {'planned': len(rows), **counts}}
    (run_dir / 'cases.json').write_text(json.dumps(_stamp(merged), ensure_ascii=False, indent=2) + '\n',
                                        encoding='utf-8')
    return merged


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description='并发跑资产自带用例（分片写入，按 case_id 排序合并）')
    ap.add_argument('--package', required=True)
    ap.add_argument('--run-dir', required=True)
    ap.add_argument('--jobs', type=int, default=4, help='并发数（默认 4；建议不超过 8）')
    ap.add_argument('--timeout', type=int, default=1800, help='单 case 超时秒数')
    ap.add_argument('--only', action='append', help='只跑这些资产目录（可重复）')
    ap.add_argument('--artifacts', help='产物根目录（下有 <资产目录>/ 则优先用它做 --artifact）')
    ap.add_argument('--json-out')
    args = ap.parse_args(argv)

    package = Path(args.package).expanduser().resolve()
    run_dir = Path(args.run_dir).expanduser().resolve()
    if not package.is_dir():
        print(f'资产根目录不存在：{package}', file=sys.stderr)
        return 2
    run_dir.mkdir(parents=True, exist_ok=True)
    assets = discover(package, args.only)
    if not assets:
        print('没有找到任何带 validate/cases.json 的资产（用例执行阶段无事可做）')
        merged = merge(run_dir, [])
        if args.json_out:
            Path(args.json_out).write_text(json.dumps(merged, ensure_ascii=False, indent=2), encoding='utf-8')
        return 0
    jobs = max(1, min(int(args.jobs), 8, len(assets)))
    print(f'资产 {len(assets)} 个，并发 {jobs}（harbor/Sage 请另行串行执行）')
    shards = []
    with ThreadPoolExecutor(max_workers=jobs) as pool:
        art_root = Path(args.artifacts).expanduser().resolve() if args.artifacts else None
        futures = {pool.submit(run_one, a, run_dir, args.timeout, art_root): a for a in assets}
        for fut in as_completed(futures):
            shard = fut.result()
            shards.append(shard)
            t = shard['cases']
            ok = sum(1 for c in t if c['status'] == 'pass')
            print(f"  {shard['asset']:24s} {ok}/{len(t)} 通过")
    merged = merge(run_dir, shards)
    tot = merged['totals']
    print(f"合并：planned {tot['planned']} / pass {tot['pass']} / fail {tot['fail']} / "
          f"not_evaluated {tot['not_evaluated']} → {run_dir / 'cases.json'}")
    if args.json_out:
        Path(args.json_out).write_text(json.dumps(merged, ensure_ascii=False, indent=2), encoding='utf-8')
    return 0 if tot['fail'] == 0 else 1


if __name__ == '__main__':
    raise SystemExit(main())
