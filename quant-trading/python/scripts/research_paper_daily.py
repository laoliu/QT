"""Run one post-close session in a pinned, dedicated Paper deployment."""
from __future__ import annotations
import argparse
from datetime import date, datetime, timezone
import json
import logging
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--deployment", type=Path, required=True)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--preflight-date", type=date.fromisoformat)
    parser.add_argument("--cpu-affinity", help="optional process-local logical CPU indices, comma separated")
    args = parser.parse_args()
    if args.cpu_affinity:
        cpus = {int(value) for value in args.cpu_affinity.split(",")}
        if not cpus or min(cpus) < 0 or max(cpus) >= (os.cpu_count() or 1):
            raise ValueError("invalid logical CPU affinity")
        if os.name == "nt":
            import ctypes
            from ctypes import wintypes
            kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel.GetCurrentProcess.restype = wintypes.HANDLE
            kernel.SetProcessAffinityMask.argtypes = [wintypes.HANDLE, ctypes.c_size_t]
            if not kernel.SetProcessAffinityMask(kernel.GetCurrentProcess(), sum(1 << cpu for cpu in cpus)):
                raise ctypes.WinError(ctypes.get_last_error())
        else:
            os.sched_setaffinity(0, cpus)
    from aipha.research.daily_operation import load_deployment, operate_day, _json
    deployment = load_deployment(args.deployment)
    from dotenv import load_dotenv
    load_dotenv(deployment["env_file"], override=False)
    from aipha.research.daily_network import configure_proxy_fallback
    configure_proxy_fallback(on_fallback=lambda endpoint, error_type: print(json.dumps({
        "event": "tushare_proxy_fallback", "endpoint": endpoint,
        "error_type": error_type, "transport": "direct",
    }), file=sys.stderr, flush=True))
    secret_values = [os.environ.get(key) for key in ("TUSHARE_TOKEN", "AIPHA_PIT_SNAPSHOT_KEY") if os.environ.get(key)]
    def clean(value):
        for secret in secret_values:
            value = value.replace(secret, "[REDACTED]")
        return value
    logging.disable(logging.CRITICAL)
    try:
        result = operate_day(deployment, execute=args.execute, preflight_date=args.preflight_date)
        print(json.dumps({k: v for k, v in result.items() if k != "receipt"}, ensure_ascii=True))
        return 0
    except Exception as exc:
        result = {"status": "failed", "error_type": type(exc).__name__, "error": clean(str(exc)),
                  "observed_at": datetime.now(timezone.utc).isoformat(), "broker_contacted": False}
        _json(deployment["output_root"] / "latest_failure.json", result)
        print(json.dumps(result, ensure_ascii=True), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
