#!/usr/bin/env python3
"""Sage A1.5 附加测试：判定资产是否涉及大模型 API，并解析/探测 Sage 配置。

为什么单列：涉及大模型的资产（skill 资产必然要在 agent 上验收，其余类型视 API 证据）在通用后端上的通过率
不能代表在 Sage A1.5 上的表现，Sage 结果属于**兼容性附加信息，不作为资产合格与否的判定项**。因此本脚本只
负责「要不要测」和「配置够不够」，真正的测试用资产包自带的正确性 harness 把后端换成 Sage 来跑。

子命令
------
  detect  <资产根目录>           扫描包内是否涉及大模型 API 调用，写 sage-need.json
  config                         解析 Sage 配置（CLI > 环境变量），可选探测连通性，写 sage-config.json
  report  <run 目录>             把探测/测试结论落成 sage-a1.5-report.json（供报告引用）

配置来源（按优先级）
--------------------
  1. CLI：--base-url / --model / --api-key-env
  2. 环境变量：SAGE_BASE_URL / SAGE_MODEL / SAGE_API_KEY（别名：SAGE_LLM_BASE_URL / SAGE_LLM_MODEL / SAGE_LLM_API_KEY）
  3. 宿主机的 DSH 配置：~/.dsh/profiles/*/cordis.patch.yml 里 models 含 Sage 的 provider（取 baseURL 与 apiKeyEnv），
     密钥从环境变量或 ~/.dsh/.credentials.yaml 的 refs 解析（只读取，不落盘、不打印）
  **找不到配置时退出码 3，并把缺哪几项、怎么提供打印出来——必须立即把这段反馈给用户，由用户提供；
  不得静默跳过，也不得拿其他模型后端的结果代填 Sage 结果。**

退出码：0 成功；2 参数/IO 错误；3 缺少 Sage 配置（需用户提供）；4 配置齐全但探测失败。

用法
----
  python scripts/sage_probe.py detect <资产根目录> --json-out <run>/sage-need.json
  python scripts/sage_probe.py config --json-out <run>/sage-config.json [--probe]
  python scripts/sage_probe.py report <run> --status blocked_missing_config --note "缺 SAGE_MODEL"
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

try:                                     # 布局约定：跳过核验期挂载进来的外部资产副本
    from _layout import is_mounted_external as _is_mounted
except Exception:                        # noqa: BLE001
    def _is_mounted(d):
        return (d / '.mounted-external.json').is_file()

try:                                     # 版本戳：报告与证据 JSON 都要能追溯校验 skill 版本
    from _version import stamp as _stamp
except Exception:                        # noqa: BLE001
    def _stamp(payload):
        return payload

LLM_ASSET_TYPES = {'skill', 'task', 'trajectory', 'benchmark', 'model'}
ENV_KEYS = {
    'base_url': ['SAGE_BASE_URL', 'SAGE_LLM_BASE_URL'],
    'model': ['SAGE_MODEL', 'SAGE_LLM_MODEL'],
    'api_key': ['SAGE_API_KEY', 'SAGE_LLM_API_KEY'],
}
# 包内出现这些字样就算“涉及大模型 API”，逐条留证据
PATTERNS = [
    (re.compile(r'LLM_BASE_URL|LLM_API_KEY|LLM_MODEL'), 'env-template', '环境文件里声明了 LLM_* 端点/模型/密钥'),
    (re.compile(r'OPENAI_BASE_URL|OPENAI_API_KEY|OPENAI_MODEL'), 'env-template', '环境文件里声明了 OPENAI_* 端点/模型/密钥'),
    (re.compile(r'/v1/chat/completions|chat\.completions'), 'code', '代码里调用了 OpenAI 兼容的 chat/completions'),
    (re.compile(r'model_backed|model-backed'), 'harness', '自带「模型驱动」适配器/替身'),
    (re.compile(r'--backend-name\s+sage-a1\.5|backend_name.*sage'), 'harness', '自带 Sage A1.5 后端开关'),
    (re.compile(r'"llm"\s*:|dependencies\.llm|external_services.*模型'), 'asset-json', 'asset.json 把模型服务列为依赖'),
]
SCAN_SUFFIX = {'.py', '.json', '.md', '.example', '.env', '.yaml', '.yml', '.txt', '.sh', '.toml'}
SKIP_PARTS = {'asset-runs', '.git', '__pycache__', 'node_modules'}


class _Force:
    assume_llm = False


args_force = _Force()
MAX_HITS = 8


def now() -> str:
    return datetime.now().astimezone().isoformat(timespec='seconds')


def load_json(p):
    return json.loads(Path(p).read_text(encoding='utf-8'))


# ---------------------------------------------------------------- detect
def detect(root: Path) -> dict:
    reasons, evidence = [], []
    if not root.is_dir():
        return {'needed': False, 'error': f'目录不存在：{root}', 'reasons': [], 'evidence': []}
    # 1) 资产类型（包根 + 子目录都要读：资产常放在 <包>/<资产名>/asset.json）
    assets = []
    for name in ('catalog.json', 'asset.json'):
        p = root / name
        if p.exists():
            try:
                doc = load_json(p)
            except Exception:  # noqa: BLE001
                doc = None
            if isinstance(doc, list):
                assets = doc
            elif isinstance(doc, dict):
                assets = doc.get('assets') or ([doc] if 'asset_id' in doc else [])
            if assets:
                break
    if not assets:
        for f in sorted(root.rglob('asset.json')):
            rel = f.relative_to(root)
            if SKIP_PARTS & set(rel.parts) or _is_mounted(f.parent):
                continue
            try:
                doc = load_json(f)
            except Exception:  # noqa: BLE001
                continue
            if isinstance(doc, dict) and 'asset_id' in doc:
                assets.append(doc)
    types = sorted({a.get('asset_type') for a in assets if a.get('asset_type')})
    llm_types = [t for t in types if t in LLM_ASSET_TYPES]
    # 类型线索：skill 资产本身就是「给 agent 用」的，验收要上 agent（见下方第 3 步），故直接判为适用；
    # 其余类型只作提示，仍需大模型 API 配置/调用证据（第 2 步）。
    if llm_types:
        type_hint = '包内含 ' + '、'.join(llm_types) + ' 类型资产（仅作提示，需看是否有大模型 API 证据）'
    else:
        type_hint = None

    # 2) 文本证据
    hits = {}
    for f in root.rglob('*'):
        if not f.is_file() or f.suffix.lower() not in SCAN_SUFFIX:
            continue
        rel = f.relative_to(root)
        if SKIP_PARTS & set(rel.parts):
            continue
        try:
            text = f.read_text(encoding='utf-8', errors='replace')
        except Exception:  # noqa: BLE001
            continue
        for pat, kind, why in PATTERNS:
            m = pat.search(text)
            if not m:
                continue
            key = (kind, why)
            if key not in hits:
                line = text[: m.start()].count('\n') + 1
                hits[key] = {'path': str(rel), 'line': line, 'kind': kind, 'why': why,
                             'snippet': text.splitlines()[line - 1][:120].strip()}
            if len(hits) >= MAX_HITS:
                break
        if len(hits) >= MAX_HITS:
            break
    evidence = list(hits.values())
    if evidence:
        reasons.append('包内检出大模型 API 相关配置/调用（%d 处证据）' % len(evidence))
    # 3) skill 资产：验收必须上 agent（用模型完成真实任务），所以 Sage A1.5 兼容性测试**适用**——
    #    不是"无 LLM 故不适用"。是否真的能跑取决于 Sage 配置，但判定结论不能写成 not_applicable。
    if 'skill' in types:
        reasons.insert(0, '包内含 skill 资产：验收须在 agent 上完成，Sage A1.5 应按同一 harness 执行（仅替换后端模型）')
    needed = bool(evidence) or ('skill' in types) or bool(getattr(args_force, 'assume_llm', False))
    if needed and type_hint:
        reasons.insert(0, type_hint)
    return {
        'checked_at': now(), 'asset_root': str(root), 'asset_types': types,
        'type_hint': type_hint, 'assumed': bool(getattr(args_force, 'assume_llm', False)),
        'needed': needed, 'reasons': reasons, 'evidence': evidence,
        'nothing_to_do': None if needed else '包内既无 skill 资产也无大模型 API 调用迹象，Sage A1.5 测试不适用（not_applicable_no_llm_asset）',
    }


# ---------------------------------------------------------------- config
def resolve_config(args) -> tuple[dict, list[str]]:
    cfg = {'base_url': args.base_url, 'model': args.model,
           'api_key_env': args.api_key_env, 'sources': {}}
    for field, names in ENV_KEYS.items():
        if cfg.get(field):
            cfg['sources'][field] = 'cli'
            continue
        for n in names:
            v = os.environ.get(n)
            if v:
                cfg[field] = v
                cfg['sources'][field] = f'env:{n}'
                break
    # 3) 宿主机的 DSH 配置（只读）
    if not cfg.get('base_url') or not cfg.get('model'):
        disc = discover_dsh_sage(Path.home())
        if disc.get('base_url'):
            for field in ('base_url', 'model'):
                if not cfg.get(field) and disc.get(field):
                    cfg[field] = disc[field]
                    cfg['sources'][field] = disc['source']
            if disc.get('api_key') and not cfg.get('api_key'):
                cfg['api_key'] = disc['api_key']
                cfg['sources']['api_key'] = disc.get('api_key_source') or 'dsh'
    missing = [f for f in ('base_url', 'model') if not cfg.get(f)]
    if not cfg.get('api_key_env') and not cfg.get('api_key'):
        key_found = any(os.environ.get(n) for n in ENV_KEYS['api_key'])
        if not key_found:
            missing.append('api_key')
        else:
            cfg['sources']['api_key'] = 'env'
    return cfg, missing


def discover_dsh_sage(home: Path) -> dict:
    """从 DSH 的 profile/凭据里找 Sage 后端：返回 {base_url, model, api_key, source, api_key_env}。

    按 provider 块解析（同一文件里可能有多个 provider，不能把 A 的 baseURL 和 B 的模型拼在一起）。
    """
    out = {}
    prof_root = home / '.dsh' / 'profiles'
    if not prof_root.is_dir():
        return out
    for f in sorted(prof_root.glob('*/cordis.patch.yml')):
        try:
            lines = f.read_text(encoding='utf-8', errors='replace').splitlines()
        except Exception:  # noqa: BLE001
            continue
        try:
            pidx = next(i for i, l in enumerate(lines) if l.strip() == 'providers:')
        except StopIteration:
            continue
        pindent = len(lines[pidx]) - len(lines[pidx].lstrip())
        blocks, cur = [], None
        for l in lines[pidx + 1:]:
            if not l.strip():
                continue
            ind = len(l) - len(l.lstrip())
            if ind <= pindent:
                break
            m = re.match(r'^(\s+)([A-Za-z0-9_.-]+):\s*$', l)
            if m and len(m.group(1)) == pindent + 2:
                cur = {'id': m.group(2), 'lines': []}
                blocks.append(cur)
            elif cur is not None:
                cur['lines'].append(l)
        for b in blocks:
            base_url = api_key_env = None
            models = []
            for l in b['lines']:
                st = l.strip()
                if st.startswith('baseURL:'):
                    base_url = st.split(':', 1)[1].strip()
                elif st.startswith('apiKeyEnv:'):
                    api_key_env = st.split(':', 1)[1].strip()
                elif re.match(r'-\s*id:', st):
                    models.append(st.split(':', 1)[1].strip())
            if base_url and any(re.search('sage', m, re.I) for m in models):
                sage_model = next(m for m in models if re.search('sage', m, re.I))
                out = {'base_url': base_url, 'model': sage_model, 'api_key_env': api_key_env,
                       'provider': b['id'], 'source': f'dsh-profile:{f}#{b["id"]}'}
                break
        if out:
            break
    if not out:
        return out
    key = None
    names = ([out.get('api_key_env')] if out.get('api_key_env') else []) + ENV_KEYS['api_key']
    for n in names:
        if n and os.environ.get(n):
            key = os.environ[n]
            out['api_key_source'] = f'env:{n}'
            break
    if not key:
        cred = home / '.dsh' / '.credentials.yaml'
        if cred.exists():
            try:
                text = cred.read_text(encoding='utf-8', errors='replace')
                for n in names:
                    if not n:
                        continue
                    m = re.search(r'^\s+' + re.escape(n) + r':\s*(\S+)', text, re.M)
                    if m:
                        key = m.group(1)
                        out['api_key_source'] = f'dsh-credentials:{cred.name}#{n}'
                        break
            except Exception:  # noqa: BLE001
                pass
    out['api_key'] = key
    return out


def probe(base_url: str, api_key: str | None, model: str) -> dict:
    out = {'base_url': base_url, 'model': model, 'checked_at': now()}
    headers = {'Content-Type': 'application/json'}
    if api_key:
        headers['Authorization'] = f'Bearer {api_key}'
    t0 = time.time()
    try:
        req = urllib.request.Request(base_url.rstrip('/') + '/models', headers=headers)
        with urllib.request.urlopen(req, timeout=20) as resp:
            body = json.loads(resp.read().decode('utf-8', 'replace'))
        ids = [m.get('id') for m in (body.get('data') or [])][:40]
        out.update({'models_endpoint': 'ok', 'http_status': resp.status,
                    'models': ids, 'model_listed': model in ids if ids else None,
                    'seconds': round(time.time() - t0, 2)})
    except urllib.error.HTTPError as e:
        out.update({'models_endpoint': 'http_error', 'http_status': e.code,
                    'detail': e.read().decode('utf-8', 'replace')[:200], 'seconds': round(time.time() - t0, 2)})
    except Exception as e:  # noqa: BLE001
        out.update({'models_endpoint': 'unreachable', 'detail': f'{type(e).__name__}: {e}'[:200],
                    'seconds': round(time.time() - t0, 2)})
    return out


def user_request_block(missing: list[str]) -> str:
    want = {
        'base_url': 'SAGE_BASE_URL   —— Sage A1.5 的 OpenAI 兼容地址（形如 http://<Sage 服务>/v1）',
        'model': 'SAGE_MODEL      —— 该服务上的模型/服务名（核验方给出的 served name）',
        'api_key': 'SAGE_API_KEY    —— 若服务需要鉴权则提供；本地 vLLM 可留空',
    }
    lines = ['', '=' * 72,
             '【需要用户提供 Sage A1.5 配置】系统里没有找到可用的 Sage 配置，以下项目缺失：']
    lines += ['  - ' + want[m] for m in missing]
    lines += ['',
              '提供方式（任选其一，密钥只放环境变量，不要写进文件或交付包）：',
              '  export SAGE_BASE_URL=...  SAGE_MODEL=...  SAGE_API_KEY=...',
              '  或直接告诉我这三个值，我在当前会话里注入后再跑。',
              '配置到位后，Sage 结果会写进报告的「Sage A1.5 兼容性测试」小节与 sage-a1.5-report.json，',
              '**只作为附加信息，不参与该资产合格与否的判定**。',
              '=' * 72]
    return '\n'.join(lines)


# ---------------------------------------------------------------- report
def write_report(run: Path, status: str, note: str, extra: dict | None = None) -> dict:
    need_p, cfg_p = run / 'sage-need.json', run / 'sage-config.json'
    need = load_json(need_p) if need_p.exists() else {}
    cfg = load_json(cfg_p) if cfg_p.exists() else {}
    payload = {
        'tool': 'sage_probe.py', 'generated_at': now(), 'run': run.name,
        'asset_involves_llm': need.get('needed'),
        'status': status,
        'note': note,
        'is_acceptance_criterion': False,
        'statement': 'Sage A1.5 结果属于兼容性附加测试：无论通过与否，都不改变该资产合格与否的判定，也不进入通过率与覆盖度分母。',
        'need': need, 'config': {k: v for k, v in cfg.items() if k != 'api_key'},
        'evidence': extra or {},
    }
    (run / 'sage-a1.5-report.json').write_text(json.dumps(_stamp(payload), ensure_ascii=False, indent=2), encoding='utf-8')
    return payload


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description='Sage A1.5 附加测试：需求判定 + 配置解析/探测')
    sub = ap.add_subparsers(dest='cmd', required=True)

    d = sub.add_parser('detect', help='扫描资产包是否需要 Sage 测试')
    d.add_argument('asset_root')
    d.add_argument('--assume-llm', action='store_true',
                   help='包内没有明显证据、但人工确认涉及大模型时强制判为需要测试')
    d.add_argument('--json-out')

    c = sub.add_parser('config', help='解析并可选探测 Sage 配置')
    c.add_argument('--base-url')
    c.add_argument('--model')
    c.add_argument('--api-key-env')
    c.add_argument('--probe', action='store_true')
    c.add_argument('--json-out')

    r = sub.add_parser('report', help='写 sage-a1.5-report.json')
    r.add_argument('run')
    r.add_argument('--status', required=True)
    r.add_argument('--note', default='')
    r.add_argument('--evidence')

    args = ap.parse_args(argv)

    if args.cmd == 'detect':

        args_force.assume_llm = getattr(args, 'assume_llm', False)
        payload = detect(Path(args.asset_root).expanduser().resolve())
        if args.json_out:
            p = Path(args.json_out); p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(json.dumps(_stamp(payload), ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps({k: payload[k] for k in ('needed', 'reasons', 'asset_types')}, ensure_ascii=False, indent=2))
        if not payload['needed']:
            print(payload['nothing_to_do'], file=sys.stderr)
        return 0

    if args.cmd == 'config':
        cfg, missing = resolve_config(args)
        key = os.environ.get(cfg.get('api_key_env') or 'SAGE_API_KEY') if not cfg.get('api_key') else cfg.get('api_key')
        out = {'tool': 'sage_probe.py', 'checked_at': now(),
               'base_url': cfg.get('base_url'), 'model': cfg.get('model'),
               'api_key_present': bool(key), 'sources': cfg.get('sources'), 'missing': missing}
        rc = 0
        if missing:
            out['status'] = 'missing_config'
            out['user_request'] = user_request_block(missing)
            rc = 3
        else:
            out['status'] = 'configured'
            if args.probe:
                pr = probe(cfg['base_url'], key, cfg['model'])
                out['probe'] = pr
                ok = pr.get('models_endpoint') == 'ok' or pr.get('http_status') in (200, 401, 403)
                out['probe_ok'] = bool(ok) and pr.get('models_endpoint') != 'unreachable'
                rc = 0 if out['probe_ok'] else 4
        if args.json_out:
            p = Path(args.json_out); p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(json.dumps(_stamp(out), ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps({k: out.get(k) for k in ('status', 'base_url', 'model', 'api_key_present', 'missing')},
                         ensure_ascii=False, indent=2))
        if rc == 3:
            print(out['user_request'])
        elif rc == 4:
            print('\n【Sage 配置存在但连通性探测失败】请确认地址/模型名/密钥是否有效：'
                  + json.dumps(out.get('probe'), ensure_ascii=False)[:300])
        return rc

    run = Path(args.run).expanduser().resolve()
    if not run.is_dir():
        print(f'run 目录不存在：{run}', file=sys.stderr)
        return 2
    ev = json.loads(args.evidence) if args.evidence else None
    payload = write_report(run, args.status, args.note, ev)
    print(json.dumps({k: payload[k] for k in ('status', 'asset_involves_llm', 'is_acceptance_criterion')},
                     ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
