#!/usr/bin/env python3
"""校验已有资产描述文件是否符合《科学智能体资产统一表征体系设计 v1.0》§4 公共包络。

本脚本**只做校验，不构造、不改写包络**。纯标准库实现，无需安装依赖；若环境里有
jsonschema，会额外用 assets/asset-envelope.schema.json 复核一次（结果以本脚本规则为准）。

用法：
  python scripts/validate_envelope.py <路径...> [--json-out FILE] [--strict] [--quiet]
  # <路径> 可以是文件（asset.json / catalog.json / 任意 JSON 或 JSONL）或目录（递归找
  # asset.json、*.asset.json、catalog.json）

退出码：
  0  全部通过（在 --strict 下：无 error 且无 warning）
  1  存在 error（或在 --strict 下存在 warning）
  2  参数、读取或解析失败
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

try:                                     # 版本戳：报告与证据 JSON 都要能追溯校验 skill 版本
    from _version import stamp as _stamp
except Exception:                        # noqa: BLE001
    def _stamp(payload):
        return payload

SCHEMA_FILE = pathlib.Path(__file__).resolve().parents[1] / 'assets' / 'asset-envelope.schema.json'

ASSET_TYPES = ['data', 'tool', 'model', 'environment', 'skill', 'task', 'trajectory', 'benchmark']
LIFECYCLE = ['draft', 'validated', 'published', 'deprecated', 'withdrawn']
ACCESS = ['public', 'restricted', 'private', 'internal']
RISK = ['low', 'medium', 'high', 'prohibited']
SOURCE_KIND = ['original', 'derived', 'generated']
TOP_LEVEL = ['schema_version', 'asset_id', 'asset_type', 'name', 'description', 'asset_version',
             'lifecycle', 'domains', 'tasks', 'task_classes', 'provenance', 'governance',
             'extensions', 'spec']
SPEC_KEYS = ['metadata', 'artifacts', 'domain_attributes', 'asset_protocol']
SEMVER = re.compile(r'^\d+\.\d+\.\d+([-+][0-9A-Za-z.-]+)?$')
ASSET_ID_SPEC = ('asset_id 格式为 sciasset://{project-id}/{namespace}/{asset-type}/{slug}，'
                 '其中 project-id 为项目编号（见项目任务书首页）、namespace 为自拟命名空间、'
                 'asset-type 为资产类型、slug 为资产名称')

# 规范 v1.1 §4.1：sciasset://{project-id}/{namespace}/{asset-type}/{slug}，不含版本号
ASSET_ID = re.compile(r'^sciasset://([A-Za-z0-9][A-Za-z0-9._-]*)/([a-z0-9][a-z0-9-]*)'
                      r'/(data|tool|model|environment|skill|task|trajectory|benchmark)/([a-z0-9][a-z0-9._-]*)$')
ASSET_ID_LEGACY = re.compile(r'^sciasset://[A-Za-z0-9][A-Za-z0-9._-]*'
                             r'/(data|tool|model|environment|skill|task|trajectory|benchmark)/[A-Za-z0-9][A-Za-z0-9._-]*$')
DOMAIN_ID = re.compile(r'^domain:[a-z0-9-]+$')
CLASS_ID = re.compile(r'^(problem|operation|stage|task):[a-z0-9-]+$')
ABS_PATH = re.compile(r'^(/|[A-Za-z]:[\\/]|\\\\|file://)')


def now():
    return datetime.now(timezone.utc).astimezone().isoformat(timespec='seconds')


def collect_assets(path_list):
    """展开路径为 [(来源文件, 资产 dict)]。"""
    out, errors = [], []
    for raw in path_list:
        p = pathlib.Path(raw)
        files = []
        if p.is_dir():
            seen = set()
            for pat in ('asset.json', '*.asset.json', 'catalog.json', '*.json'):
                hits = [f for f in sorted(p.rglob(pat))
                        if 'asset-runs' not in f.parts and '.git' not in f.parts]
                for f in hits:
                    if f.resolve() in seen:
                        continue
                    seen.add(f.resolve())
                    files.append(f)
                if files:      # 找到标准命名即停止扩大范围
                    break
        elif p.is_file():
            files = [p]
        else:
            errors.append({'file': str(raw), 'message': '路径不存在'})
            continue
        for f in files:
            try:
                text = f.read_text(encoding='utf-8')
            except Exception as e:  # noqa: BLE001
                errors.append({'file': str(f), 'message': f'读取失败：{e}'})
                continue
            docs = []
            stripped = text.lstrip()
            if f.suffix == '.jsonl' or (stripped.startswith('{') and stripped.count('\n') > 0 and stripped[1:].lstrip().startswith('{') and '\n}' not in stripped.split('\n{')[0]):
                for i, line in enumerate(text.splitlines(), 1):
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        docs.append(json.loads(line))
                    except Exception:  # noqa: BLE001
                        pass
            if not docs:
                try:
                    docs = [json.loads(text)]
                except Exception as e:  # noqa: BLE001
                    errors.append({'file': str(f), 'message': f'JSON 解析失败：{e}'})
                    continue
            for doc in docs:
                out.extend(expand(doc, f))
    return out, errors


def expand(doc, file):
    """把一份文档展开成若干资产（支持单资产、资产数组、{assets:[...]}、catalog 包装）。"""
    found = []
    if isinstance(doc, list):
        for item in doc:
            found.extend(expand(item, file))
    elif isinstance(doc, dict):
        if 'asset_id' in doc and 'asset_type' in doc:
            found.append((file, doc))
        elif isinstance(doc.get('assets'), list):
            for item in doc['assets']:
                found.extend(expand(item, file))
        elif isinstance(doc.get('items'), list):
            for item in doc['items']:
                found.extend(expand(item, file))
    return found


class Result:
    def __init__(self, file, asset):
        self.file = str(file)
        self.asset_id = str(asset.get('asset_id', '') or '')
        self.asset_type = str(asset.get('asset_type', '') or '')
        self.errors = []
        self.warnings = []
        self.fingerprint = json.dumps(asset, ensure_ascii=False, sort_keys=True)
        self.also_in = []

    def err(self, path, msg):
        self.errors.append({'path': path, 'message': msg})

    def warn(self, path, msg):
        self.warnings.append({'path': path, 'message': msg})

    def as_dict(self):
        return {'file': self.file, 'asset_id': self.asset_id, 'asset_type': self.asset_type,
                'status': 'fail' if self.errors else 'pass',
                'errors': self.errors, 'warnings': self.warnings,
                'same_asset_also_in': self.also_in}



# ---------------------------------------------------------------- 媒体类型 / 格式一致性
# 规范 §5 要求 artifacts 给出访问位置与媒体类型，metadata 声明数据格式；两者必须能对上。
# 家族粒度用于判断"是同一类"（同一家族内不一致只记 warning，跨家族不一致记 error）。
EXT_FAMILY = {
    '.tif': 'raster:tiff', '.tiff': 'raster:tiff',
    '.asc': 'raster:esri-grid', '.adf': 'raster:esri-grid',
    '.nc': 'raster:netcdf', '.nc4': 'raster:netcdf',
    '.npy': 'array:npy', '.npz': 'array:npz',
    '.h5': 'array:hdf', '.hdf5': 'array:hdf',
    '.csv': 'tabular:csv', '.tsv': 'tabular:csv',
    '.xls': 'tabular:excel', '.xlsx': 'tabular:excel',
    '.shp': 'vector:shapefile', '.shx': 'vector:shapefile', '.dbf': 'vector:shapefile',
    '.prj': 'vector:shapefile', '.cpg': 'vector:shapefile', '.sbn': 'vector:shapefile',
    '.sbx': 'vector:shapefile',
    '.geojson': 'vector:geojson',
    '.gpkg': 'vector:geopackage',
    '.kml': 'vector:kml',
    '.zip': 'archive:zip', '.gz': 'archive:gzip', '.tar': 'archive:tar',
    '.onnx': 'model:onnx', '.pt': 'model:torch', '.pth': 'model:torch',
    '.pkl': 'model:pickle', '.joblib': 'model:pickle',
    '.json': 'neutral:json', '.jsonl': 'neutral:json',
    '.md': 'neutral:markdown', '.markdown': 'neutral:markdown',
    '.yaml': 'neutral:yaml', '.yml': 'neutral:yaml',
    '.txt': 'neutral:text', '.log': 'neutral:text',
    '.py': 'neutral:python', '.ipynb': 'neutral:python',
    '.xml': 'neutral:xml', '.html': 'neutral:xml',
    '.png': 'neutral:image', '.jpg': 'neutral:image', '.jpeg': 'neutral:image',
    '.svg': 'neutral:image', '.gif': 'neutral:image',
    '.pdf': 'neutral:document', '.docx': 'neutral:document', '.doc': 'neutral:document',
}

FORMAT_FAMILY = {
    'geotiff': 'raster:tiff', 'tiff': 'raster:tiff', 'tif': 'raster:tiff',
    'esri ascii grid': 'raster:esri-grid', 'ascii grid': 'raster:esri-grid',
    'esri grid': 'raster:esri-grid', 'arcinfo grid': 'raster:esri-grid', 'grid': 'raster:esri-grid',
    'netcdf': 'raster:netcdf',
    'numpy npy': 'array:npy', 'npy': 'array:npy', 'numpy': 'array:npy', 'npz': 'array:npz',
    'hdf': 'array:hdf', 'hdf5': 'array:hdf',
    'csv': 'tabular:csv', 'tsv': 'tabular:csv',
    'excel': 'tabular:excel', 'excel biff': 'tabular:excel', 'xls': 'tabular:excel', 'xlsx': 'tabular:excel',
    'shapefile': 'vector:shapefile', 'esri shapefile': 'vector:shapefile',
    'geojson': 'vector:geojson', 'geopackage': 'vector:geopackage', 'gpkg': 'vector:geopackage',
    'kml': 'vector:kml',
    'zip': 'archive:zip',
    'onnx': 'model:onnx',
    'json': 'neutral:json', 'markdown': 'neutral:markdown', 'md': 'neutral:markdown',
    'yaml': 'neutral:yaml', 'text': 'neutral:text', 'plain text': 'neutral:text',
}


def _declared_families(fmt):
    """把 metadata.format（字符串或数组）归一化成家族集合。"""
    if isinstance(fmt, str):
        items = [x.strip() for x in fmt.replace('/', ',').replace(';', ',').split(',') if x.strip()]
    elif isinstance(fmt, (list, tuple)):
        items = [str(x).strip() for x in fmt if str(x).strip()]
    else:
        return set()
    out = set()
    for it in items:
        key = it.lower()
        if key in FORMAT_FAMILY:
            out.add(FORMAT_FAMILY[key])
        else:
            for token, fam in FORMAT_FAMILY.items():
                if token in key:
                    out.add(fam)
                    break
    return out


def check_format_consistency(md, arts, r, atype):
    """artifact 的媒体类型/扩展名 与 metadata.format 的一致性。"""
    if not isinstance(arts, list) or not arts:
        return
    declared = _declared_families(md.get('format')) | _declared_families(md.get('formats'))
    if not declared:
        return                      # 未声明格式（tool/model/skill 常见）→ 不判
    seen_declared = set()
    for i, a in enumerate(arts):
        if not isinstance(a, dict):
            continue
        uri = str(a.get('uri') or '')
        low = uri.lower().split('?')[0]
        ext = ''
        for e in EXT_FAMILY:
            if low.endswith(e):
                ext = e if len(e) >= len(ext) else ext
        fam = EXT_FAMILY.get(ext)
        if not fam:
            continue
        if fam.startswith('neutral:'):
            continue                # 文档/脚本/配置类实体不参与格式一致性判定
        if fam in declared:
            seen_declared.add(fam)
            continue
        same_group = {d for d in declared if d.split(':')[0] == fam.split(':')[0]}
        if same_group:
            r.warn(f'spec.artifacts[{i}].uri',
                   f'实体类型 {fam} 与声明的 {sorted(same_group)} 属同一大类但格式不同（{uri}）；'
                   f'如为派生/附带格式，请在 metadata.format 或 artifact.role 中说明')
            seen_declared |= same_group
        else:
            r.err(f'spec.artifacts[{i}].uri',
                  f'实体类型 {fam} 与 metadata.format 声明 {sorted(declared)} 不一致（{uri}）')
    missing = declared - seen_declared
    if missing:
        r.warn('spec.metadata.format',
               f'声明了 {sorted(missing)}，但 artifacts 中没有对应类型的实体')


HASH_MAX_MB = 64                     # 声明的 sha256 只对不超过该体积的文件做实测校验
CHECK_ARTIFACT_FILES = True          # 是否核对本地实体路径/体积/摘要（--no-artifact-files 可关）
SCHEME = re.compile(r'^[A-Za-z][A-Za-z0-9+.-]*://')


def _resolve_local(base: Path, uri: str):
    """解析本地实体路径。返回 (path, inside)。

    **资产自包含**：目录外的引用一律不接受（资产入库后是独立单元，`../shared/...` 会当场失效）。
    跨资产/跨目录引用只允许通过本资产目录下的 `external_assets.json` 声明。
    """
    from urllib.parse import unquote
    rel = unquote(uri)
    if rel.startswith('./'):
        rel = rel[2:]
    p = (base / rel).resolve()
    b = base.resolve()
    return p, (p == b or b in p.parents)


def check_artifacts(arts, atype, r):
    """本地实体：路径必须可达；声明了体积/摘要就核对（大文件跳过哈希并提示）。"""
    import hashlib
    base = Path(r.file).resolve().parent
    for i, a in enumerate(arts):
        uri = str(a.get('uri') or '')
        if not uri or SCHEME.match(uri):
            continue                      # 远程实体不在本地校验范围
        path, in_dir = _resolve_local(base, uri)
        if not in_dir:
            r.err(f'spec.artifacts[{i}].uri',
                  f'不在资产目录内（§资产自包含）：{uri!r} 解析到 {path}；'
                  '资产入库后是独立单元，跨目录/跨资产引用必须改用资产目录下的 external_assets.json 声明')
            continue
        if not path.exists():
            r.err(f'spec.artifacts[{i}].uri',
                  f'资产目录内找不到该实体：{uri!r}（解析为 {path}）')
            continue
        size = a.get('size_bytes')
        size_key = 'size_bytes'
        if size is None:
            for alt in ('bytes', 'size'):
                if isinstance(a.get(alt), int):
                    size, size_key = a.get(alt), alt
                    r.warn(f'spec.artifacts[{i}].{alt}',
                           '规范字段名是 size_bytes；已按等价处理，建议改名为 size_bytes')
                    break
        if isinstance(size, int) and path.is_file():
            actual = path.stat().st_size
            if actual != size:
                r.err(f'spec.artifacts[{i}].{size_key}', f'声明 {size} 字节，实际 {actual} 字节')
        want = a.get('sha256')
        if want and path.is_file():
            mb = path.stat().st_size / 1048576
            if mb > HASH_MAX_MB:
                r.warn(f'spec.artifacts[{i}].sha256',
                       f'文件 {mb:.0f}MB 超过 {HASH_MAX_MB}MB，未实测摘要（可用 --hash-max-mb 调整）')
            else:
                h = hashlib.sha256()
                with open(path, 'rb') as f:
                    for c in iter(lambda: f.read(1 << 20), b''):
                        h.update(c)
                if not str(want).lower().startswith(h.hexdigest()[:len(str(want))]):
                    r.err(f'spec.artifacts[{i}].sha256',
                          f'摘要不符：声明 {str(want)[:12]}…，实际 {h.hexdigest()[:12]}…')


def check(asset, file):
    r = Result(file, asset)

    # ---- 顶层
    for key in ['schema_version', 'asset_id', 'asset_type', 'name', 'description',
                'asset_version', 'lifecycle', 'domains', 'provenance', 'governance', 'spec']:
        if key not in asset or asset.get(key) in (None, '', [], {}):
            r.err(key, '缺少必需字段或为空（规范 §4 公共包络）')

    for key in asset:
        if key not in TOP_LEVEL:
            r.warn(key, '顶层出现规范未定义的字段；实验性字段应放进 extensions（规范 §1）')

    sv = asset.get('schema_version')
    if isinstance(sv, str) and not SEMVER.match(sv):
        r.err('schema_version', f'应为语义化版本，实际 {sv!r}')

    aid = asset.get('asset_id')
    atype = asset.get('asset_type')
    if isinstance(aid, str):
        m = ASSET_ID.match(aid)
        if not m:
            if ASSET_ID_LEGACY.match(aid):
                r.err('asset_id', ASSET_ID_SPEC + f'；当前值 {aid!r} 缺少 project-id 段（规范 v1.1 §4.1）')
            else:
                r.err('asset_id', ASSET_ID_SPEC + f'；当前值 {aid!r} 不符合（§4.1）')
        else:
            project, name_space, seg_type, slug = m.groups()
            if atype and seg_type != atype:
                r.err('asset_id', f'类型段 {seg_type!r} 与 asset_type {atype!r} 不一致')
            if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]*', project):
                r.err('asset_id', f'project-id 段不合法：{project!r}')
            if name_space != name_space.lower():
                r.warn('asset_id', f'namespace 建议小写连字符：{name_space!r}')
            if slug != slug.lower():
                r.warn('asset_id', f'slug 建议小写：{slug!r}')
            av0 = str(asset.get('asset_version') or '')
            if av0 and slug.endswith('-' + av0):
                r.err('asset_id', 'asset_id 不得包含版本号（§4.1）；版本由 asset_version 表达，'
                                  '清单里的资产ID 才拼版本')
        if atype and str(atype) in aid and aid.rstrip('/').endswith(str(asset.get('asset_version'))):
            r.warn('asset_id', 'asset_id 以版本号结尾；规范要求 asset_id 稳定且不含版本号（§4.1）')
    if atype is not None and atype not in ASSET_TYPES:
        r.err('asset_type', f'不在八类枚举内：{atype!r}')

    av = asset.get('asset_version')
    if isinstance(av, str) and not SEMVER.match(av):
        r.err('asset_version', f'应为语义化版本，实际 {av!r}')

    lc = asset.get('lifecycle')
    if lc is not None and lc not in LIFECYCLE:
        r.err('lifecycle', f'不在 §7 五态内：{lc!r}')
    if lc == 'deprecated':
        ext = asset.get('extensions') or {}
        if not any(k in ext for k in ('deprecation_reason', 'replacement', 'replaced_by')):
            r.warn('lifecycle', 'deprecated 应提供替代资产或弃用原因（§7），建议写入 extensions')

    # ---- 分类
    domains = asset.get('domains')
    if isinstance(domains, list):
        if not domains:
            r.err('domains', '至少需要一个科学领域（§4.2）')
        for i, d in enumerate(domains):
            if not isinstance(d, dict) or 'id' not in d:
                r.err(f'domains[{i}]', '应为 {id: domain:*} 对象')
            elif not DOMAIN_ID.match(str(d['id'])):
                r.err(f'domains[{i}].id', f'应为 domain:* 编码，实际 {d["id"]!r}')

    tcs = asset.get('task_classes')
    if isinstance(tcs, list):
        for i, t in enumerate(tcs):
            if not isinstance(t, dict) or 'id' not in t:
                r.err(f'task_classes[{i}]', '应为 {id: problem:*|operation:*|stage:*|task:*} 对象')
            elif not CLASS_ID.match(str(t['id'])):
                r.err(f'task_classes[{i}].id', f'应为 problem:/operation:/stage:/task: 前缀，实际 {t["id"]!r}')


    if atype in ('skill', 'task', 'trajectory', 'benchmark') and not (isinstance(tcs, list) and tcs):
        r.err('task_classes', f'{atype} 至少需要一个任务概念（§4.2）；缺该字段或为空都不合规')

    tasks = asset.get('tasks')
    if atype == 'trajectory':
        if not isinstance(tasks, list) or not tasks:
            r.err('tasks', 'trajectory 必须关联任务（§5.7）')
    if isinstance(tasks, list):
        for i, t in enumerate(tasks):
            if not isinstance(t, dict) or 'id' not in t:
                r.err(f'tasks[{i}]', '应为 {id: sciasset://...} 对象')
            elif not str(t['id']).startswith('sciasset://'):
                r.err(f'tasks[{i}].id', f'应为 sciasset:// 引用，实际 {t["id"]!r}')

    # ---- provenance
    prov = asset.get('provenance')
    if isinstance(prov, dict):
        if not (prov.get('created_time') or prov.get('published_time')):
            r.err('provenance', '必须包含创建时间或发布时间（§6.1）')
        provs = prov.get('provider')
        if not isinstance(provs, list) or not provs:
            r.err('provenance.provider', '至少一个 provider（§6.1）')
        else:
            for i, p in enumerate(provs):
                if not isinstance(p, dict) or not p.get('name'):
                    r.err(f'provenance.provider[{i}]', '需要 name')
                elif not p.get('role'):
                    r.warn(f'provenance.provider[{i}].role', '建议声明 role（author/provider/creator…）')
        src = prov.get('source')
        if not isinstance(src, dict) or not src.get('kind'):
            r.err('provenance.source', '缺少 source.kind（§6.1）')
        else:
            if src['kind'] not in SOURCE_KIND:
                r.err('provenance.source.kind', f'应为 original/derived/generated，实际 {src["kind"]!r}')
            if src['kind'] in ('derived', 'generated') and not (src.get('uri') or src.get('derived_from')):
                r.err('provenance.source', '导入/派生/生成资产必须记录原始 URI 或 derived_from（§6.1）')

    # ---- governance
    gov = asset.get('governance')
    if isinstance(gov, dict):
        for key in ['license', 'access', 'risk_level', 'privacy', 'usage_constraints']:
            if key not in gov or gov.get(key) in (None, ''):
                r.err(f'governance.{key}', '五项治理信息必须声明（§6.2）')
        if gov.get('access') is not None and gov['access'] not in ACCESS:
            r.err('governance.access', f'不在枚举内：{gov["access"]!r}（public/restricted/private/internal）')
        if gov.get('risk_level') is not None and gov['risk_level'] not in RISK:
            r.err('governance.risk_level', f'不在枚举内：{gov["risk_level"]!r}（low/medium/high/prohibited）')
        if 'usage_constraints' in gov and not isinstance(gov['usage_constraints'], list):
            r.err('governance.usage_constraints', '应为数组（可为空数组）')

    # ---- spec
    spec = asset.get('spec')
    if isinstance(spec, dict):
        for key in SPEC_KEYS:
            if key not in spec:
                r.err(f'spec.{key}', 'spec 四件套缺失（metadata/artifacts/domain_attributes/asset_protocol，§5）')
        md = spec.get('metadata') if isinstance(spec.get('metadata'), dict) else {}
        arts = spec.get('artifacts') if isinstance(spec.get('artifacts'), list) else None
        proto = spec.get('asset_protocol') if isinstance(spec.get('asset_protocol'), dict) else {}

        if arts is None:
            r.err('spec.artifacts', '应为数组')
        else:
            for i, a in enumerate(arts):
                if not isinstance(a, dict):
                    r.err(f'spec.artifacts[{i}]', '应为对象')
                    continue
                for key in ('uri', 'media_type'):
                    if not a.get(key):
                        r.err(f'spec.artifacts[{i}].{key}', '缺少必需字段（§5 每条都要求访问位置与媒体类型）')
                uri = str(a.get('uri') or '')
                if uri and ABS_PATH.match(uri):
                    r.err(f'spec.artifacts[{i}].uri', f'不得使用本机/服务器绝对路径：{uri!r}')
                if uri.startswith('s3://') and not a.get('sha256'):
                    r.warn(f'spec.artifacts[{i}].sha256', '远程实体建议记录内容摘要（§1）')
                role = a.get('role')
                if role and not re.match(r'^[a-z0-9][a-z0-9._-]*$', str(role)):
                    r.warn(f'spec.artifacts[{i}].role', f'role 建议用「小写+连字符」的稳定取值：{role!r}')
            if CHECK_ARTIFACT_FILES:
                check_artifacts(arts, atype, r)

        # asset_protocol 里的本地路径同样要求落在资产目录内
        proto_base = Path(r.file).resolve().parent
        for key in ('repository_uri', 'path', 'entry', 'readme'):
            v = proto.get(key) if isinstance(proto, dict) else None
            if not isinstance(v, str) or not v or SCHEME.match(v):
                continue
            pth, in_dir = _resolve_local(proto_base, v)
            if not in_dir:
                r.err(f'spec.asset_protocol.{key}',
                      f'不在资产目录内（§资产自包含）：{v!r} 解析到 {pth}；'
                      '协议/共享文件应随资产目录交付，跨资产引用改用 external_assets.json')

        check_format_consistency(md, arts, r, atype)

        # 类型专有
        if atype == 'data':
            if not (md.get('format') or md.get('data_model')):
                r.err('spec.metadata', 'data 需声明 format 或 data_model（§5.1 / 注册规则）')
            if arts is not None and not arts:
                r.err('spec.artifacts', 'data 至少一个数据实体（§5.1）')
        if atype == 'tool':
            if not md.get('interface_type'):
                r.err('spec.metadata.interface_type', 'tool 需声明接口类型（§5.2）')
            if arts is not None and not arts:
                r.err('spec.artifacts', 'tool 至少一个实现或描述文件（§5.2）')
        if atype == 'model':
            if not md.get('model_type'):
                r.err('spec.metadata.model_type', 'model 需声明模型类型（§5.3）')
            if arts is not None and not arts:
                r.err('spec.artifacts', 'model 至少一个权重或服务实体（§5.3）')
        if atype == 'environment' and arts is not None and not arts:
            r.err('spec.artifacts', 'environment 至少一个镜像/锁文件/环境定义（§5.4）')
        if atype == 'skill' and arts is not None and not arts:
            r.err('spec.artifacts', 'skill 至少一个指令或流程文件（§5.5）')
        if atype == 'benchmark':
            ev = proto.get('evaluate') if isinstance(proto.get('evaluate'), dict) else None
            if not ev:
                r.err('spec.asset_protocol.evaluate', 'benchmark 必须提供评测协议 evaluate（§5.8）')
            else:
                if not isinstance(ev.get('metrics'), list) or not ev.get('metrics'):
                    r.err('spec.asset_protocol.evaluate.metrics', '至少一个指标（§5.8）')
                else:
                    for i, m in enumerate(ev['metrics']):
                        if not isinstance(m, dict) or not m.get('id'):
                            r.err(f'spec.asset_protocol.evaluate.metrics[{i}]', '需要 id')
                        elif m.get('direction') and m['direction'] not in ('maximize', 'minimize'):
                            r.err(f'spec.asset_protocol.evaluate.metrics[{i}].direction', 'direction 应为 maximize/minimize')
                if not ev.get('entrypoint'):
                    r.warn('spec.asset_protocol.evaluate.entrypoint', '建议给出唯一评测入口（§5.8）')

        # asset_protocol 形态
        if not (proto.get('repository_uri') or all(k in proto for k in ('readme', 'prepare', 'use', 'validate'))):
            r.warn('spec.asset_protocol', '建议给出 repository_uri(+revision+path) 或 readme/prepare/use/validate 四段（§5）')

        # prohibited 硬约束
        if isinstance(gov, dict) and gov.get('risk_level') == 'prohibited' and arts:
            r.err('spec.artifacts', 'risk_level=prohibited 只允许登记审计元数据，不得提供可执行内容或下载位置（§6.2）')
    return r


def builtin_schema_validate(inst, schema, path=''):
    """内置轻量 JSON Schema 校验：覆盖包络 schema 实际用到的关键字。

    支持 type/required/properties/items/minItems/minLength/pattern/enum/const/allOf/anyOf。
    遇到未支持的关键字不静默放过——由调用方在结果里标注覆盖范围。
    """
    errs = []

    def type_ok(v, t):
        return {
            'object': isinstance(v, dict), 'array': isinstance(v, list),
            'string': isinstance(v, str), 'integer': isinstance(v, int) and not isinstance(v, bool),
            'number': isinstance(v, (int, float)) and not isinstance(v, bool),
            'boolean': isinstance(v, bool), 'null': v is None,
        }.get(t, True)

    t = schema.get('type')
    if t and not type_ok(inst, t):
        return [(path or '(root)', f'应为 {t}')]
    if 'const' in schema and inst != schema['const']:
        errs.append((path or '(root)', f'应为常量 {schema["const"]!r}'))
    if 'enum' in schema and inst not in schema['enum']:
        errs.append((path or '(root)', f'取值不在枚举内：{inst!r}'))
    if isinstance(inst, str):
        if 'minLength' in schema and len(inst) < schema['minLength']:
            errs.append((path or '(root)', f'长度应 ≥{schema["minLength"]}'))
        if 'pattern' in schema and not re.search(schema['pattern'], inst):
            errs.append((path or '(root)', f'不匹配 pattern {schema["pattern"]!r}'))
    if isinstance(inst, dict):
        for req in schema.get('required') or []:
            if req not in inst:
                errs.append((f'{path}/{req}'.strip('/'), f'缺少必需字段 {req}'))
        for k, sub in (schema.get('properties') or {}).items():
            if k in inst and isinstance(sub, dict):
                errs += builtin_schema_validate(inst[k], sub, f'{path}/{k}'.strip('/'))
    if isinstance(inst, list):
        if 'minItems' in schema and len(inst) < schema['minItems']:
            errs.append((path or '(root)', f'元素数应 ≥{schema["minItems"]}'))
        if isinstance(schema.get('items'), dict):
            for i, it in enumerate(inst):
                errs += builtin_schema_validate(it, schema['items'], f'{path}/{i}'.strip('/'))
    for sub in schema.get('allOf') or []:
        errs += builtin_schema_validate(inst, sub, path)
    if schema.get('anyOf'):
        if not any(not builtin_schema_validate(inst, sub, path) for sub in schema['anyOf']):
            errs.append((path or '(root)', 'anyOf 的所有分支都不满足'))
    return errs


def _import_jsonschema():
    """优先用环境里的 jsonschema；没有就用 skill 自带的 vendor/（离线可用）。"""
    try:
        import jsonschema  # type: ignore
        return jsonschema
    except Exception:  # noqa: BLE001
        pass
    vendor = Path(__file__).resolve().parents[1] / 'vendor'
    if vendor.is_dir() and str(vendor) not in sys.path:
        sys.path.insert(0, str(vendor))
        try:
            import jsonschema  # type: ignore
            return jsonschema
        except Exception:  # noqa: BLE001
            return None
    return None


def optional_jsonschema_check(assets, engine='auto'):
    """用内置 schema 复核一遍。有 jsonschema 用它，没有就用内置轻量实现——绝不静默跳过。"""
    if not SCHEMA_FILE.exists():
        return 'schema 文件缺失，未做交叉校验', []
    schema = json.loads(SCHEMA_FILE.read_text(encoding='utf-8'))
    notes = []
    use = engine
    if use in ('auto', 'jsonschema'):
        jsonschema = _import_jsonschema()
        try:
            if jsonschema is None:
                raise ImportError('jsonschema 不可用')
            validator = jsonschema.Draft202012Validator(schema)
            for file, asset in assets:
                errs = sorted(validator.iter_errors(asset), key=lambda e: list(e.path))[:5]
                for e in errs:
                    notes.append({'file': str(file), 'asset_id': asset.get('asset_id'),
                                  'path': '/'.join(str(x) for x in e.path) or '(root)',
                                  'message': e.message})
            from importlib.metadata import version as _pkg_version
            try:
                ver = _pkg_version('jsonschema')
            except Exception:  # noqa: BLE001
                ver = '4.x'
            return f'jsonschema {ver}', notes
        except Exception:  # noqa: BLE001
            if use == 'jsonschema':
                return 'jsonschema 不可用（已按要求不降级）', []
    kw = 'type/required/properties/items/minItems/minLength/pattern/enum/const/allOf/anyOf'
    for file, asset in assets:
        for path, msg in builtin_schema_validate(asset, schema)[:5]:
            notes.append({'file': str(file), 'asset_id': asset.get('asset_id'),
                          'path': path, 'message': msg})
    return f'builtin（{kw}）', notes


def main(argv=None):
    ap = argparse.ArgumentParser(description='校验资产描述文件是否符合规范 v1.0 公共包络（只读校验，不构造包络）')
    ap.add_argument('paths', nargs='+', help='资产描述文件或目录（递归找 asset.json / catalog.json）')
    ap.add_argument('--json-out', help='把结果写成 JSON 报告')
    ap.add_argument('--strict', action='store_true', help='warning 也视为失败')
    ap.add_argument('--quiet', action='store_true', help='只输出汇总')
    ap.add_argument('--hash-max-mb', type=int, default=64,
                    help='声明了 sha256 的文件，超过该体积只提示不实测（默认 64MB）')
    ap.add_argument('--schema-engine', choices=['auto', 'jsonschema', 'builtin'], default='auto',
                    help='schema 交叉校验引擎：auto=有 jsonschema 就用，否则用内置轻量实现')
    ap.add_argument('--no-artifact-files', action='store_true',
                    help='不核对 artifacts 的本地路径/体积/摘要（仅用于实体不随包交付的场景）')
    args = ap.parse_args(argv)
    global HASH_MAX_MB, CHECK_ARTIFACT_FILES
    HASH_MAX_MB = args.hash_max_mb
    CHECK_ARTIFACT_FILES = not args.no_artifact_files

    assets, read_errors = collect_assets(args.paths)
    if not assets and read_errors:
        payload = {'tool': 'validate_envelope.py', 'schema': str(SCHEMA_FILE), 'checked_at': now(),
                   'summary': {'assets': 0, 'passed': 0, 'failed': 0, 'warnings': 0},
                   'read_errors': read_errors, 'results': []}
        if args.json_out:
            pathlib.Path(args.json_out).write_text(json.dumps(_stamp(payload), ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 2
    if not assets:
        print('未找到任何资产描述文件（期望 asset.json 或含 asset_id+asset_type 的 JSON）', file=sys.stderr)
        return 2

    results = [check(asset, file) for file, asset in assets]

    # 同一 asset_id 可能同时出现在 catalog.json 与 assets/*/asset.json：
    # 内容完全一致 → 合并为一条（记录 also_in）；内容不一致 → 两条都保留并互相标记冲突（§7 唯一性）
    by_id = {}
    merged = []
    for r in results:
        if not r.asset_id:
            merged.append(r)
            continue
        same = by_id.setdefault(r.asset_id, [])
        if not same:
            same.append(r)
            merged.append(r)
            continue
        first = same[0]
        if any(x.fingerprint == r.fingerprint for x in same):
            first.also_in.append(r.file)
        else:
            same.append(r)
            merged.append(r)
            first.err('asset_id', f'同一 asset_id 在 {r.file} 有内容不一致的描述（§7 要求唯一）')
            r.err('asset_id', f'同一 asset_id 在 {first.file} 有内容不一致的描述（§7 要求唯一）')
    results = merged

    jsonschema_version, schema_notes = optional_jsonschema_check(assets, args.schema_engine)

    failed = [r for r in results if r.errors]
    warn_count = sum(len(r.warnings) for r in results)
    payload = {
        'tool': 'validate_envelope.py',
        'spec': '科学智能体资产统一表征体系设计 v1.0 §4/§5/§6/§7',
        'schema': str(SCHEMA_FILE),
        'checked_at': now(),
        'inputs': [str(p) for p in args.paths],
        'summary': {'assets': len(results), 'passed': len(results) - len(failed), 'failed': len(failed),
                    'warnings': warn_count,
                    'jsonschema_cross_check': jsonschema_version or 'schema 文件缺失，未做交叉校验'},
        'read_errors': read_errors,
        'results': [r.as_dict() for r in results],
        'jsonschema_cross_check_notes': schema_notes,
    }
    if args.json_out:
        out = pathlib.Path(args.json_out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(_stamp(payload), ensure_ascii=False, indent=2), encoding='utf-8')

    if args.quiet:
        print(f"assets={payload['summary']['assets']} passed={payload['summary']['passed']} "
              f"failed={payload['summary']['failed']} warnings={payload['summary']['warnings']}")
    else:
        for r in results:
            mark = 'FAIL' if r.errors else ('WARN' if r.warnings else 'PASS')
            print(f"[{mark}] {r.asset_id or r.file}")
            for e in r.errors:
                print(f"    error  {e['path']}: {e['message']}")
            for w in r.warnings:
                print(f"    warn   {w['path']}: {w['message']}")
        s = payload['summary']
        print(f"\n合计 {s['assets']} 个资产：通过 {s['passed']}，失败 {s['failed']}，warning {s['warnings']}"
              f"（jsonschema 交叉校验：{s['jsonschema_cross_check']}）")

    if failed or (args.strict and warn_count):
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
