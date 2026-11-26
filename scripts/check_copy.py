#!/usr/bin/env python3
"""副本可信性检查：确认「我要在里面跑用例的那份东西」忠实于原包、且与外界的隔离没破。

为什么不重跑一遍预审：预审查的是"包合不合规"，在副本上重跑同一套检查几乎全是重复劳动
（唯一新增的差异是挂载进来的外部资产软链，而它们是按声明做的、本就合规）。
真正只在副本上才成立的问题有且只有三类，本脚本就只查这三类：

1. **文件集忠实性**：副本 = 原包（跳过 `asset-runs/`、`__pycache__`、`*.pyc`），
   缺文件/多文件/大小不一致 → 说明复制不完整，副本不可信；
2. **隔离性**：副本内每条软链都必须解析到**副本内部**。绝对路径软链会指回原包，
   于是"在副本里跑"其实在读/写原包 —— 这是复制 stage 才会暴露的问题，预审看不到；
   *例外*：`external_assets.json` 里声明的 `target_path`，以及带 `.mounted-external.json`
   标记的挂载目录（这些是初始化**故意**造的链接）；
3. **挂载落地**：`external-mounts.json` 里 `required: true` 的声明必须都解析到了；
   声明了却什么都没挂上，说明 `external_assets.json` 与磁盘现状不一致。

退出码：0 可信；1 不可信（原因逐条列出）；2 参数/IO 错误。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

SKIP = {'asset-runs', '__pycache__', '.git'}
SKIP_SUFFIX = {'.pyc'}
MARKER = '.mounted-external.json'


def rel_files(root: Path) -> dict[str, int]:
    out = {}
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP]
        for f in filenames:
            p = Path(dirpath) / f
            if p.suffix in SKIP_SUFFIX or p.name == MARKER:
                continue
            rel = p.relative_to(root).as_posix()
            try:
                out[rel] = p.stat().st_size
            except OSError:
                out[rel] = -1
    return out


def declared_pairs(root: Path) -> set[tuple[str, str]]:
    """(资产目录, target_path)：external_assets.json 里声明要挂进来的位置。"""
    out = set()
    for spec in root.rglob('external_assets.json'):
        if SKIP & set(spec.relative_to(root).parts):
            continue
        asset_dir = spec.parent.relative_to(root).as_posix()
        try:
            doc = json.loads(spec.read_text(encoding='utf-8'))
        except Exception:  # noqa: BLE001
            continue
        for a in doc.get('assets') or []:
            tp = str((a or {}).get('target_path') or '').strip().rstrip('/')
            if tp:
                out.add((asset_dir, tp))
    return out


def check(source: Path, copy: Path, mounts: Path | None = None) -> dict:
    findings: list[dict] = []
    if not copy.is_dir():
        return {'ok': False, 'findings': [{'code': 'COPY-MISSING', 'message': f'副本不存在：{copy}'}]}

    # 1) 文件集忠实性
    src, dst = rel_files(source), rel_files(copy)
    missing = sorted(set(src) - set(dst))
    extra = sorted(set(dst) - set(src))
    sizediff = sorted(k for k in set(src) & set(dst) if src[k] != dst[k])
    if missing:
        findings.append({'code': 'COPY-FILE-MISSING', 'count': len(missing), 'sample': missing[:10],
                         'message': f'副本缺 {len(missing)} 个文件（示例：{", ".join(missing[:5])}）'})
    if extra:
        findings.append({'code': 'COPY-FILE-EXTRA', 'count': len(extra), 'sample': extra[:10],
                         'message': f'副本多出 {len(extra)} 个文件（示例：{", ".join(extra[:5])}）'})
    if sizediff:
        findings.append({'code': 'COPY-SIZE-MISMATCH', 'count': len(sizediff), 'sample': sizediff[:10],
                         'message': f'{len(sizediff)} 个文件大小与原包不同（示例：{", ".join(sizediff[:5])}）'})

    # 2) 隔离性：软链必须落在副本内
    declared = declared_pairs(copy)
    exempt_prefixes = {f'{a}/{t}' for a, t in declared}
    copy_res = copy.resolve()
    escapes = []
    for dirpath, dirnames, filenames in os.walk(copy):
        for name in list(dirnames) + filenames:
            p = Path(dirpath) / name
            if not p.is_symlink():
                continue
            rel = p.relative_to(copy).as_posix()
            # 声明过的挂载点（<资产目录>/<target_path> 及其子树）是初始化故意造的，放行
            if any(rel == pre or rel.startswith(pre + '/') for pre in exempt_prefixes):
                continue
            try:
                tgt = p.resolve()
            except OSError:
                escapes.append(f'{rel} -> (unresolvable)')
                continue
            if tgt == copy_res or copy_res in tgt.parents:
                if (tgt / MARKER).is_file():
                    continue
                continue
            escapes.append(f'{rel} -> {tgt}')
    if escapes:
        findings.append({'code': 'COPY-SYMLINK-ESCAPE', 'count': len(escapes), 'sample': escapes[:10],
                         'message': f'{len(escapes)} 条软链解析到副本外（示例：{escapes[0]}）'
                                    '—— 副本隔离失效，"在副本里跑"实际会读写原包'})

    # 3) 挂载落地
    mounts_f = mounts or (copy.parent / 'external-mounts.json')
    if not mounts_f.is_file():
        cand = list(Path(copy).parent.glob('external-mounts.json'))
        mounts_f = cand[0] if cand else mounts_f
    mounted = 0
    if mounts_f.is_file():
        try:
            doc = json.loads(mounts_f.read_text(encoding='utf-8'))
        except Exception:  # noqa: BLE001
            doc = {}
        resolved = doc.get('resolved') or []
        missing_req = [m for m in (doc.get('missing') or []) if m.get('required', True)]
        mounted = len(resolved)
        if missing_req:
            findings.append({'code': 'COPY-MOUNT-MISSING', 'count': len(missing_req),
                             'sample': [str(m.get('asset_id') or m.get('target_path')) for m in missing_req[:5]],
                             'message': f'{len(missing_req)} 个 required 外部资产没挂上'})
        elif (doc.get('declared') or 0) > 0 and not resolved:
            findings.append({'code': 'COPY-MOUNT-EMPTY', 'declared': doc.get('declared'),
                             'message': f"声明了 {doc.get('declared')} 个外部资产，但一个都没解析到"})

    return {'ok': not findings, 'findings': findings,
            'stats': {'source_files': len(src), 'copy_files': len(dst), 'mounted': mounted,
                      'declared': sorted(exempt_prefixes)}}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description='副本可信性检查（忠实性 / 隔离性 / 挂载落地）')
    ap.add_argument('--source', required=True, help='原包根目录')
    ap.add_argument('--copy', required=True, help='副本根目录')
    ap.add_argument('--mounts', help='external-mounts.json 路径（默认在副本父目录找）')
    ap.add_argument('--json-out')
    args = ap.parse_args(argv)

    source = Path(args.source).expanduser().resolve()
    copy = Path(args.copy).expanduser().resolve()
    if not source.is_dir():
        print(f'原包不存在：{source}', file=sys.stderr)
        return 2
    report = check(source, copy, Path(args.mounts).expanduser().resolve() if args.mounts else None)
    payload = {'tool': 'check_copy.py', 'source': str(source), 'copy': str(copy), **report}
    if args.json_out:
        out = Path(args.json_out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    st = report.get('stats') or {}
    print(f"副本可信性：{'通过' if report['ok'] else '未通过'}"
          f"（文件 {st.get('copy_files')}/{st.get('source_files')}，挂载 {st.get('mounted')}，"
          f"阻断项 {len(report['findings'])}）")
    for f in report['findings']:
        print(f"  [阻断] {f['code']}：{f['message']}")
    return 0 if report['ok'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
