"""命令行：查看工作区版本或输出复核报告。"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from . import __version__
from .engine import analyse


def _print_json(value) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2, default=str))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="appliance-price-bridge", description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=None, help="资料目录，默认 data/")
    parser.add_argument("--version", action="store_true", help="只显示版本")
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("report", help="输出整份复核报告")
    sub.add_parser("editions", help="输出各刊期的冻结评估与替代关系")
    sub.add_parser("reissue", help="输出待重发范围")
    p_ref = sub.add_parser("reference", help="按合同约定复核下一结算期基准")
    p_ref.add_argument("contract")
    p_ref.add_argument("--as-of", required=True)
    p_diff = sub.add_parser("difference", help="同合同同调整期的累计差额")
    p_diff.add_argument("contract")
    p_diff.add_argument("period")
    p_window = sub.add_parser("window", help="开放付款窗口")
    p_window.add_argument("window")
    p_notice = sub.add_parser("notice", help="从一笔通知追溯价格与服务依据")
    p_notice.add_argument("notice")
    p_series = sub.add_parser("series", help="某系列某评估周的可比水平")
    p_series.add_argument("series")
    p_series.add_argument("week")
    p_series.add_argument("--as-of", required=True)

    args = parser.parse_args(argv)
    if args.version or not args.command:
        print(f"本地分析工作区 {__version__}")
        return 0

    analysis = analyse(args.data_dir)
    if args.command == "report":
        _print_json(analysis.report())
    elif args.command == "editions":
        _print_json(analysis.editions())
    elif args.command == "reissue":
        _print_json(analysis.reissue_plan())
    elif args.command == "reference":
        _print_json(analysis.contract_reference(args.contract, args.as_of))
    elif args.command == "difference":
        _print_json(analysis.difference(args.contract, args.period))
    elif args.command == "window":
        _print_json(analysis.window(args.window))
    elif args.command == "notice":
        _print_json(analysis.notice(args.notice))
    elif args.command == "series":
        _print_json(analysis.series(args.series, args.week, args.as_of).describe())
    else:  # pragma: no cover - argparse 已保证
        parser.error(f"未知命令 {args.command}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
