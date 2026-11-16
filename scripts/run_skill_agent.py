#!/usr/bin/env python3
"""在**独立 CLI 进程**里跑 skill 资产的 agent 验收（不在当前会话内跑），再按 readme 的门槛判产物。

为什么必须单独用 CLI：skill 是给 agent 用的，验收要让一个 agent 真实完成任务；但**不能**在当前核验会话里做
（会污染会话、也无法为 Sage 换后端）。所以固定用 harness 的 CLI 模式起一个独立进程：

  1. 首选 `validate/readme.md` 里建议的 harness；本机若有同名 CLI（DSH → `dsh --profile headless`）就直接用；
  2. 找不到就退回"当前 harness 的 CLI 模式"（同样是 `dsh --profile headless`），并在证据里注明替代原因；
  3. Sage 测试**沿用同一 harness**，只把后端换成 Sage（`--backend sage` → 注入 provider/model 补丁）。

产物判定：跑 readme 里声明的门槛脚本（形如 `validate/validate.py ... --artifact <目录>`），
判的是 agent 交出的产物，不是重跑资产自带脚本。

用法：
  python scripts/run_skill_agent.py --skill <副本>/<资产目录> --out-dir <run>/agent/<资产> \
      [--backend harness|sage] [--provider P --model M] [--timeout 900] [--json-out F] [--dry-run]

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
DEFAULT_PROVIDER, DEFAULT_MODEL = 'agent-center', 'DeepSeek-V4.1-Flash-1017'
SAGE_PROVIDER, SAGE_MODEL = 'inkstone', 'Sage-A1.5'
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
    case = ''
    m = re.search(r'--case\s+(\S+)', gate_cmd)
    if m:
        case = m.group(1)
    return {'text': text, 'harness_hint': harness, 'model_hint': model,
            'task': task, 'gate': gate, 'prompt': prompt, 'gate_cmd': gate_cmd, 'case': case}


def find_harness(spec: dict) -> tuple[str, str]:
    """返回 (harness 名称, 可执行命令)；找不到返回 ('', '')。"""
    dsh = shutil.which('dsh')
    hint = (spec['harness_hint'] or '').lower()
    if dsh and ('dsh' in hint or 'deepseek harness' in hint or not hint):
        return 'dsh --profile headless', dsh
    if dsh:
        return 'dsh --profile headless', dsh          # 当前 harness 的 CLI 模式
    return '', ''


def build_prompt(spec: dict, skill: Path, out: Path) -> str:
    body = spec['prompt'] or spec['task']
    return (f'{body}\n\n'
            '【硬约束】\n'
            f'1. 技能与输入都在这个目录里（只读）：{skill}\n'
            f'2. 当前工作目录就是产物目录（{out}）；产物写在这里即可，不要写进技能目录\n'
            '3. 完成后用一句话回报产物绝对路径。')


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description='独立 CLI 进程里跑 skill 的 agent 验收')
    ap.add_argument('--skill', required=True, help='副本内的 skill 资产目录')
    ap.add_argument('--out-dir', required=True, help='agent 产物目录（也是 agent 的工作输出位置）')
    ap.add_argument('--backend', choices=['harness', 'sage'], default='harness')
    ap.add_argument('--provider')
    ap.add_argument('--model')
    ap.add_argument('--timeout', type=int, default=900)
    ap.add_argument('--json-out')
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args(argv)

    skill = Path(args.skill).expanduser().resolve()
    out = Path(args.out_dir).expanduser().resolve()
    if not (skill / SPEC).is_file():
        print(f'缺少 {skill / SPEC}', file=sys.stderr)
        return 2
    spec = parse_spec(skill)
    harness, exe = find_harness(spec)
    if not harness:
        print('系统中找不到可用的 harness CLI（dsh）', file=sys.stderr)
        return 3
    substituted = ''
    if spec['harness_hint'] and 'dsh' not in spec['harness_hint'].lower():
        substituted = f"readme 建议 {spec['harness_hint']}；系统中无该 harness，改用当前 harness 的 CLI（dsh --profile headless）"

    provider = args.provider or (SAGE_PROVIDER if args.backend == 'sage' else DEFAULT_PROVIDER)
    model = args.model or (SAGE_MODEL if args.backend == 'sage' else DEFAULT_MODEL)
    out.mkdir(parents=True, exist_ok=True)
    cwd = out                                     # 工作目录=产物目录：workspace-write 才允许写，且相对路径也落在正确位置
    cwd.mkdir(parents=True, exist_ok=True)
    prompt = build_prompt(spec, skill, out)

    cmd = [exe, '--profile', 'headless', '--permission-mode', 'workspace-write', '--', prompt]
    patch_file = None
    if args.backend == 'sage':
        patch_file = Path(tempfile.mkdtemp(prefix='sage-patch-')) / 'sage.yml'
        patch_file.write_text(SAGE_PATCH % {'provider': provider, 'model': model,
                                            'base_url': 'https://discovery-api.intern-ai.org.cn/v1'},
                              encoding='utf-8')
        cmd = [exe, '--profile', 'headless', '--patch', str(patch_file),
               '--permission-mode', 'workspace-write', '--', prompt]

    if args.dry_run:
        print(json.dumps({'harness': harness, 'provider': provider, 'model': model,
                          'cmd': cmd[:6] + ['<prompt>'], 'prompt': prompt[:300]},
                         ensure_ascii=False, indent=2))
        return 0

    print(f'启动 agent：{harness}（backend={args.backend}, model={provider}/{model}）', file=sys.stderr)
    try:
        proc = subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True, timeout=args.timeout)
    except subprocess.TimeoutExpired:
        print(f'agent 执行超时（{args.timeout}s）', file=sys.stderr)
        return 3
    (out.parent / f'agent-{skill.name}-stdout.txt').write_text(proc.stdout or '', encoding='utf-8')
    (out.parent / f'agent-{skill.name}-stderr.txt').write_text(proc.stderr or '', encoding='utf-8')

    # 门槛：跑 readme 声明的验证脚本，判 agent 产物
    gate_cmd = spec['gate_cmd'].replace('<agent 产物目录>', str(out)).replace('<agent 产物>', str(out))
    if '--artifact' not in gate_cmd:
        gate_cmd = f'{gate_cmd} --artifact {out}'
    gate_proc = subprocess.run(gate_cmd.split(), cwd=str(skill), capture_output=True, text=True)
    gate_ok = gate_proc.returncode == 0

    # 有的说明模板会写 "结果放到 out/" → agent 就在产物目录下再建一层；允许下探一层以内重试，并记录用过的目录
    retried = []
    if not gate_ok:
        for sub in sorted([d for d in out.rglob('*') if d.is_dir()], key=lambda d: len(d.parts))[:3]:
            if not any(f.is_file() for f in sub.iterdir()):
                continue
            parts = gate_cmd.split()
            cmd2, skip = [], False
            for tok in parts:                        # 去掉原来的 --artifact <out> 两段
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

    # 产物没落在指定目录时，给出线索（agent 写错地方属于失败，不做搬运掩盖）
    hint = None
    if not gate_ok:
        strays = [str(p) for p in list(skill.rglob('interpolated.csv')) + list(skill.rglob('report.json'))
                  + list(out.rglob('interpolated.csv')) + list(out.rglob('report.json'))][:5]
        if strays:
            hint = f'agent 产物没写到指定目录，发现这些位置的产物：{strays}'

    payload = _stamp({'tool': 'run_skill_agent.py', 'skill': str(skill), 'out_dir': str(out),
                      'harness': harness, 'harness_hint': spec['harness_hint'],
                      'substituted_harness': substituted or None,
                      'backend': args.backend, 'provider': provider, 'model': model,
                      'agent_rc': proc.returncode, 'agent_exit': 'ok' if proc.returncode == 0 else 'failed',
                      'gate_cmd': gate_cmd, 'gate_rc': gate_proc.returncode,
                      'gate_stdout': (gate_proc.stdout or '')[-1500:],
                      'gate_stderr': (gate_proc.stderr or '')[-1500:],
                      'artifact_retry_dirs': retried,
                      'stray_artifacts': hint,
                      'verdict': 'pass' if gate_ok else 'fail'})
    print(json.dumps({'harness': harness, 'backend': args.backend, 'agent_rc': proc.returncode,
                      'gate_rc': gate_proc.returncode, 'verdict': payload['verdict']}, ensure_ascii=False))
    if args.json_out:
        p = Path(args.json_out)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
    return 0 if gate_ok else 1


if __name__ == '__main__':
    raise SystemExit(main())
