"""Read-only UI adapter, run in the pinned research environment."""
import argparse
import json
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--deployment", required=True)
    parser.add_argument("--section", choices=["overview", "predictions"], default="overview")
    parser.add_argument("--scope", choices=["paper", "research"], default="paper")
    parser.add_argument("--date")
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument("--limit", type=int, default=50)
    parser.add_argument("--cpu-affinity", default="")
    args = parser.parse_args()
    try:
        if args.cpu_affinity:
            cpus = {int(v) for v in args.cpu_affinity.split(",")}
            if not cpus or min(cpus) < 0 or max(cpus) >= (os.cpu_count() or 1):
                raise ValueError("invalid process-local CPU affinity")
            if os.name == "nt":
                import ctypes
                from ctypes import wintypes
                kernel = ctypes.WinDLL("kernel32", use_last_error=True)
                kernel.GetCurrentProcess.restype = wintypes.HANDLE
                kernel.SetProcessAffinityMask.argtypes = [wintypes.HANDLE, ctypes.c_size_t]
                if not kernel.SetProcessAffinityMask(kernel.GetCurrentProcess(), sum(1 << n for n in cpus)):
                    raise ctypes.WinError(ctypes.get_last_error())
            else:
                os.sched_setaffinity(0, cpus)
        from aipha.research.operations_view import OperationsView
        view = OperationsView(args.deployment)
        data = view.overview() if args.section == "overview" else view.predictions(args.scope, args.date, args.offset, args.limit)
        print(json.dumps({"status": "ok", "data": data}, ensure_ascii=True, allow_nan=False))
        return 0
    except Exception as exc:
        print(json.dumps({"status": "error", "error_type": type(exc).__name__,
                          "message": "研究运营证据不可用，请检查本机部署和固定 Python 环境"}, ensure_ascii=True))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
