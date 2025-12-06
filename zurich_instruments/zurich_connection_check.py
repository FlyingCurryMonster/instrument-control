"""Lightweight connectivity probe for Zurich Instruments DAQ server.

Reads-only: connects to the data server, optionally attaches to a device,
and can fetch a demod sample to verify access.
"""
from __future__ import annotations

import argparse
import json
import sys
from typing import Any, Dict

import zhinst.core


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="192.168.77.26", help="DAQ server host/IP")
    parser.add_argument("--port", type=int, default=8004, help="DAQ server port")
    parser.add_argument(
        "--api-level", type=int, default=6, help="zhinst API level (default 6)"
    )
    parser.add_argument(
        "--interface", default="PCIe", help="Interface for connectDevice (e.g., PCIe, USB, 1GbE)"
    )
    parser.add_argument(
        "--device",
        default="dev4934",
        help="Device ID to connect (omit or set to '' to skip connectDevice)",
    )
    parser.add_argument(
        "--demod", type=int, default=1, help="Demodulator number to sample (1-based)"
    )
    parser.add_argument(
        "--list-nodes",
        action="store_true",
        help="List device nodes (JSON) after connectDevice (can be large)",
    )
    return parser.parse_args()


def safe_json_listnodes(daq: zhinst.core.ziDAQServer, path: str) -> Dict[str, Any]:
    try:
        return json.loads(daq.listNodesJSON(path, flags=0))
    except Exception:
        return {}


def main() -> int:
    args = parse_args()
    print(f"Connecting to DAQ server {args.host}:{args.port} (API {args.api_level})")
    try:
        daq = zhinst.core.ziDAQServer(args.host, args.port, api_level=args.api_level)
        print("Connected to server.")
    except Exception as exc:  # pragma: no cover
        print(f"Failed to connect to server: {exc}")
        return 1

    if args.device:
        print(f"Connecting to device {args.device} via {args.interface}")
        try:
            daq.connectDevice(args.device, interface=args.interface)
            clockbase = None
            try:
                clockbase = daq.getInt(f"/{args.device}/clockbase")
            except Exception:
                pass
            print(f"Connected to {args.device}. clockbase={clockbase}")
        except Exception as exc:  # pragma: no cover
            print(f"connectDevice failed: {exc}")
            return 2

        if args.list_nodes:
            nodes = safe_json_listnodes(daq, f"/{args.device}")
            print(f"Nodes under /{args.device}:")
            print(json.dumps(nodes, indent=2))

        # Try a read-only demod sample
        if args.demod:
            demod_index = args.demod - 1
            sample_path = f"/{args.device}/demods/{demod_index}/sample"
            print(f"Reading one demod sample from {sample_path}")
            try:
                sample = daq.getSample(sample_path)
                keys = list(sample.keys())
                summary = {k: len(v) if hasattr(v, "__len__") else type(v).__name__ for k, v in sample.items()}
                print(f"Sample keys: {keys}")
                print(f"Sample summary: {summary}")
            except Exception as exc:  # pragma: no cover
                print(f"getSample failed: {exc}")
                return 3

    print("Finished connectivity probe.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
