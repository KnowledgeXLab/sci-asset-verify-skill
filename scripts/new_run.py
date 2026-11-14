#!/usr/bin/env python3
"""创建/解析规范化的核验批次目录 `asset-runs/run-[YYYYMMDD]-[hhmmss]/`。

批次目录名是唯一的规范形式：`run-` + 8 位日期 + `-` + 6 位时间（本地时区）。
同一批核验的所有产物都落在这个目录里，便于按时间顺序追溯与对齐。

用法：
  python scripts/new_run.py --package <资产根目录>              # 创建并打印路径
  python scripts/new_run.py --package <资产根目录> --print      # 只打印，不创建
  python scripts/new_run.py --package <资产根目录> --at 2026-11-14T10:55:45   # 指定时间（补录/复现）

退出码：0 成功；2 参数问题。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path

try:
    from _version import stamp as _stamp
except Exception:  # noqa: BLE001
    def _stamp(payload):
        return payload

RUNS_DIR = 'asset-runs'
RUN_ID = re.compile(r'^run-(\d{8})-(\d{6})$')


def is_canonical(run_id: str) -> bool:
    return bool(RUN_ID.match(str(run_id)))


def new_run_id(at: datetime | None = None) -> str:
    t = at or datetime.now()
    return f'run-{t:%Y%m%d}-{t:%H%M%S}'


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description='创建规范化的 run-[YYYYMMDD]-[hhmmss] 批次目录')
    ap.add_argument('--package', required=True, help='资产根目录')
    ap.add_argument('--at', help='指定时间（ISO 格式），用于补录或复现')
    ap.add_argument('--print', dest='print_only', action='store_true', help='只打印路径，不创建目录')
    ap.add_argument('--json-out')
    args = ap.parse_args(argv)

    root = Path(args.package).expanduser().resolve()
    if not root.is_dir():
        print(f'资产根目录不存在：{root}', file=sys.stderr)
        return 2
    at = None
    if args.at:
        try:
            at = datetime.fromisoformat(args.at)
        except Exception as exc:  # noqa: BLE001
            print(f'--at 解析失败：{exc}', file=sys.stderr)
            return 2

    runs = root / RUNS_DIR
    run_id = new_run_id(at)
    target = runs / run_id
    # 同一秒内重复调用时顺延一秒，避免覆盖历史批次
    n = 0
    while target.exists() and not args.print_only:
        n += 1
        base = at or datetime.now()
        run_id = new_run_id(base.replace(microsecond=0) if at else None)
        from datetime import timedelta
        run_id = new_run_id(base + timedelta(seconds=n))
        target = runs / run_id
    if not args.print_only:
        target.mkdir(parents=True, exist_ok=True)

    payload = _stamp({'tool': 'new_run.py', 'package': str(root), 'run_id': run_id,
                      'run_dir': str(target), 'created': not args.print_only,
                      'pattern': 'run-[YYYYMMDD]-[hhmmss]'})
    print(str(target))
    if args.json_out:
        p = Path(args.json_out)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
