# Vision pipeline

```
frame ──► density-map model ──► people per pixel ──┐
   │                                               ├─► per-zone: count, density (p/m²), ±σ
   └───► dense optical flow ──► homography ──► ground velocity ──► mean speed, velocity variance
```

## 1. Calibrate each camera (once)

Pick 4 or more points on the ground that you can measure, such as tile corners, a rope square or road
markings. Write down their pixel positions and their real positions in metres:

```json
{
  "camera_id": "cam-hall-1",
  "image_points":    [[412, 690], [1510, 702], [1288, 380], [640, 372]],
  "ground_points_m": [[0, 0],     [12, 0],     [12, 15],    [0, 15]],
  "zones": [{"zone_id": "hall", "image_polygon": [[0, 300], [1920, 300], [1920, 1080], [0, 1080]]}]
}
```

From this, CrowdSafe computes a homography. It turns pixel counts into **people per square metre** and
pixel motion into **metres per second**. Without calibration, density numbers are meaningless.

## 2. Choose a density model

| Option | When | Accuracy in dense crowds |
|---|---|---|
| `--model models/dmcount_qnrf.onnx` (density map) | **Production** | See the benchmark below |
| no model (HOG fallback) | Testing, sparse scenes only | Poor above ~1 p/m², because heads are hidden |

The ONNX contract is input `(1,3,H,W)` RGB with ImageNet normalisation, and output a `(1,1,h,w)` density
map whose sum is the head count. Any model that follows it works with `OnnxDensityModel`.

### Default model: DM-Count

Install it with one command. It downloads the ONNX file from this project's
[`models-v1` release](https://github.com/abhieq31/crowdsafe/releases/tag/models-v1) and checks its SHA-256.
No PyTorch is needed:

```bash
crowdsafe model fetch            # "dense": DM-Count trained on UCF-QNRF (default, best on unseen cameras)
crowdsafe model fetch sparse     # DM-Count trained on ShanghaiTech B, for streets below ~250 people per frame
crowdsafe model list             # shows what is downloaded and where
```

`crowdsafe edge` uses the dense model by default (`--model auto`) and downloads it on first use.
`--model sparse`, `--model path/to/your.onnx` and `--model hog` (testing only) are also accepted.

The release files were built with `scripts/export_density_onnx.py` (torch 2.14.1). A rebuild with another
torch version may differ byte for byte but should give the same counts. The script downloads the
[DM-Count](https://github.com/cvlab-stonybrook/DM-Count) checkpoint (Wang et al., NeurIPS 2020, VGG-19),
checks its SHA-256, exports it to ONNX and checks the ONNX output against PyTorch. Use it to rebuild the
files yourself or to export the SHA checkpoint:

```bash
pip install torch onnx onnxruntime          # only needed to export
python scripts/export_density_onnx.py       # -> models/dmcount_qnrf.onnx (+ .json with checksums)
python scripts/export_density_onnx.py --weights SHB
```

| | Licence |
|---|---|
| DM-Count code (architecture) | MIT |
| Checkpoints, mirrored at [tersekmatija/lwcc_weights](https://github.com/tersekmatija/lwcc_weights) v0.1 | MIT |
| Training images (UCF-QNRF, ShanghaiTech) | Research use. Commercial deployers should take their own advice, or fine-tune on footage they own |

Pinned checksums (`scripts/export_density_onnx.py`):

| Checkpoint | SHA-256 |
|---|---|
| `DM-Count_QNRF.pth` | `c354cb0d49bdf28f580a74c0ae962721b2eec939f84485fd3f1684f0e36697ee` |
| `DM-Count_SHA.pth` | `9cd1536a52824ba0102a85659aae46600140c57b6df9cf0356def8514043f556` |
| `DM-Count_SHB.pth` | `12bf4b553e4d795a936f35e0dcbbdfafa34b5d998f80390f7a0ec87a0cf9b6f2` |

The checkpoints are loaded with `torch.load(weights_only=True)`, so a tampered file cannot run code.

### Benchmark

`scripts/benchmark_density.py` runs every test image through `OnnxDensityModel`, the same code the edge
pipeline uses, and reports MAE, RMSE, bias (negative = undercount) and error by true head count.

Run on 2026-10-20 with onnxruntime 1.31 on CPU (export with torch 2.14.1), at native image resolution. Data: ShanghaiTech
[Part A](https://doi.org/10.1109/CVPR.2016.70) test (182 images, 78,862 heads) and Part B test (316 images,
39,121 heads). We used the
[KTAEHWA/shanghaitech-crowd-counting](https://huggingface.co/datasets/KTAEHWA/shanghaitech-crowd-counting)
mirror, whose image and head totals match the official split.

**Cross-dataset** is the honest number for a new venue: the model never saw that dataset's cameras.

| Checkpoint | Part A MAE / RMSE | Part B MAE / RMSE | Note |
|---|---|---|---|
| **QNRF (default)** | **69.9 / 126.1** (16% rel.) | **14.4 / 27.8** (12% rel.) | cross-dataset on both |
| SHA | 64.7 / 107.0 (15%) | 22.9 / 35.4 (19%) | in-domain on A |
| SHB | 122.4 / 208.3 (28%) | 7.7 / 12.7 (6%) | in-domain on B |

The SHB-on-Part-B result reproduces the published 7.68 / 12.66 exactly, which checks the export and the
wrapper end to end. SHA-on-Part-A is slightly worse than the published 61.4 / 98.6 because we round
image sizes to a multiple of 16.

**Error by true count, default QNRF checkpoint, Part A (dense):**

| True count | Images | Mean true | MAE | Bias | Rel. MAE |
|---|---|---|---|---|---|
| 50-100 | 7 | 82 | 7.4 | -3.5 | 9% |
| 100-250 | 62 | 185 | 24.6 | +7.4 | 13% |
| 250-500 | 67 | 361 | 52.1 | +10.8 | 14% |
| 500-1000 | 28 | 689 | 85.6 | -15.6 | 12% |
| 1000-2000 | 17 | 1238 | 292.2 | -112.9 | 24% |
| 2000+ | 1 | 2256 | 295.8 | -295.8 | 13% |

**Part B (street scenes):**

| True count | Images | Mean true | MAE | Bias | Rel. MAE |
|---|---|---|---|---|---|
| 0-50 | 60 | 36 | 2.3 | -0.7 | 6% |
| 50-100 | 109 | 71 | 4.5 | -1.8 | 6% |
| 100-250 | 117 | 162 | 16.3 | -11.0 | 10% |
| 250-500 | 27 | 326 | 59.2 | -59.2 | 18% |
| 500-1000 | 3 | 519 | 133.7 | -133.7 | 26% |

At the edge pipeline's default `input_width=1024` the numbers barely move: Part B is identical (its
images are already 1024 px wide), and Part A, where most images are smaller and get upscaled, is
71.7 / 129.6.

What this means for safety:

- **The model undercounts the densest scenes.** On Part A images with 1000-2000 heads, a single image's
  count is off by 24% on average, and the counts run about 9% low overall. On Part B images with 250+ heads
  they run 19% low. Undercounting is the dangerous direction, so set alert thresholds with that margin,
  and fuse cameras with gate counts.
- Public datasets have no camera calibration, so these buckets are heads per image, not p/m². They
  show the trend, not the error at a given density.
- Speed: 3-5 s per 1024x768 frame on an Apple M1 CPU. With CoreML, which the edge pipeline picks
  automatically when available, it was 0.47 s, and Part B counts differed from CPU by at most 1.4 heads
  per image (MAE 14.55 vs 14.35).

**Speed against accuracy.** A smaller input is faster but undercounts dense crowds badly. Default
QNRF checkpoint, same data, CPU:

| Input width | CPU s/frame (M1) | Part A MAE | Part A bias, 1000+ heads (18 images) | Part B MAE | Part B bias, 250+ heads (30 images) |
|---|---|---|---|---|---|
| **1024 (default)** | 3-5 | 71.7 | -8% | 14.4 | -19% |
| 768 | 2.0 | 82.8 | -16% | 19.0 | -27% |
| 512 | 0.7 | 105.4 | -34% | 30.4 | -39% |

So `crowdsafe edge` **never lowers the input width on its own**. If the device is too slow for the
requested interval, it reports less often instead (for example every 6.7 s on a 4 s/frame CPU) and says
so. A crush builds up over minutes (the demo's RED alert comes 13 minutes before critical density), so a
slower report is far safer than doubling or quadrupling the undercount in the densest scenes. `--input-width` is a manual override for people who
have measured the trade-off on their own cameras.

Reproduce:

```bash
python scripts/benchmark_density.py --model models/dmcount_qnrf.onnx \
    --format shanghaitech --data ShanghaiTech/part_B/test_data --json shb.json
CROWDSAFE_SHTECH_B=ShanghaiTech/part_B/test_data pytest tests/test_density_model.py
```

`--format jhu` reads JHU-Crowd++ splits, and `--format csv` reads an `image,count` CSV, for mirrors or
your own labelled frames.

**Accuracy is a safety property.** Before relying on a model at a venue, label 50-100 frames from that
venue's own cameras, measure the error with `--format csv`, fine-tune if needed, and publish what you find.

## 3. Motion → crowd pressure

Farnebäck optical flow is projected onto the ground plane. For each zone the pipeline reports:

- `mean_speed`: median ground speed. A collapsing speed at high density means a jam.
- `velocity_variance`: Var(vx) + Var(vy). Multiplied by density on the server, this gives Helbing's
  *crowd pressure*. Above ~0.02/s² it indicates crowd turbulence.

The server also looks for **stop-and-go waves** in the speed time series (detrended autocorrelation).

## 4. Privacy

Frames are processed in memory on the device. Only the JSON numbers are sent. There is no face
recognition and no tracking.
