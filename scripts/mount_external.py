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


def candidate_dirs(package: Path, search_roots, asset_dir: Path | None, exclude=(), into=None):
    """按优先级产出候选目录（去重保序；挂载目标自身与 .asset-work 一律排除）。"""
    seen = set()
    excluded = [Path(e).resolve() for e in exclude if e]
    into = Path(into).resolve() if into else None

    def emit(d: Path):
        d = d.resolve()
        if d in seen:
            return
        for ex in excluded:
            if d == ex or ex in d.parents:
                return                      # 不从挂载目标里再挂一遍，避免拿污染过的副本当源
        try:                                # 只在**包内**按目录名排除（包本身可能在 .asset-work 下）
            rel_parts = d.relative_to(package.resolve()).parts
        except ValueError:
            rel_parts = ()
        if SKIP_PARTS & set(rel_parts):
            return
        if (d / MARKER).exists() or (into and (d == into.resolve() or into.resolve() in d.parents)):
            return                          # 已挂载副本 / 挂载目标，不作为源
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


def discover(copy_root: Path):
    """找出副本内每个资产目录下的 external_assets.json，返回 [(消费方资产目录, 文件)]。"""
    out = []
    for f in sorted(copy_root.rglob(FILE)):
        rel = f.relative_to(copy_root)
        if SKIP_PARTS & set(rel.parts):
            continue
        out.append((f.parent.resolve(), f))
    return out


def asset_index(copy_root: Path) -> dict:
    """副本根下「资产目录 → (asset_id, asset_version)」，用于先在同包内匹配。"""
    idx = {}
    for a in sorted(copy_root.iterdir()):
        if not a.is_dir() or a.name in SKIP_PARTS:
            continue
        aj = a / 'asset.json'
        if not aj.is_file():
            continue
        try:
            doc = json.loads(aj.read_text(encoding='utf-8'))
        except Exception:  # noqa: BLE001
            continue
        idx[a.resolve()] = (str(doc.get('asset_id') or ''), str(doc.get('asset_version') or ''))
    return idx


def unique_dir(copy_root: Path, name: str, asset_id: str) -> Path:
    """在副本根下为被引资产选一个目录名：同名且同一个 asset_id 就复用，否则加后缀。"""
    cand = copy_root / name
    if not cand.exists():
        return cand
    aj = cand / 'asset.json'
    if aj.is_file():
        try:
            if str(json.loads(aj.read_text(encoding='utf-8')).get('asset_id') or '') == asset_id:
                return cand
        except Exception:  # noqa: BLE001
            pass
    i = 2
    while True:
        cand = copy_root / f'{name}-{i}'
        if not cand.exists():
            return cand
        i += 1


def link_external(consumer_dir: Path, target_path: str, target: Path) -> dict:
    """在消费方目录内按 target_path 建软链；父目录不存在就建出来。"""
    rel = target_path.strip().lstrip('./').rstrip('/')
    link = (consumer_dir / rel)
    link.parent.mkdir(parents=True, exist_ok=True)
    info = {'link': str(link), 'target': str(target), 'created': False, 'note': None}
    if link.is_symlink():
        if Path(link.readlink()) == target:
            info['note'] = '软链已存在且指向正确'
            return info
        link.unlink()
        info['created'] = True
    elif link.exists():
        info['note'] = '该位置已有实体（非软链），未覆盖'
        return info
    link.symlink_to(target, target_is_directory=target.is_dir())
    info['created'] = True
    return info


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description='按各资产目录下的 external_assets.json 解析并软链外部资产')
    ap.add_argument('--copy-root', required=True,
                    help='工作副本根目录（如 .asset-work/run-xxxx），其下应有各资产目录')
    ap.add_argument('--origin-package', help='原始资产包根目录（用于按优先级到包外交替查找）')
    ap.add_argument('--search-root', action='append', default=[],
                    help='用户指定的挂载目录（可重复）；会搜索该目录及其一级子目录')
    ap.add_argument('--copy-mode', choices=['copy', 'hardlink'], default='copy',
                    help='从包外找到的资产如何放进副本：copy=完全副本（默认）/ hardlink')
    ap.add_argument('--json-out')
    args = ap.parse_args(argv)

    copy_root = Path(args.copy_root).expanduser().resolve()
    if not copy_root.is_dir():
        print(f'副本根不存在：{copy_root}', file=sys.stderr)
        return 2
    origin = Path(args.origin_package).expanduser().resolve() if args.origin_package else None
    roots = [Path(r).expanduser().resolve() for r in args.search_root]

    consumers = discover(copy_root)
    if not consumers:
        payload = _stamp({'tool': 'mount_external.py', 'copy_root': str(copy_root), 'declared': 0,
                          'resolved': [], 'missing': [], 'links': [],
                          'skipped': '各资产目录下均无 external_assets.json'})
        print('各资产目录下均无 external_assets.json：无需挂载')
        if args.json_out:
            Path(args.json_out).parent.mkdir(parents=True, exist_ok=True)
            Path(args.json_out).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
        return 0

    index = asset_index(copy_root)                       # 同包资产（副本里已有的）
    outside_dirs = (list(candidate_dirs(origin, roots, None, into=copy_root)) if origin
                    else list(candidate_dirs(copy_root, roots, None, into=copy_root)))

    resolved, missing, links = [], [], []
    for consumer_dir, spec in consumers:
        consumer = consumer_dir.name
        if spec.parent != consumer_dir:
            continue
        try:
            doc = json.loads(spec.read_text(encoding='utf-8'))
        except Exception as exc:  # noqa: BLE001
            print(f'{spec} 不是合法 JSON：{exc}', file=sys.stderr)
            return 2
        for a in doc.get('assets') or []:
            aid = str(a.get('asset_id') or '')
            ver = str(a.get('asset_version') or '') or None
            required = a.get('required', True) is not False
            tpath = str(a.get('target_path') or '')
            found, where = None, None
            for d, (iid, iver) in index.items():        # ① 同包内（副本里已有）
                if iid == aid and (not ver or iver == ver):
                    found, where = d, 'same-package'
                    break
            if found is None:                            # ② 包外按优先级找，找到就复制进副本
                hit, hit_ver, near = find_asset(aid, ver, outside_dirs)
                if hit is not None:
                    target_dir = unique_dir(copy_root, hit.name, aid)
                    size_mb = sum(p.stat().st_size for p in hit.rglob('*') if p.is_file()) / 1048576
                    if size_mb > MAX_COPY_MB_WARN and args.copy_mode == 'copy':
                        print(f'[warn] {consumer} 需要的 {aid} 约 {size_mb:.0f} MB，复制占空间；'
                              '确认只读可改用 --copy-mode hardlink', file=sys.stderr)
                    n = copy_tree(hit, target_dir, args.copy_mode)
                    (target_dir / MARKER).write_text(json.dumps(
                        {'asset_id': aid, 'asset_version': hit_ver, 'source': str(hit),
                         'mode': args.copy_mode}, ensure_ascii=False), encoding='utf-8')
                    index[target_dir.resolve()] = (aid, hit_ver or '')
                    found, where = target_dir.resolve(), 'external'
                    resolved.append({'consumer': consumer, 'asset_id': aid, 'asset_version': hit_ver,
                                     'required': required, 'source': str(hit), 'placed_at': str(target_dir),
                                     'from': 'external', 'files': n + 1, 'size_mb': round(size_mb, 1),
                                     'copy_mode': args.copy_mode})
                else:
                    why = '同包内没有该资产，包外搜索范围内也没有'
                    if near:
                        why += '；找到同名 asset_id 但版本不符：' + '、'.join(f'{d.name}@{v}' for d, v in near[:3])
                    missing.append({'consumer': consumer, 'asset_id': aid, 'asset_version': ver,
                                    'required': required, 'target_path': tpath, 'reason': why})
                    continue
            else:
                resolved.append({'consumer': consumer, 'asset_id': aid, 'asset_version': ver,
                                 'required': required, 'source': str(found), 'placed_at': str(found),
                                 'from': 'same-package'})
            if not tpath:
                links.append({'consumer': consumer, 'asset_id': aid, 'link': None,
                              'note': '未声明 target_path，跳过软链'})
                continue
            info = link_external(consumer_dir, tpath, found)
            info.update({'consumer': consumer, 'asset_id': aid})
            links.append(info)

    blocked = [m for m in missing if m['required']]
    payload = _stamp({'tool': 'mount_external.py', 'copy_root': str(copy_root),
                      'origin_package': str(origin) if origin else None,
                      'search_roots': [str(r) for r in roots],
                      'consumers': [c.name for c, _ in consumers],
                      'declared': len(resolved) + len(missing),
                      'resolved': resolved, 'links': links, 'missing': missing,
                      'required_missing': [f"{m['consumer']}→{m['asset_id']}" for m in blocked],
                      'status': 'complete' if not missing else 'incomplete'})

    same = sum(1 for r in resolved if r['from'] == 'same-package')
    ext = sum(1 for r in resolved if r['from'] == 'external')
    print(f'{len(consumers)} 个资产声明了外部引用：同包解析 {same}、包外复制 {ext}、缺失 {len(missing)}，'
          f'建软链 {sum(1 for l in links if l.get("created"))}')
    for r in resolved:
        tag = '同包' if r['from'] == 'same-package' else '包外'
        print(f"  ✓ [{r['consumer']}] {r['asset_id']}（{tag}）→ {r['placed_at']}")
    for l in links:
        if l.get('link'):
            flag = '新建' if l.get('created') else (l.get('note') or '已存在')
            print(f"      {l['link']} → {l['target']}（{flag}）")
    for m in missing:
        flag = 'required' if m['required'] else 'optional'
        print(f"  ✗ [{m['consumer']}] {m['asset_id']}（{flag}）：{m['reason']}", file=sys.stderr)
    if missing:
        print(f'有 {len(missing)} 项外部资产没解析出来（其中 required {len(blocked)} 项）：不中断校验，'
              '请在报告中声明；依赖它的用例若跑不通，按用例执行结果记 blocked/failed', file=sys.stderr)
    if args.json_out:
        Path(args.json_out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.json_out).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
