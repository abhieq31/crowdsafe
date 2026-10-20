#!/usr/bin/env python3
"""Download a pretrained DM-Count crowd-density model and export it to ONNX.

The exported file follows the ``OnnxDensityModel`` contract in docs/vision.md:
input ``(1, 3, H, W)`` float32 RGB with ImageNet normalisation, output
``(1, 1, H/8, W/8)`` density map whose sum is the head count. H and W are
dynamic.

Model and licences
------------------
* Architecture: DM-Count (Wang et al., "Distribution Matching for Crowd
  Counting", NeurIPS 2020), VGG-19 backbone. Code: MIT licence,
  https://github.com/cvlab-stonybrook/DM-Count
* Checkpoints: re-hosted by the LWCC project, MIT licence,
  https://github.com/tersekmatija/lwcc_weights (release v0.1)
* The checkpoints were trained on ShanghaiTech / UCF-QNRF, whose images are
  distributed for research. If you deploy commercially, get your own legal
  advice on the training data, or fine-tune on footage you own.

Weights are never committed to this repository. This script downloads them,
checks the SHA-256 below, and refuses to continue on a mismatch.

Usage (needs ``pip install torch onnx onnxruntime``; torch is NOT a CrowdSafe
dependency, it is only needed once to export)::

    python scripts/export_density_onnx.py --weights QNRF --out models/dmcount_qnrf.onnx
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import urllib.request
from pathlib import Path

RELEASE = "https://github.com/tersekmatija/lwcc_weights/releases/download/v0.1/"
# SHA-256 of the upstream .pth files, pinned so a changed upstream file is caught.
WEIGHTS = {
    "SHA": ("DM-Count_SHA.pth", "9cd1536a52824ba0102a85659aae46600140c57b6df9cf0356def8514043f556"),
    "SHB": ("DM-Count_SHB.pth", "12bf4b553e4d795a936f35e0dcbbdfafa34b5d998f80390f7a0ec87a0cf9b6f2"),
    "QNRF": ("DM-Count_QNRF.pth", "c354cb0d49bdf28f580a74c0ae962721b2eec939f84485fd3f1684f0e36697ee"),
}
VGG19 = [64, 64, "M", 128, 128, "M", 256, 256, 256, 256, "M", 512, 512, 512, 512, "M", 512, 512, 512, 512]


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def download(name: str, expected: str, cache: Path) -> Path:
    cache.mkdir(parents=True, exist_ok=True)
    dest = cache / name
    if not dest.exists():
        print(f"downloading {RELEASE + name}")
        tmp = dest.with_suffix(".part")
        urllib.request.urlretrieve(RELEASE + name, tmp)
        tmp.rename(dest)
    got = sha256(dest)
    if got != expected:
        raise SystemExit(f"checksum mismatch for {dest}\n  expected {expected}\n  got      {got}\n"
                         "Delete the file and retry; if it persists the upstream file has changed.")
    print(f"sha256 ok  {got}")
    return dest


def build_model():
    import torch
    from torch import nn

    class DMCount(nn.Module):
        def __init__(self):
            super().__init__()
            layers, c = [], 3
            for v in VGG19:
                if v == "M":
                    layers.append(nn.MaxPool2d(2, 2))
                else:
                    layers += [nn.Conv2d(c, v, 3, padding=1), nn.ReLU(inplace=True)]
                    c = v
            self.features = nn.Sequential(*layers)
            self.reg_layer = nn.Sequential(nn.Conv2d(512, 256, 3, padding=1), nn.ReLU(inplace=True),
                                           nn.Conv2d(256, 128, 3, padding=1), nn.ReLU(inplace=True))
            self.density_layer = nn.Sequential(nn.Conv2d(128, 1, 1), nn.ReLU())

        def forward(self, x):
            x = self.features(x)
            x = nn.functional.interpolate(x, scale_factor=2.0, mode="bilinear", align_corners=True)
            return self.density_layer(self.reg_layer(x))

    return DMCount(), torch


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--weights", choices=sorted(WEIGHTS), default="QNRF",
                    help="training set of the checkpoint: QNRF (dense, default), SHA (dense), SHB (sparse streets)")
    ap.add_argument("--out", type=Path, default=None, help="output .onnx (default models/dmcount_<weights>.onnx)")
    ap.add_argument("--cache", type=Path, default=Path.home() / ".cache" / "crowdsafe",
                    help="where downloaded checkpoints are kept")
    ap.add_argument("--opset", type=int, default=17)
    args = ap.parse_args(argv)

    name, expected = WEIGHTS[args.weights]
    out = args.out or Path("models") / f"dmcount_{args.weights.lower()}.onnx"
    ckpt = download(name, expected, args.cache)

    model, torch = build_model()
    # weights_only=True refuses arbitrary pickled code inside the checkpoint.
    state = torch.load(ckpt, map_location="cpu", weights_only=True)
    state = state.get("model", state) if isinstance(state, dict) else state
    model.load_state_dict(state)
    model.eval()

    out.parent.mkdir(parents=True, exist_ok=True)
    dummy = torch.randn(1, 3, 384, 512)
    torch.onnx.export(model, (dummy,), str(out), input_names=["image"], output_names=["density"],
                      dynamic_axes={"image": {2: "height", 3: "width"}, "density": {2: "h", 3: "w"}},
                      opset_version=args.opset, dynamo=False)

    # Check the ONNX graph against PyTorch at a different, non-square size.
    import numpy as np
    import onnxruntime as ort

    x = torch.rand(1, 3, 576, 768)
    with torch.no_grad():
        ref = model(x).numpy()
    got = ort.InferenceSession(str(out), providers=["CPUExecutionProvider"]).run(None, {"image": x.numpy()})[0]
    if got.shape != ref.shape or not np.allclose(got, ref, rtol=1e-3, atol=1e-5):
        raise SystemExit(f"ONNX output differs from PyTorch: {got.shape} vs {ref.shape}, "
                         f"max abs diff {float(np.abs(got - ref).max()):.3g}")

    digest = sha256(out)
    meta = {
        "model": f"DM-Count VGG-19, trained on {args.weights}",
        "source_weights": RELEASE + name,
        "source_weights_sha256": expected,
        "onnx_sha256": digest,
        "licence": "MIT (DM-Count code, LWCC weight mirror); training data is research-licensed",
        "contract": "input (1,3,H,W) RGB ImageNet-normalised; output (1,1,H/8,W/8) density, sum = head count",
    }
    out.with_suffix(".json").write_text(json.dumps(meta, indent=2) + "\n")
    print(f"wrote {out}  sha256 {digest}")
    print(f"run:  crowdsafe edge --model {out} --source ... --calibration ...   (see docs/vision.md)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
