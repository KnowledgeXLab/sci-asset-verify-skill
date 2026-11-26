#!/usr/bin/env python3
"""在**独立 CLI 进程**里跑 skill 资产的 agent 验收（不在当前会话内跑），再按 readme 的门槛判产物。

为什么必须单独用 CLI：skill 是给 agent 用的，验收要让一个 agent 真实完成任务；但**不能**在当前核验会话里做
（会污染会话、也无法为 Sage 换后端）。所以固定用 harness 的 CLI 一次性调用模式起独立进程。

harness 选定顺序
----------------
1. 首选 `validate/readme.md` 里建议的 harness（按名字匹配预设：dsh / codex / claude / cursor / gemini / opencode / aider，
   见 scripts/harnesses/ 适配器包）；
2. 建议的没装，就按预设顺序挑一个**已安装**的，并在证据里注明"建议 X、实际用 Y"；
3. 一个都没装 → 报错，并提示用 `--harness-cmd` 就地固化本地命令（用户环境千变万化，预设只是加速）；
4. **provider 用 harness 自己配置的默认值**，本脚本不覆盖；
5. Sage 测试**沿用同一 harness**，只换后端：从 `SAGE_BASE_URL`/`SAGE_API_KEY`（含 `SAGE_LLM_*` 别名）读取，模型固定 `Sage-A1.5`，
   按预设声明的方式注入（env 或配置补丁）；预设声明不支持时明确失败，不偷偷用默认后端。

用法：
  python scripts/run_skill_agent.py --skill <副本>/<资产目录> --out-dir <run>/agent/<资产> \\
      [--harness auto|dsh|codex|claude|cursor|gemini|opencode|aider] \\
      [--harness-cmd "<命令模板，用 {prompt} 占位>"] \\
      [--backend harness|sage] [--model M] [--timeout 900] [--json-out F] [--dry-run]
  python scripts/run_skill_agent.py --list-harnesses

退出码：0 判定通过；1 判定不通过；2 参数/环境问题；3 无法启动 harness。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

try:
    from _version import stamp as _stamp
except Exception:  # noqa: BLE001
    def _stamp(payload):
        return payload

SPEC = 'validate/readme.md'
# Sage 模型名由平台固定（Sage-A1.5），不通过环境变量配置
SAGE_MODEL = 'Sage-A1.5'
SAGE_ENV = {'base_url': ['SAGE_BASE_URL', 'SAGE_LLM_BASE_URL'],
            'api_key': ['SAGE_API_KEY', 'SAGE_LLM_API_KEY']}
DSH_HOME_AUTO = 'auto'


def dsh_home_runtime(out: Path, skill_name: str, mode: str = 'auto') -> tuple[dict, str]:
    """nested dsh 要写 $DSH_HOME/profiles/*/cordis.yml；核验沙箱常把它挡掉。

    返回 (要注入的环境变量, 说明)。mode:
      default  → 用宿主的 ~/.dsh
      isolated → 复制 profiles/headless + node_modules 软链到 run 目录下；**凭据绝不落盘**，
                 只在内存里读出 *_API_KEY 并通过环境变量注入子进程
      auto     → 宿主 DSH_HOME 可写就用宿主的，否则隔离
    """
    real = Path(os.environ.get('DSH_HOME') or (Path.home() / '.dsh'))
    if mode == 'default':
        return {}, f'用宿主 DSH_HOME={real}'
    if mode == 'auto' and os.access(real / 'profiles' / 'headless', os.W_OK):
        return {}, f'用宿主 DSH_HOME={real}（可写）'

    home = out.parent / f'.dsh-home-{skill_name}'
    prof = home / 'profiles'
    if not (prof / 'headless').exists():
        prof.mkdir(parents=True, exist_ok=True)
        src = real / 'profiles'
        if (src / 'headless').is_dir():
            shutil.copytree(src / 'headless', prof / 'headless', dirs_exist_ok=True)
        nm_src, nm_dst = src / 'node_modules', prof / 'node_modules'
        if nm_src.is_dir() and not nm_dst.exists():
            try:
                nm_dst.symlink_to(nm_src, target_is_directory=True)
            except OSError:
                shutil.copytree(nm_src, nm_dst, dirs_exist_ok=True)
    # 凭据**只读进内存、经环境变量传给子进程**，绝不在 run 目录留副本（交付包不得含密钥）
    env = {'DSH_HOME': str(home)}
    env.update(credential_env(real))
    return env, (f'隔离 DSH_HOME={home}（工作区内可写）；凭据仅以环境变量注入，未落盘'
                 f'（注入键：{", ".join(sorted(credential_env(real))) or "无"}）')


def credential_env(real: Path) -> dict:
    """从凭据文件内存解析 *_API_KEY，返回要注入的环境变量；文件不存在或解析不到就返回空。"""
    f = real / '.credentials.yaml'
    if not f.is_file():
        return {}
    out = {}
    try:
        text = f.read_text(encoding='utf-8', errors='replace')
    except Exception:  # noqa: BLE001
        return {}
    for line in text.splitlines():
        m = re.match(r'^\s*([A-Z][A-Z0-9_]*_API_KEY)\s*:\s*["\']?([^"\'\s#]+)', line)
        if m:
            out[m.group(1)] = m.group(2)
    return out


def resolve_harness_env(env_map: dict) -> tuple[dict, dict]:
    """按注册表的 env_map 解析 harness 需要的环境变量。

    解析顺序前缀：
      env:NAME        → 当前进程环境变量
      bashrc:NAME     → ~/.bashrc 里的 `export NAME=...`
      codex-auth:NAME → ~/.codex/auth.json 里的字段
      cred:NAME       → ~/.dsh/.credentials.yaml 的 *_API_KEY
    返回 (值字典, 来源字典)；只记录来源，不打印也不落盘任何值。
    """
    import re

    def bashrc_exports() -> dict:
        """~/.bashrc 的全部 `export NAME=值`（只进内存，用于补全派生写法）。"""
        f = Path.home() / ".bashrc"
        if not f.is_file():
            return {}
        out = {}
        pat = re.compile(r"^\s*export\s+([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.+?)\s*$")
        for line in f.read_text(encoding="utf-8", errors="replace").splitlines():
            m = pat.match(line)
            if m and not m.group(2).startswith("$("):     # 跳过 $(命令) 形式的动态值
                out[m.group(1)] = m.group(2).strip().strip("'\"")
        return out

    def from_bashrc(name: str) -> str:
        return bashrc_exports().get(name, "")

    def from_codex_auth(name: str) -> str:
        f = Path.home() / ".codex" / "auth.json"
        if not f.is_file():
            return ""
        try:
            return str((json.loads(f.read_text(encoding="utf-8")) or {}).get(name) or "")
        except Exception:  # noqa: BLE001
            return ""

    creds = credential_env(Path(os.environ.get("DSH_HOME") or (Path.home() / ".dsh")))
    def expand(raw: str, scope: dict) -> str:
        """展开 ${VAR}、${VAR%suffix}、${VAR#prefix}、$VAR（最多 3 轮，够 bashrc 这种派生写法）。"""
        import re as _re
        out = raw
        for _ in range(3):
            def rep(m):
                name, op, pat = m.group(1), m.group(2), m.group(3)
                base = scope.get(name) or os.environ.get(name) or ""
                if op == "%" and pat and base.endswith(pat):
                    return base[: -len(pat)]
                if op == "#" and pat and base.startswith(pat):
                    return base[len(pat):]
                return base
            new = _re.sub(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?:([%#])([^}]*))?\}", rep, out)
            new = _re.sub(r"(?<!\$)\$([A-Za-z_][A-Za-z0-9_]*)", lambda m: scope.get(m.group(1)) or os.environ.get(m.group(1), ""), new)
            if new == out:
                break
            out = new
        return out

    values, sources = {}, {}
    for name, order in (env_map or {}).items():
        for cand in order or []:
            kind, _, arg = str(cand).partition(":")
            val = ""
            if kind == "env":
                val = os.environ.get(arg or name, "")
            elif kind == "bashrc":
                val = from_bashrc(arg or name)
            elif kind == "codex-auth":
                val = from_codex_auth(arg or name)
            elif kind == "cred":
                val = creds.get(arg or name, "")
            if val:
                scope = dict(bashrc_exports())
                scope.update(os.environ)
                scope.update(values)
                val = expand(val, scope)
                if val:
                    values[name] = val
                    sources[name] = kind + ":" + (arg or name)
                    break
    return values, sources


def scrub_secrets(base: Path) -> list[str]:
    """删掉运行目录里任何凭据文件/隔离 home 目录（纵深防御，正常流程本就该没有）。"""
    removed = []
    if not base.is_dir():
        return removed
    for f in list(base.rglob('.credentials.yaml')) + list(base.rglob('*.credentials.yaml')):
        try:
            f.unlink()
            removed.append(str(f))
        except OSError:
            pass
    for pattern in ('.dsh-home-*', 'codex-home', 'claude-home'):
        for d in list(base.rglob(pattern)):
            if d.is_dir():
                shutil.rmtree(d, ignore_errors=True)
                removed.append(str(d))
    # 兜底：任何残留的 agent 凭据文件
    for name in ('auth.json', 'credentials.json'):
        for f in base.rglob(name):
            if f.is_file() and any(x in str(f) for x in ('codex-home', 'claude-home', '.dsh-home')):
                f.unlink(missing_ok=True)
                removed.append(str(f))
    return removed


SAGE_PATCH = """# Sage 后端补丁：注册端砚 provider 并把默认模型切到 Sage-A1.5（harness 不变）
- id: llm-pi-ai
  config:
    providers:
      %(provider)s:
        displayName: 端砚
        apiKeyEnv: INKSTONE_API_KEY
        api: openai-completions
        baseURL: %(base_url)s
        models:
          - id: %(model)s
            name: %(model)s
- id: agent-default-model
  config:
    provider: %(provider)s
    model: %(model)s
"""


# ---------------------------------------------------------------- readme 解析
def sections(text: str) -> dict:
    out, cur, buf = {}, None, []
    for line in text.splitlines():
        m = re.match(r'^#{2,4}\s*(.+?)\s*$', line)
        if m:
            if cur is not None:
                out[cur] = '\n'.join(buf).strip()
            cur, buf = m.group(1).lower(), []
        elif cur is not None:
            buf.append(line)
    if cur is not None:
        out[cur] = '\n'.join(buf).strip()
    return out


def pick(sec: dict, *keys) -> str:
    for head, body in sec.items():
        if any(k in head for k in keys):
            return body
    return ''


def parse_spec(skill: Path) -> dict:
    text = (skill / SPEC).read_text(encoding='utf-8')
    sec = sections(text)
    agent = pick(sec, 'agent', 'harness')
    task = pick(sec, '任务', '怎么测')
    gate = pick(sec, '门槛', '验收')
    harness = ''
    m = re.search(r'建议\s*agent\s*harness[：:]\s*(.+)', agent, re.I)
    if m:
        harness = m.group(1).strip()
    model = ''
    m = re.search(r'建议\s*agent\s*model[：:]\s*(.+)', agent, re.I)
    if m:
        model = m.group(1).strip()
    prompt = ''
    m = re.search(r'```[a-zA-Z]*\n(.+?)```', task, re.S)
    if m:
        prompt = m.group(1).strip()
    gate_cmd = ''
    m = re.search(r'(python\s+validate/\S+[^\n`]*)', gate)
    if m:
        gate_cmd = m.group(1).strip()
    return {'text': text, 'harness_hint': harness, 'model_hint': model,
            'task': task, 'gate': gate, 'prompt': prompt, 'gate_cmd': gate_cmd}


# ---------------------------------------------------------------- harness 预设
def load_registry() -> dict:
    """harness 注册表：来自 scripts/harnesses 适配器包（每个 harness 一个文件）。

    环境变量 ASSET_VERIFY_HARNESS_REGISTRY 指向 JSON 时优先用它（自测/私有分发）。
    """
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from harnesses import legacy_registry      # noqa: WPS433
        return legacy_registry()
    except Exception:  # noqa: BLE001
        return {'preference': [], 'harnesses': {}}


def sage_env() -> dict:
    """Sage 后端配置：只有 base_url 与 api_key；模型名固定 SAGE_MODEL。"""
    out = {'model': SAGE_MODEL}
    for key, names in SAGE_ENV.items():
        for n in names:
            if os.environ.get(n):
                out[key] = os.environ[n]
                break
    return out


def _matches(preset: dict, name: str, hint: str) -> bool:
    if not hint:
        return False
    h = hint.lower()
    return name in h or (preset.get('display', '').lower() in h)


def resolve_harness(spec: dict, registry: dict, wanted: str = 'auto') -> dict:
    presets = registry.get('harnesses') or {}
    order = list(registry.get('preference') or presets.keys())
    hint = spec['harness_hint'] or ''
    preferred = [n for n in order if n in presets and _matches(presets[n], n, hint)]
    # 候选顺序：指定项优先，其后是 readme 建议项与其余预设（去重）。
    # 修复：指定项未安装时，之前会把 preferred 一起排除，导致明明装了 dsh 也判"找不到可用 harness"。
    candidates = []
    for n in ([wanted] if wanted and wanted != 'auto' else []) + preferred + list(order):
        if n in presets and n not in candidates:
            candidates.append(n)
    for name in candidates:
        preset = presets.get(name)
        if not preset:
            continue
        if preset.get('headless') is False:        # 桌面应用等：明确不支持无头调用
            continue
        if wanted in ("auto", "", None) and str(preset.get('verified') or '').startswith("blocked"):
            continue                                # 已知不可用（如未鉴权）不参与 auto 选择
        for b in preset.get('bins') or []:
            exe = shutil.which(b)
            if exe:
                if wanted and wanted != 'auto' and name != wanted:
                    reason = f'指定的 {wanted} 本机未安装，退用已安装的 {name}'
                elif _matches(preset, name, hint):
                    reason = '按 readme 建议'
                else:
                    reason = f'readme 未指定可用的 harness，退用已安装的 {name}'
                return {'name': name, 'preset': preset, 'bin': exe, 'hint': hint, 'reason': reason}
    return {}


def list_harnesses(registry: dict) -> int:
    print(f'预设 harness（{len(registry.get("harnesses") or {})} 个，按优先级排列）：')
    print('  优先级 1=整包核验跑通 2=最小包跑通 3=可调用但不稳定 4=仅登记/未鉴权')
    for name, preset in (registry.get('harnesses') or {}).items():
        found = next((shutil.which(b) for b in preset.get('bins') or [] if shutil.which(b)), None)
        mark = f'✓ {found}' if found else '✗ 未安装'
        pr = preset.get('priority', 9)
        ver = preset.get('verified') or '-'
        auto = 'auto✓' if (found and preset.get('headless') is not False
                           and not str(ver).startswith('blocked')) else 'auto✗'
        print(f'  P{pr} {name:9s} {preset.get("display", ""):26s} {mark:34s} {auto}  {ver}')
        print(f'            argv: {" ".join(preset.get("argv") or [])}')
    print('未列出的 harness 可用 --harness-cmd "<命令模板>" 就地固化（模板里用 {prompt} 占位）。')
    return 0


# ---------------------------------------------------------------- 执行
def build_prompt(spec: dict, skill: Path, out: Path) -> str:
    body = spec['prompt'] or spec['task']
    return (f'{body}\n\n'
            '【硬约束】\n'
            f'1. 技能与输入都在这个目录里（只读）：{skill}\n'
            f'2. 当前工作目录就是产物目录（{out}）；产物写在这里即可，不要写进技能目录\n'
            '3. 完成后用一句话回报产物绝对路径。')


def build_argv(harness: dict, prompt: str, model: str, extra: dict | None = None) -> list:
    preset = harness['preset']
    argv = [harness['bin'] if a == '{bin}' else a for a in (preset.get('argv') or ['{prompt}'])]
    argv = [prompt if a == '{prompt}' else a for a in argv]
    for k, v in (extra or {}).items():                 # {skill_dir} / {package} 等占位符
        argv = [v if a == '{' + k + '}' else a for a in argv]
    if model:
        if any(a == '{model}' for a in argv):          # argv 里直接写了 {model}（如 opencode run --model {model}）
            argv = [model if a == '{model}' else a for a in argv]
        elif preset.get('model_flag'):
            argv += [model if a == '{model}' else a for a in preset['model_flag']]
    else:
        # 没指定模型：把 {model} 连同它前面的 flag 一起去掉，交给 harness 用自身默认模型
        flags = set((preset.get('model_flag') or [])[:1]) or {'--model', '-m'}
        cleaned = []
        for a in argv:
            if a == '{model}':
                if cleaned and cleaned[-1] in flags:
                    cleaned.pop()
                continue
            cleaned.append(a)
        argv = cleaned
    return argv


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description='独立 CLI 进程里跑 skill 的 agent 验收')
    ap.add_argument('--skill', help='副本内的 skill 资产目录')
    ap.add_argument('--out-dir', help='agent 产物目录（同时作为 agent 工作目录）')
    ap.add_argument('--harness', default='auto', help='auto（默认）或预设名')
    ap.add_argument('--harness-cmd', help='自定义命令模板（用 {prompt} 占位），用于预设之外的 harness')
    ap.add_argument('--backend', choices=['harness', 'sage'], default='harness',
                    help='harness=用 harness 自己的默认 provider；sage=同一 harness 换 Sage 后端（SAGE_* 环境变量）')
    ap.add_argument('--model', help='覆盖模型（默认：harness 自行决定；Sage 时固定 Sage-A1.5）')
    ap.add_argument('--timeout', type=int, default=900)
    ap.add_argument('--json-out')
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--list-harnesses', action='store_true',
                    help='列出预设 harness 与本机安装情况')
    ap.add_argument('--dsh-home', choices=['auto', 'default', 'isolated'], default='auto',
                    help='nested dsh 的 DSH_HOME 策略（沙箱挡住默认目录时用 isolated）')
    ap.add_argument('--show-help', metavar='HARNESS',
                    help='打印该 harness 的 --help，用于预设之外的"就地分析"（也可直接跑 <bin> --help）')
    args = ap.parse_args(argv)

    registry = load_registry()
    if args.list_harnesses:
        return list_harnesses(registry)
    if args.show_help:
        preset = (registry.get('harnesses') or {}).get(args.show_help) or {}
        exe = next((shutil.which(b) for b in (preset.get('bins') or [args.show_help]) if shutil.which(b)), None)
        if not exe:
            print(f'找不到可执行文件：{args.show_help}', file=sys.stderr)
            return 2
        print(f'# {exe} --help（据此确认一次性调用的子命令与自动化开关，必要时用 --harness-cmd 固化）')
        print(subprocess.run([exe, '--help'], capture_output=True, text=True).stdout)
        if preset.get('argv'):
            print(f"# 当前预设 argv：{' '.join(preset['argv'])}")
        return 0
    if not args.skill or not args.out_dir:
        print('需要 --skill 与 --out-dir', file=sys.stderr)
        return 2

    skill = Path(args.skill).expanduser().resolve()
    out = Path(args.out_dir).expanduser().resolve()
    if not (skill / SPEC).is_file():
        print(f'缺少 {skill / SPEC}', file=sys.stderr)
        return 2
    spec = parse_spec(skill)

    if args.harness_cmd:
        bin0 = (args.harness_cmd.split() or [''])[0]
        harness = {'name': 'custom', 'bin': shutil.which(bin0) or bin0, 'hint': spec['harness_hint'],
                   'preset': {'display': '自定义命令', 'argv': args.harness_cmd.split(),
                              'sage': {'kind': 'env', 'env': {}}},
                   'reason': '用户用 --harness-cmd 就地指定'}
    else:
        harness = resolve_harness(spec, registry, args.harness)
    if not harness:
        names = ', '.join((registry.get('harnesses') or {}).keys())
        print(f'找不到可用的 harness。已检测预设：{names}\n'
              '· 装其中一个即可；或\n'
              '· 用 --harness-cmd "<命令模板>" 把本地命令就地固化（模板里用 {prompt} 占位）；\n'
              '· --list-harnesses 查看本机安装情况。', file=sys.stderr)
        return 3

    sage = sage_env()
    substituted = ''
    if (args.harness == 'auto' and harness['name'] != 'custom' and harness['hint']
            and not _matches(harness['preset'], harness['name'], harness['hint'])):
        substituted = f"readme 建议 {harness['hint']}；本机没有可用的，改用已安装的 {harness['preset'].get('display')}"

    model = args.model or (SAGE_MODEL if args.backend == 'sage' else '')
    provider = 'sage-env' if args.backend == 'sage' else 'harness-default'
    if args.backend == 'sage' and harness['name'] != 'custom':
        sage_mech = (harness['preset'].get('sage') or {}).get('kind')
        if sage_mech == 'unsupported':
            print(f"{harness['preset'].get('display')} 无法指向自定义后端（{(harness['preset'].get('sage') or {}).get('why', '')}）；"
                  '请换一个支持 env 注入的 harness 或用 --harness-cmd 指定。', file=sys.stderr)
            return 2
        if not sage.get('base_url') or not sage.get('model'):
            print('Sage 测试需要 SAGE_BASE_URL（或 SAGE_LLM_BASE_URL）环境变量；模型名固定 Sage-A1.5；'
                  'README 声明的是同一 harness 换后端，缺配置时不做降级。', file=sys.stderr)
            return 2

    out.mkdir(parents=True, exist_ok=True)
    prompt = build_prompt(spec, skill, out)
    cmd = build_argv(harness, prompt, model)
    try:                                            # 让宿主 shell 配置（端点/代理/PATH）生效
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from harnesses import get as _get_adapter
        _ad = _get_adapter(harness.get('name') or '')
        if _ad is not None:
            cmd = _ad.wrap(cmd)
    except Exception:  # noqa: BLE001
        pass
    env = dict(os.environ)
    dsh_note = ''
    if harness['name'] == 'dsh':
        extra, dsh_note = dsh_home_runtime(out, skill.name, args.dsh_home)
        env.update(extra)

    patch_file = None
    if args.backend == 'sage' and harness['name'] != 'custom':
        sage_mech = (harness['preset'].get('sage') or {})
        if sage_mech.get('kind') == 'patch':
            patch_file = Path(tempfile.mkdtemp(prefix='sage-patch-')) / 'sage.yml'
            patch_file.write_text(SAGE_PATCH % {'provider': sage_mech.get('provider', 'inkstone'),
                                                'model': sage['model'], 'base_url': sage['base_url']},
                                  encoding='utf-8')
            # --patch 是 launcher 选项，必须紧跟 --profile <name>；放到 app 参数之后会被 dsh 拒（unknown option）
            if '--profile' in cmd:
                pi = cmd.index('--profile') + 2
            else:
                pi = 1
            cmd = cmd[:pi] + ['--patch', str(patch_file)] + cmd[pi:]
            env.setdefault('INKSTONE_API_KEY', sage.get('api_key', ''))
        elif sage_mech.get('kind') == 'env':
            for k, template in (sage_mech.get('env') or {}).items():
                env[k] = (template.replace('{base_url}', sage.get('base_url', ''))
                                 .replace('{api_key}', sage.get('api_key', ''))
                                 .replace('{model}', sage.get('model', '')))

    if args.dry_run:
        print(json.dumps({'harness': harness['name'], 'display': harness['preset'].get('display'),
                          'argv': cmd[:8] + (['…'] if len(cmd) > 8 else []), 'cwd': str(out),
                          'backend': args.backend, 'provider': provider, 'model': model or '(harness 默认)',
                          'dsh_home': dsh_note, 'cmd_full': cmd[:10],
                          'sage_env_used': sorted(sage.keys()) if args.backend == 'sage' else [],
                          'substituted': substituted or None, 'reason': harness['reason']},
                         ensure_ascii=False, indent=2))
        return 0

    print(f"启动 agent：{harness['preset'].get('display')}（backend={args.backend}, model={model or 'harness 默认'}）",
          file=sys.stderr)
    try:
        proc = subprocess.run(cmd, cwd=str(out), capture_output=True, text=True,
                              timeout=args.timeout, env=env)
    except subprocess.TimeoutExpired:
        print(f'agent 执行超时（{args.timeout}s）', file=sys.stderr)
        return 3
    (out.parent / f'agent-{skill.name}-stdout.txt').write_text(proc.stdout or '', encoding='utf-8')
    (out.parent / f'agent-{skill.name}-stderr.txt').write_text(proc.stderr or '', encoding='utf-8')
    scrubbed = scrub_secrets(out.parent)

    # 宿主 DSH_HOME 写不进去（沙箱拒绝）时，隔离一份再试一次；第一次现场留在 attempt-01 前缀文件里
    retried_isolated = False
    if (harness['name'] == 'dsh' and proc.returncode != 0 and not dsh_note.startswith('隔离')):
        blob = f'{proc.stdout or ""}{proc.stderr or ""}'.lower()
        if any(k in blob for k in ('permission denied', 'eacces', 'sandbox', 'read-only', '无法写入')):
            shutil.copy2(out.parent / f'agent-{skill.name}-stdout.txt',
                         out.parent / f'agent-{skill.name}-attempt-01-sandbox-stdout.txt')
            shutil.copy2(out.parent / f'agent-{skill.name}-stderr.txt',
                         out.parent / f'agent-{skill.name}-attempt-01-sandbox-stderr.txt')
            extra, dsh_note = dsh_home_runtime(out, skill.name, 'isolated')
            env.update(extra)
            proc = subprocess.run(cmd, cwd=str(out), capture_output=True, text=True,
                                  timeout=args.timeout, env=env)
            (out.parent / f'agent-{skill.name}-stdout.txt').write_text(proc.stdout or '', encoding='utf-8')
            (out.parent / f'agent-{skill.name}-stderr.txt').write_text(proc.stderr or '', encoding='utf-8')
            retried_isolated = True

    gate_cmd = (spec['gate_cmd'] or '').replace('<agent 产物目录>', str(out)).replace('<agent 产物>', str(out))
    if not gate_cmd:
        print('readme 的「通过门槛」里没找到验证脚本命令', file=sys.stderr)
        return 2
    if '--artifact' not in gate_cmd:
        gate_cmd = f'{gate_cmd} --artifact {out}'
    gate_proc = subprocess.run(gate_cmd.split(), cwd=str(skill), capture_output=True, text=True)
    gate_ok = gate_proc.returncode == 0
    retried = []
    if not gate_ok:                     # 有的模板写 "结果放 out/" → 允许下探一层重试，并记录
        for sub in sorted([d for d in out.rglob('*') if d.is_dir()], key=lambda d: len(d.parts))[:3]:
            if not any(f.is_file() for f in sub.iterdir()):
                continue
            parts, cmd2, skip = gate_cmd.split(), [], False
            for tok in parts:
                if skip:
                    skip = False
                    continue
                if tok == '--artifact':
                    skip = True
                    continue
                cmd2.append(tok)
            cmd2 += ['--artifact', str(sub)]
            gp = subprocess.run(cmd2, cwd=str(skill), capture_output=True, text=True)
            retried.append({'dir': str(sub), 'gate_rc': gp.returncode})
            if gp.returncode == 0:
                gate_proc, gate_cmd, gate_ok = gp, ' '.join(cmd2), True
                break
    hint = None
    if not gate_ok:
        strays = [str(p) for p in list(skill.rglob('interpolated.csv')) + list(skill.rglob('report.json'))
                  + list(out.rglob('interpolated.csv')) + list(out.rglob('report.json'))][:5]
        if strays:
            hint = f'agent 产物没写到指定目录，发现这些位置的产物：{strays}'

    payload = _stamp({'tool': 'run_skill_agent.py', 'skill': str(skill), 'out_dir': str(out),
                      'harness': harness['name'], 'harness_display': harness['preset'].get('display'),
                      'harness_hint': harness['hint'], 'harness_reason': harness['reason'],
                      'substituted_harness': substituted or None,
                      'backend': args.backend, 'provider': provider, 'model': model or '(harness 默认)',
                      'cmd_head': cmd[:8], 'dsh_home': dsh_note, 'agent_rc': proc.returncode,
                      'secrets_scrubbed': scrubbed,
                      'gate_cmd': gate_cmd, 'gate_rc': gate_proc.returncode,
                      'gate_stdout': (gate_proc.stdout or '')[-1500:],
                      'gate_stderr': (gate_proc.stderr or '')[-1500:],
                      'artifact_retry_dirs': retried, 'stray_artifacts': hint,
                      'retried_with_isolated_dsh_home': retried_isolated,
                      'verdict': 'pass' if gate_ok else 'fail'})
    print(json.dumps({'harness': harness['name'], 'backend': args.backend,
                      'agent_rc': proc.returncode, 'gate_rc': gate_proc.returncode,
                      'verdict': payload['verdict'], 'hint': hint}, ensure_ascii=False))
    if args.json_out:
        p = Path(args.json_out)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
    return 0 if gate_ok else 1


if __name__ == '__main__':
    raise SystemExit(main())
