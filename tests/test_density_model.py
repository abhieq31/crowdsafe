"""Optional checks of a real exported density model.

Skipped unless an ONNX model exists. Export one with
``python scripts/export_density_onnx.py`` (writes models/dmcount_qnrf.onnx), or
point ``CROWDSAFE_DENSITY_ONNX`` at another file. The sample-image test also
needs the ShanghaiTech Part B test split: set ``CROWDSAFE_SHTECH_B`` to the
``part_B/test_data`` folder (the one containing ``images/``).
"""

import json
import os
from pathlib import Path

import pytest

np = pytest.importorskip("numpy")
cv2 = pytest.importorskip("cv2")
pytest.importorskip("onnxruntime")

from crowdsafe.vision.density import OnnxDensityModel  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
MODEL = Path(os.environ.get("CROWDSAFE_DENSITY_ONNX", ROOT / "models" / "dmcount_qnrf.onnx"))
SHB = os.environ.get("CROWDSAFE_SHTECH_B")
SAMPLES = json.loads((ROOT / "tests" / "vectors" / "density_samples.json").read_text())

pytestmark = pytest.mark.skipif(not MODEL.exists(), reason=f"no density model at {MODEL}")


@pytest.fixture(scope="module")
def model():
    return OnnxDensityModel(str(MODEL), providers=["CPUExecutionProvider"])


def test_output_aligned_with_frame_and_non_negative(model):
    frame = np.random.default_rng(0).integers(0, 255, (480, 640, 3), dtype=np.uint8)
    dm = model.density_map(frame)
    assert dm.shape == (480, 640)
    assert dm.min() >= 0


def test_empty_scene_counts_near_zero(model):
    frame = np.full((720, 1280, 3), 128, np.uint8)
    assert model.density_map(frame).sum() < 2.0


def test_count_survives_resize_to_frame(model):
    # The wrapper must not change the count when it resizes the map back to the frame.
    frame = np.random.default_rng(1).integers(0, 255, (600, 900, 3), dtype=np.uint8)
    model.input_width = 512
    try:
        dm = model.density_map(frame)
        x = cv2.cvtColor(cv2.resize(frame, (512, 336)), cv2.COLOR_BGR2RGB).astype(np.float32) / 255.0
        x = ((x - [0.485, 0.456, 0.406]) / [0.229, 0.224, 0.225]).astype(np.float32).transpose(2, 0, 1)[None]
        raw = model.sess.run(None, {model.input_name: x})[0].sum()
    finally:
        model.input_width = 1024
    assert dm.sum() == pytest.approx(raw, rel=1e-4, abs=1e-4)


@pytest.mark.skipif(not SHB, reason="set CROWDSAFE_SHTECH_B to run on ShanghaiTech Part B images")
@pytest.mark.parametrize("sample", SAMPLES["images"], ids=lambda s: s["image"])
def test_count_within_tolerance_on_shanghaitech_b(model, sample):
    frame = cv2.imread(str(Path(SHB) / "images" / sample["image"]))
    assert frame is not None, sample["image"]
    model.input_width = None  # native resolution, as in the published benchmark
    try:
        pred = float(model.density_map(frame).sum())
    finally:
        model.input_width = 1024
    true = sample["count"]
    tol = max(SAMPLES["abs_tolerance"], SAMPLES["rel_tolerance"] * true)
    assert abs(pred - true) <= tol, f"{sample['image']}: predicted {pred:.1f}, true {true}"
