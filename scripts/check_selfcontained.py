#!/usr/bin/env python3
"""资产自包含校验：资产只能引用自己目录内的文件，跨资产/跨目录一律走 external_assets.json。

为什么必须有这一步：资产入库后是**独立存在的单元**，别人只拿走一个资产目录，
任何 `../shared/...`、`/home/xxx/...`、`../另一个资产/...` 的引用都会当场失效。
唯一允许的外部引用方式是本资产目录下的 `external_assets.json` 里按资产编号声明（见 references/registration.md）。

检查内容
--------
1. `asset.json` 里所有本地路径（`spec.artifacts[].uri`、`spec.asset_protocol` 的 `repository_uri`/`path`）
   解析后必须落在**该资产目录内**；越界即 violations。
2. 资产目录内的脚本/配置/文档里出现的相对路径（`../`）、绝对路径（`/home/…`、`C:\\…`）、`~/` 引用，
   解析后越出资产目录即 violations；越到**包内另一个资产目录**时额外提示改用 external_assets.json。

用法：
  python scripts/check_selfcontained.py --package <包根> [--json-out FILE] [--quiet]
  python scripts/check_selfcontained.py --package <包根> --asset <资产目录名>

退出码：0 全部自包含；1 存在越界引用；2 参数/IO 错误。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from urllib.parse import unquote

try:                                     # 布局约定：跳过核验期挂载进来的外部资产副本
    from _layout import is_mounted_external as _is_mounted
except Exception:                        # noqa: BLE001
    def _is_mounted(d):
        return (d / '.mounted-external.json').is_file()

try:
    from _version import stamp as _stamp
except Exception:  # noqa: BLE001
    def _stamp(payload):
        return payload

SCHEME = re.compile(r'^[A-Za-z][A-Za-z0-9+.-]*://')
SKIP_PARTS = {'asset-runs', '.git', '__pycache__', 'node_modules', '.venv', 'venv'}
SCAN_SUFFIX = {'.py', '.sh', '.bash', '.R', '.r', '.json', '.jsonl', '.yaml', '.yml', '.toml',
               '.cfg', '.ini', '.env', '.example', '.md', '.txt', '.schema'}
DOC_SUFFIX = {'.md', '.txt'}          # 文档里出现路径只算提示，不算违规
TEXT_PATTERNS = [
    (re.compile(r'(?:^|[\s"\'=(,])((?:\.\./)+[^\s"\'`,;:)\]]+)'), 'parent'),
    (re.compile(r'(?:^|[\s"\'=(,])(/(?:home|opt|data|mnt|Users|root|scratch)/[^\s"\'`,;:)\]]+)'), 'absolute'),
    (re.compile(r'(?:^|[\s"\'=(,])(~/[^\s"\'`,;:)\]]+)'), 'home'),
    (re.compile(r'(?:^|[\s"\'=(,])([A-Za-z]:\\\\?[^\s"\'`,;:)\]]+)'), 'windows'),
    (re.compile(r'(file://[^\s"\'`,;)\]]+)'), 'file-url'),
]
MAX_PER_ASSET = 200


def local_paths_of(asset: dict):
    """从 asset.json 抽出本地路径型取值。"""
    out = []
    spec = asset.get('spec') if isinstance(asset.get('spec'), dict) else {}
    for i, a in enumerate(spec.get('artifacts') or []):
        uri = a.get('uri') if isinstance(a, dict) else None
        if isinstance(uri, str) and uri and not SCHEME.match(uri):
            out.append((f'spec.artifacts[{i}].uri', uri))
    proto = spec.get('asset_protocol') if isinstance(spec.get('asset_protocol'), dict) else {}
    for key in ('repository_uri', 'path', 'entry', 'readme'):
        v = proto.get(key)
        if isinstance(v, str) and v and not SCHEME.match(v):
            out.append((f'spec.asset_protocol.{key}', v))
    return out


def inside(base: Path, p: Path) -> bool:
    try:
        p = p.resolve()
        base = base.resolve()
    except Exception:  # noqa: BLE001
        return False
    return p == base or base in p.parents


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description='资产自包含校验（只允许引用自己目录内的文件）')
    ap.add_argument('--package', required=True, help='资产包根目录')
    ap.add_argument('--asset', help='只检查指定资产目录（相对包根）')
    ap.add_argument('--json-out')
    ap.add_argument('--quiet', action='store_true')
    args = ap.parse_args(argv)

    root = Path(args.package).expanduser().resolve()
    if not root.is_dir():
        print(f'包根不存在：{root}', file=sys.stderr)
        return 2

    # 资产目录 = 含 asset.json 的目录（包根本身可能就是资产）
    candidates = [p.parent for p in sorted(root.rglob('asset.json'))
                  if not (SKIP_PARTS & set(p.relative_to(root).parts))
                  and not _is_mounted(p.parent)]          # 挂载副本不算本包资产
    if args.asset:
        want = (root / args.asset).resolve()
        candidates = [c for c in candidates if c.resolve() == want or want in c.resolve().parents]
    if not candidates:
        print(f'未找到任何 asset.json：{root}（无法判定资产单元）', file=sys.stderr)
        return 2
    asset_dirs = sorted({c.resolve() for c in candidates})
    asset_names = {d.name: d for d in asset_dirs}

    results = []
    for adir in asset_dirs:
        violations = []
        try:
            asset = json.loads((adir / 'asset.json').read_text(encoding='utf-8'))
        except Exception as exc:  # noqa: BLE001
            violations.append({'kind': 'unreadable', 'where': 'asset.json',
                               'detail': f'{type(exc).__name__}: {exc}', 'hint': '修包络后重跑'})
            asset = {}

        # 0) 该资产声明的外部资产挂载点（软链到目录外属合规通道）
        declared_targets = set()
        ext = adir / 'external_assets.json'
        if ext.is_file():
            try:
                for a in (json.loads(ext.read_text(encoding='utf-8')).get('assets') or []):
                    tp = str(a.get('target_path') or '').strip().lstrip('./').rstrip('/')
                    if tp:
                        declared_targets.add(tp)
            except Exception:  # noqa: BLE001
                pass

        # 1) asset.json 里的路径字段
        for field, uri in local_paths_of(asset):
            p = (adir / unquote(uri)).resolve()
            if not inside(adir, p):
                hint = _hint(p, root, asset_names, adir)
                violations.append({'kind': 'asset-json-escape', 'where': field, 'value': uri,
                                   'resolves_to': str(p), 'hint': hint})

        # 1.5) 软链：指向目录外的，若是本资产声明的 target_path 则属合规挂载，否则算违规
        for link in sorted(adir.rglob('*')):
            if not link.is_symlink():
                continue
            try:
                tgt = link.resolve()
            except Exception:  # noqa: BLE001
                continue
            if inside(adir, tgt):
                continue
            rel_link = str(link.relative_to(adir)).replace('\\', '/')
            if rel_link.rstrip('/') in declared_targets:
                violations.append({'kind': 'declared-external-link', 'severity': 'note',
                                   'where': rel_link, 'value': str(link.readlink()),
                                   'resolves_to': str(tgt),
                                   'hint': '由 external_assets.json 声明并软链到副本内其他资产，属合规通道'})
            else:
                violations.append({'kind': 'symlink-escape', 'severity': 'violation',
                                   'where': rel_link, 'value': str(link.readlink()),
                                   'resolves_to': str(tgt),
                                   'hint': '软链指向资产目录外且未在 external_assets.json 声明'})

        # 2) 目录内文本文件里的越界引用
        for f in sorted(adir.rglob('*')):
            if f.is_symlink() or not f.is_file() or f.suffix.lower() not in SCAN_SUFFIX:
                continue
            rel = f.relative_to(adir)
            if SKIP_PARTS & set(rel.parts):
                continue
            try:
                text = f.read_text(encoding='utf-8', errors='replace')
            except Exception:  # noqa: BLE001
                continue
            for lineno, line in enumerate(text.splitlines(), 1):
                if 'http://' in line or 'https://' in line:
                    line = re.sub(r'https?://[^\s"\'`,;)\]]+', '', line)
                for pat, kind in TEXT_PATTERNS:
                    for m in pat.finditer(line):
                        token = m.group(1).strip()
                        if not token or token in ('..', '../', './'):
                            continue
                        if any(ch in token for ch in ('*', '{', '}', '$')):   # 模板/通配，无法静态解析
                            continue
                        target = _resolve_token(token, f.parent)
                        if target is None or inside(adir, target):
                            continue
                        sev = 'note' if f.suffix.lower() in DOC_SUFFIX else 'violation'
                        violations.append({'kind': kind, 'severity': sev,
                                           'where': f'{rel}:{lineno}', 'value': token,
                                           'resolves_to': str(target),
                                           'hint': _hint(target, root, asset_names, adir)})
                        break
            if len(violations) >= MAX_PER_ASSET:
                break

        hard = [v for v in violations if v.get('severity') != 'note']
        results.append({'asset_dir': str(adir.relative_to(root)) or '.',
                        'asset_id': asset.get('asset_id'),
                        'violations': violations[:MAX_PER_ASSET],
                        'violation_count': len(hard),
                        'note_count': len(violations) - len(hard),
                        'self_contained': not hard})

    bad = [r for r in results if not r['self_contained']]
    payload = _stamp({'tool': 'check_selfcontained.py', 'package': str(root),
                      'assets_checked': len(results), 'assets_violating': len(bad),
                      'violations': sum(r['violation_count'] for r in results),
                      'results': results,
                      'rule': '资产只能引用自己目录内的文件；跨资产/跨目录引用必须走资产目录下的 external_assets.json'})
    if not args.quiet:
        for r in results:
            flag = 'OK  ' if r['self_contained'] else 'FAIL'
            print(f"[{flag}] {r['asset_dir']}  violations={r['violation_count']} notes={r['note_count']}")
            for v in r['violations'][:5]:
                print(f"      {v.get('severity', 'violation')}  {v.get('where')}  {v.get('value')}")
                if v.get('hint'):
                    print(f"            → {v['hint']}")
        print(f"合计 {len(results)} 个资产：自包含 {len(results) - len(bad)}，越界 {len(bad)}，"
              f"违规条目 {payload['violations']}")
    if args.json_out:
        p = Path(args.json_out)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
    return 0 if not bad else 1


def _resolve_token(token: str, from_dir: Path):
    t = unquote(token)
    try:
        if t.startswith('file://'):
            return Path(t[7:]).resolve()
        if t.startswith('~'):
            return Path(t).expanduser().resolve()
        if re.match(r'^[A-Za-z]:', t):
            return None                       # Windows 路径在当前平台无法解析，按越界处理
        if t.startswith('/'):
            return Path(t).resolve()
        return (from_dir / t).resolve()
    except Exception:  # noqa: BLE001
        return None


def _hint(target: Path, root: Path, asset_names: dict, current: Path) -> str:
    if target is None:
        return '无法解析（疑似本机绝对路径）；应改为资产目录内的相对路径'
    try:
        inside_pkg = root in target.parents or target == root
    except Exception:  # noqa: BLE001
        inside_pkg = False
    if inside_pkg:
        for name, d in asset_names.items():
            if d != current and (target == d or d in target.parents):
                return f'指向包内另一个资产「{name}」；请改用资产目录下的 external_assets.json 按资产编号声明引用'
        try:
            rel = target.relative_to(root)
            return f'指向包内共享位置 {rel}；应随资产目录交付，或改用 external_assets.json 声明'
        except Exception:  # noqa: BLE001
            return '越出资产目录'
    return '指向包外路径（本机/上级目录）；资产入库后该路径必然失效'


if __name__ == '__main__':
    raise SystemExit(main())
