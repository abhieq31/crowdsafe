#!/usr/bin/env python3
"""Measure the counting error of an ONNX density model on a public test split.

Runs every image through ``roomtobreathe.vision.density.OnnxDensityModel`` (the
same code path the edge pipeline uses), then reports MAE, RMSE and error
bucketed by the true head count.

Datasets are not bundled; download them yourself and accept their terms.
Supported layouts (``--format``):

``shanghaitech``  the official ShanghaiTech folder for one split, e.g.
                  ``part_B/test_data`` with ``images/IMG_1.jpg`` and
                  ``ground-truth/GT_IMG_1.mat`` (needs scipy)
``jhu``           a JHU-Crowd++ split folder, e.g. ``val`` with
                  ``images/0001.jpg`` and ``gt/0001.txt`` (one head per line)
``csv``           a CSV with ``image,count`` columns; image paths are relative
                  to the CSV. Use this for mirrors or your own labelled frames.

Example::

    python scripts/benchmark_density.py --model models/dmcount_qnrf.onnx \\
        --format shanghaitech --data ShanghaiTech/part_B/test_data --json results.json

Public datasets have no camera calibration, so the buckets are by head count
per image, not people per square metre. For a venue, label frames from its
own cameras and use ``--format csv``.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import sys
import time
from datetime import date
from pathlib import Path
from typing import List, Tuple

BUCKETS = [(0, 50), (50, 100), (100, 250), (250, 500), (500, 1000), (1000, 2000), (2000, math.inf)]


def load_items(fmt: str, root: Path) -> List[Tuple[Path, float]]:
    if fmt == "csv":
        with open(root, newline="") as f:
            return [(root.parent / r["image"], float(r["count"])) for r in csv.DictReader(f)]
    images = sorted((root / "images").glob("*.jpg"))
    items = []
    for img in images:
        if fmt == "shanghaitech":
            from scipy.io import loadmat

            mat = loadmat(root / "ground-truth" / f"GT_{img.stem}.mat")
            count = len(mat["image_info"][0, 0][0, 0][0])
        elif fmt == "jhu":
            gt = root / "gt" / f"{img.stem}.txt"
            count = sum(1 for line in gt.read_text().splitlines() if line.strip())
        else:
            raise ValueError(fmt)
        items.append((img, float(count)))
    return items


def summarise(rows):
    if not rows:
        return {"n": 0}
    err = [p - t for _, t, p in rows]
    gt = [t for _, t, _ in rows]
    mae = sum(abs(e) for e in err) / len(err)
    return {
        "n": len(rows),
        "mean_true": sum(gt) / len(gt),
        "mae": mae,
        "rmse": math.sqrt(sum(e * e for e in err) / len(err)),
        "bias": sum(err) / len(err),  # >0 overcounts, <0 undercounts
        "rel_mae": mae / max(1e-9, sum(gt) / len(gt)),
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--model", required=True, help="ONNX density model (see scripts/export_density_onnx.py)")
    ap.add_argument("--format", choices=["shanghaitech", "jhu", "csv"], required=True)
    ap.add_argument("--data", type=Path, required=True, help="split folder, or the CSV file for --format csv")
    ap.add_argument("--input-width", type=int, default=0,
                    help="resize frames to this width like the edge pipeline (default 0 = native resolution)")
    ap.add_argument("--all-providers", action="store_true",
                    help="use GPU/CoreML if available (default: CPU only, for reproducible numbers)")
    ap.add_argument("--name", default=None, help="dataset label for the report")
    ap.add_argument("--limit", type=int, default=0, help="only use the first N images (smoke test)")
    ap.add_argument("--json", type=Path, default=None, help="also write per-image results here")
    args = ap.parse_args(argv)

    import cv2

    from roomtobreathe.vision.density import OnnxDensityModel

    items = load_items(args.format, args.data)
    if args.limit:
        items = items[: args.limit]
    if not items:
        raise SystemExit(f"no images found under {args.data}")
    providers = None if args.all_providers else ["CPUExecutionProvider"]
    model = OnnxDensityModel(args.model, input_width=args.input_width or None, providers=providers)

    rows, t0 = [], time.perf_counter()
    for i, (img, true) in enumerate(items, 1):
        frame = cv2.imread(str(img))
        if frame is None:
            raise SystemExit(f"cannot read {img}")
        rows.append((img.name, true, float(model.density_map(frame).sum())))
        if i % 50 == 0:
            print(f"  {i}/{len(items)}", file=sys.stderr)
    secs = (time.perf_counter() - t0) / len(items)

    overall = summarise(rows)
    buckets = []
    for lo, hi in BUCKETS:
        s = summarise([r for r in rows if lo <= r[1] < hi])
        if s["n"]:
            buckets.append({"true_count": f"{lo}+" if hi == math.inf else f"{lo}-{hi}", **s})

    name = args.name or str(args.data)
    width = args.input_width or "native"
    print(f"\nModel: {args.model}\nData: {name} ({overall['n']} images)  input width: {width}  "
          f"date: {date.today().isoformat()}  {secs * 1000:.0f} ms/image\n")
    print(f"MAE {overall['mae']:.2f}   RMSE {overall['rmse']:.2f}   bias {overall['bias']:+.2f}   "
          f"relative MAE {overall['rel_mae']:.1%}\n")
    print("| True count | Images | Mean true | MAE | RMSE | Bias | Rel. MAE |")
    print("|---|---|---|---|---|---|---|")
    for b in buckets:
        print(f"| {b['true_count']} | {b['n']} | {b['mean_true']:.0f} | {b['mae']:.1f} | {b['rmse']:.1f} | "
              f"{b['bias']:+.1f} | {b['rel_mae']:.0%} |")

    if args.json:
        args.json.write_text(json.dumps({
            "model": str(args.model), "dataset": name, "date": date.today().isoformat(),
            "input_width": width, "ms_per_image": secs * 1000, "overall": overall, "buckets": buckets,
            "images": [{"image": n, "true": t, "pred": p} for n, t, p in rows],
        }, indent=2) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
