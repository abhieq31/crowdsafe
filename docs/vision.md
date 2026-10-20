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
| `--model your_model.onnx` (density map) | **Production** | Good. This family is built for 3-10 p/m² |
| no model (HOG fallback) | Testing, sparse scenes only | Poor above ~1 p/m², because heads are hidden |

The ONNX contract is input `(1,3,H,W)` RGB with ImageNet normalisation, and output a `(1,1,h,w)` density
map whose sum is the head count. Models from the CSRNet, DM-Count and P2PNet family, trained on
ShanghaiTech, UCF-QNRF, JHU-Crowd++ or NWPU-Crowd, can be exported with `torch.onnx.export`.
Check each model's licence: many academic checkpoints are for non-commercial use only.

**Accuracy is a safety property.** Before relying on a model at a venue, label 50-100 frames from that
venue's own cameras and measure the counting error (MAE) at that venue. Fine-tune if needed. Publish what you find.

## 3. Motion → crowd pressure

Farnebäck optical flow is projected onto the ground plane. For each zone the pipeline reports:

- `mean_speed`: median ground speed. A collapsing speed at high density means a jam.
- `velocity_variance`: Var(vx) + Var(vy). Multiplied by density on the server, this gives Helbing's
  *crowd pressure*. Above ~0.02/s² it indicates crowd turbulence.

The server also looks for **stop-and-go waves** in the speed time series (detrended autocorrelation).

## 4. Privacy

Frames are processed in memory on the device. Only the JSON numbers are sent. There is no face
recognition and no tracking.
