#!/usr/bin/env python3
"""按 external_assets.json 自动挂载外部资产（副本形式），供依赖外部资产的用例执行。

为什么需要：某些资产（例如地球科学工具包的部分用例）会引用**别的资产**里的数据文件，
不把那份资产放到位就没法跑用例。声明在本资产目录下的 `external_assets.json` 里，本脚本负责把真实目录找出来、
以**副本**形式放到挂载目录，避免用例写产物时污染原资产。

外部资产的搜索顺序（命中即止，按优先级）
------------------------------------
1. 用户在 prompt 里指定的挂载目录本身，及其**一级子目录**
2. 同一资产包内的**其他资产目录**
3. 资产包**父级目录下的二级子目录**（`<父>/<A>/<B>`）

判定方式：读候选目录下的 `asset.json`，`asset_id` 完全一致且 `asset_version` 相符才算命中。

用法
----
  python scripts/mount_external.py --package <资产根目录> --into <挂载目录> \
      [--search-root <用户指定目录> ...] [--copy-mode copy|hardlink] [--json-out FILE]

`--into` 一般是工作副本里的目录（例如 `<副本>/external`），不要指到原始资产包里。

退出码：0 正常（**包括有外部资产没找到**——挂载缺失不中断校验，只写进 JSON 与报告里说明）；2 参数/IO 问题。

为什么缺失不退出：某个外部资产没挂上，**不代表**依赖它的用例一定跑不了（有的用例只用一部分数据、
有的自带回退），是否影响由用例执行结果说话。所以这里只如实记录，不当作门禁。
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

try:
    from _version import stamp as _stamp
except Exception:  # noqa: BLE001
    def _stamp(payload):
        return payload

FILE = 'external_assets.json'
SKIP_PARTS = {'asset-runs', '.git', '__pycache__', 'node_modules', '.asset-work'}
MAX_COPY_MB_WARN = 2048
MARKER = '.mounted-external.json'      # 挂载副本里留痕，避免下次把副本当源再挂一遍


def load(p: Path):
    return json.loads(p.read_text(encoding='utf-8'))


def asset_dirs_under(base: Path, max_depth: int):
    """列出 base 下（含 base 自身）深度不超过 max_depth 且含 asset.json 的目录。"""
    if not base.is_dir():
        return
    base = base.resolve()
    if (base / 'asset.json').is_file():
        yield base
    if max_depth <= 0:
        return
    for child in sorted(base.rglob('asset.json')):
        rel = child.relative_to(base)
        if SKIP_PARTS & set(rel.parts):
            continue
        depth = len(rel.parts) - 1          # asset.json 的父目录相对 base 的层数
        if depth <= max_depth:
            yield child.parent.resolve()


def candidate_dirs(package: Path, search_roots, asset_dir: Path | None, exclude=()):
    """按优先级产出候选目录（去重保序；挂载目标自身与 .asset-work 一律排除）。"""
    seen = set()
    excluded = [Path(e).resolve() for e in exclude if e]

    def emit(d: Path):
        d = d.resolve()
        if d in seen:
            return
        for ex in excluded:
            if d == ex or ex in d.parents:
                return                      # 不从挂载目标里再挂一遍，避免拿污染过的副本当源
        if '.asset-work' in d.parts:
            return
        if (d / MARKER).exists() or 'external' in d.parts:
            return                          # 已挂载副本 / 约定的挂载目录，不作为源
        seen.add(d)
        return d

    for root in search_roots:
        d = emit(root)
        if d:
            yield d
        for sub in sorted(p for p in root.resolve().iterdir() if p.is_dir()) if root.is_dir() else []:
            if SKIP_PARTS & set(sub.name.split('/')):
                continue
            d = emit(sub)
            if d:
                yield d

    if asset_dir is not None:
        for d in asset_dirs_under(package, max_depth=3):
            if d == asset_dir.resolve():
                continue
            got = emit(d)
            if got:
                yield got

    parent = package.resolve().parent
    if parent.is_dir():
        for level1 in sorted(p for p in parent.iterdir() if p.is_dir()):
            if level1.resolve() == package.resolve():
                continue
            for level2 in sorted(p for p in level1.iterdir() if p.is_dir()):
                got = emit(level2)
                if got:
                    yield got


def find_asset(want_id: str, want_ver: str | None, dirs):
    """在候选目录里找 asset_id(+version) 匹配的资产目录。"""
    near = []
    for d in dirs:
        f = d / 'asset.json'
        if not f.is_file():
            continue
        try:
            doc = load(f)
        except Exception:  # noqa: BLE001
            continue
        if str(doc.get('asset_id') or '') != want_id:
            continue
        ver = str(doc.get('asset_version') or '')
        if want_ver and ver != want_ver:
            near.append((d, ver))
            continue
        return d, ver, near
    return None, None, near


def copy_tree(src: Path, dst: Path, mode: str) -> int:
    """复制资产目录（跳过 asset-runs/.git 等运行残留），返回文件数。"""
    if dst.exists():
        shutil.rmtree(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    if mode == 'hardlink':
        shutil.copytree(src, dst, copy_function=__import__('os').link,
                        ignore=shutil.ignore_patterns(*SKIP_PARTS))
    else:
        shutil.copytree(src, dst, ignore=shutil.ignore_patterns(*SKIP_PARTS))
    return sum(1 for p in dst.rglob('*') if p.is_file())


def discover(package: Path):
    """找出包内每个资产目录下的 external_assets.json，返回 [(消费方资产目录, 文件)]。"""
    out = []
    for f in sorted(package.rglob(FILE)):
        rel = f.relative_to(package)
        if SKIP_PARTS & set(rel.parts):
            continue
        out.append((f.parent.resolve(), f))
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description='按每个资产目录下的 external_assets.json 副本挂载外部资产')
    ap.add_argument('--package', required=True, help='资产包根目录（含一个到多个资产目录）')
    ap.add_argument('--into', required=True, help='挂载目标目录（建议放在工作副本里）')
    ap.add_argument('--search-root', action='append', default=[],
                    help='用户指定的挂载目录（可重复）；会搜索该目录及其一级子目录')
    ap.add_argument('--copy-mode', choices=['copy', 'hardlink'], default='copy',
                    help='copy=完全副本（默认）；hardlink=硬链接（省空间，但必须只读使用）')
    ap.add_argument('--json-out')
    args = ap.parse_args(argv)

    package = Path(args.package).expanduser().resolve()
    if not package.is_dir():
        print(f'资产包不存在：{package}', file=sys.stderr)
        return 2
    into = Path(args.into).expanduser().resolve()
    roots = [Path(r).expanduser().resolve() for r in args.search_root]

    consumers = discover(package)
    if not consumers:
        payload = _stamp({'tool': 'mount_external.py', 'package': str(package), 'declared': 0,
                          'mounted': [], 'missing': [],
                          'skipped': '各资产目录下均无 external_assets.json'})
        print('各资产目录下均无 external_assets.json：无需挂载')
        if args.json_out:
            Path(args.json_out).parent.mkdir(parents=True, exist_ok=True)
            Path(args.json_out).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
        return 0

    mounted, missing, scanned = [], [], 0
    for consumer_dir, spec in consumers:
        consumer = consumer_dir.name
        try:
            doc = json.loads(spec.read_text(encoding='utf-8'))
        except Exception as exc:  # noqa: BLE001
            print(f'{spec} 不是合法 JSON：{exc}', file=sys.stderr)
            return 2
        dirs = list(candidate_dirs(package, roots, consumer_dir, exclude=[into, consumer_dir]))
        scanned = max(scanned, len(dirs))
        for a in doc.get('assets') or []:
            aid = str(a.get('asset_id') or '')
            ver = str(a.get('asset_version') or '') or None
            required = a.get('required', True) is not False
            found, found_ver, near = find_asset(aid, ver, dirs)
            slug = aid.split('/')[-1] or 'asset'
            if found is None:
                why = '搜索范围内没有该资产'
                if near:
                    why = '找到同名 asset_id 但版本不符：' + '、'.join(f'{d.name}@{v}' for d, v in near[:3])
                missing.append({'consumer': consumer, 'asset_id': aid, 'asset_version': ver,
                                'required': required, 'reason': why,
                                'target_path': a.get('target_path')})
                continue
            dst = into / consumer / slug
            size_mb = sum(p.stat().st_size for p in found.rglob('*') if p.is_file()) / 1048576
            if size_mb > MAX_COPY_MB_WARN and args.copy_mode == 'copy':
                print(f'[warn] {consumer} 需要的 {aid} 约 {size_mb:.0f} MB，复制占空间；确认只读可改用 --copy-mode hardlink',
                      file=sys.stderr)
            n = copy_tree(found, dst, args.copy_mode)
            (dst / MARKER).write_text(json.dumps({'asset_id': aid, 'asset_version': found_ver,
                                                  'source': str(found), 'mode': args.copy_mode,
                                                  'consumer': consumer}, ensure_ascii=False),
                                      encoding='utf-8')
            mounted.append({'consumer': consumer, 'asset_id': aid, 'asset_version': found_ver,
                            'required': required, 'source': str(found), 'mounted_at': str(dst),
                            'files': n + 1, 'size_mb': round(size_mb, 1), 'mode': args.copy_mode,
                            'declared_target_path': a.get('target_path')})

    blocked = [m for m in missing if m['required']]
    payload = _stamp({'tool': 'mount_external.py', 'package': str(package), 'into': str(into),
                      'search_roots': [str(r) for r in roots],
                      'consumers': [c.name for c, _ in consumers],
                      'declared': len(mounted) + len(missing),
                      'candidate_dirs_scanned': scanned,
                      'mounted': mounted, 'missing': missing,
                      'required_missing': [f"{m['consumer']}→{m['asset_id']}" for m in blocked],
                      'status': 'complete' if not missing else 'incomplete'})

    print(f'{len(consumers)} 个资产声明了外部引用，扫描 {scanned} 个候选目录 → 挂载 {len(mounted)}，缺失 {len(missing)}')
    for m in mounted:
        print(f"  ✓ [{m['consumer']}] {m['asset_id']}（v{m['asset_version']}）← {m['source']}")
        print(f"      → {m['mounted_at']}（{m['files']} 文件 / {m['size_mb']} MB，{m['mode']}）")
    for m in missing:
        flag = 'required' if m['required'] else 'optional'
        print(f"  ✗ [{m['consumer']}] {m['asset_id']}（{flag}）：{m['reason']}", file=sys.stderr)
    if missing:
        print(f'有 {len(missing)} 项外部资产没挂上（其中 required {len(blocked)} 项）：不中断校验，'
              '请在报告中声明；依赖它的用例若跑不通，按用例执行结果记 blocked/failed', file=sys.stderr)
    if args.json_out:
        Path(args.json_out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.json_out).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
