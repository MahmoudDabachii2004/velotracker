# Computer Vision for Pedal/Crank Rotation Tracking & Cycling RPM — Research Audit

**Target system:** VeloTracker (`MahmoudDabachii2004/velotracker`) — iPhone-over-IriunWebcam → OpenCV HSV sticker detection → Taubin circle fit → weighted-OLS RPM → BLE CSC/FTMS broadcast to MyWhoosh.

**Scope of this audit:** All ten topics requested — state-of-the-art review, HSV detection limits, circle-fitting math, RPM derivation, Kalman filter design, ROI strategy, frame-rate / latency, edge cases, OSS references, anti-patterns. Findings are tied back to the VeloTracker source code (which was inspected during this audit) so each section ends with concrete improvement recommendations.

---

## 0. Executive Summary (TL;DR for the maintainer)

| VeloTracker claim (README) | Actual code | Audit verdict |
|---|---|---|
| "Kasa circle fit" | `RPMCalculator._fit_circle` uses **Taubin** | README is wrong; Taubin is the correct choice. README should be updated, and you should consider Hyperfit for extra robustness on short calibration arcs. |
| "Light EMA smoothing" of centroid | One-pole IIR `α=0.8` on raw centroid **plus** a 4-D Kalman filter on `(x,y,vx,vy)` | Two smoothers cascaded — redundant; the Kalman filter already subsumes the EMA. The EMA's `α=0.8` is dangerously high at 30 FPS (effectively no smoothing). |
| "20-frame sliding window weighted OLS" | `RPM_REGRESSION_WINDOW = 20`, `RPM_WEIGHTED_OLS_LAMBDA = 2.0` | OK, but window is in **samples** not seconds — at variable FPS this changes the effective time-base. Should be a time-based window. |
| "Glitch rejection 1 rad/frame" | `MAX_DELTA_ANGLE = 1.0`, scaled by `dt/0.033` | Good — already dt-aware. But the threshold (1 rad ≈ 57°/frame ≈ 1725 RPM at 30 FPS) is so loose it never fires at human cadences; tighten to ~0.3 rad. |
| Kalman Q=100000, R=9 | Yes, fixed scalars | Tuning is suspect — at 30 FPS, R/Q ratio implies >100 px measurement noise. Should be calibrated. |

The five highest-impact changes are at the end of each section, and a prioritized improvement list is in §11.

---

## 1. State of the Art in Pedal/Crank Rotation Tracking via Camera

### 1.1 Academic literature

The directly applicable peer-reviewed literature on **camera-based cadence specifically** is surprisingly thin — most academic cadence work uses IMUs or strain gauges, not vision. The closest published work is:

1. **Sensors 2022, 22(16), 6140 — "Cadence Detection in Road Cycling Using Saddle Tube Motion and Machine Learning"** ([mdpi.com/1424-8220/22/16/6140](https://www.mdpi.com/1424-8220/22/16/6140)). Uses an IMU on the saddle tube and an ML classifier. Useful here only as the *ground truth comparison* cadence signal — it shows what sensor modality the academic community considers practical.
2. **Springer VR 2022 — "Virtual reality application for real-time pedalling cadence estimation"** ([link.springer.com/article/10.1007/s10055-022-00668-w](https://link.springer.com/article/10.1007/s10055-022-00668-w)). Low-cost wireless cadence; uses a magnetic reed sensor — not vision. Confirms the field has converged on hardware sensors, leaving vision-based cadence as a niche/DIY space.
3. **PMC 9503443 — "Using Computer Vision to Track Facial Color Changes and Predict Exercise Intensity"** ([pmc.ncbi.nlm.nih.gov/articles/PMC9503443](https://pmc.ncbi.nlm.nih.gov/articles/PMC9503443)). Vision-for-exercise but for heart rate via skin chroma, not cadence. Useful as a reference for robust color-trace pipelines (rPPD/rPPG literature is the closest academic cousin to "track a small colored blob through time under varying illumination").
4. **PMC 4788418 — "How Fast Is Your Body Motion? Determining a Sufficient Frame Rate for an Optical Motion Tracking System Using Passive Reflective Markers"** ([pmc.ncbi.nlm.nih.gov/articles/PMC4788418](https://pmc.ncbi.nlm.nih.gov/articles/PMC4788418)). **Directly relevant to §7.** Derives a frame-rate criterion beyond Nyquist: *f_min ∝ v_max / d_min* (max velocity divided by min marker spacing). Their conclusion: pure 2× Nyquist is **not** sufficient when multiple markers can swap identity.
5. **CMU Traffic21 — "Vision-based Bicyclist Detection and Tracking for Intelligent Vehicles"** ([cmu.edu/traffic21/pdfs/vision_based-bicyclist-detection-and-tracking.pdf](https://www.cmu.edu/traffic21/pdfs/vision_based-bicyclist-detection-and-tracking.pdf)). Tracks cyclists from a vehicle's POV — not pedal cadence, but useful background on HOG/edge-based bicycle detection.

The closest *enabling* literature is the **circle-fitting statistics** community (Chernov, Kanatani, Al-Sharadqah, Taubin, Pratt) — see §3 for citations. The downstream *RPM extraction* problem is structurally identical to **optical encoder signal processing** and **gait phase estimation** literature (PMC 5363411, "An Extended Kalman Filter to Estimate Human Gait Parameters").

### 1.2 Open-source projects

| Project | URL | Approach | Relevance |
|---|---|---|---|
| **Viscyc** (C. Sachs) | [github.com/csachs/viscyc](https://github.com/csachs/viscyc) | **Otsu threshold + ROI mean-intensity gate.** The camera watches a fixed rectangular ROI; each time a dark feature (a pedal-arm lever) enters it, one revolution is registered. **No centroid tracking, no circle fit, no continuous RPM** — it produces a revolution *count* and derives cadence from inter-event intervals. Runs on a Raspberry Pi Zero. | ⭐⭐⭐⭐ The most mature open-source "vision → BLE cadence" project. VeloTracker is more sophisticated but Viscyc is more robust because its failure mode (missed revolution) is far simpler than VeloTracker's (lost centroid → bad angle → bad RPM regression). Worth studying their tuning for low-end hardware. |
| **PeloMon** (I. Haque) | [github.com/ihaque/pelomon](https://github.com/ihaque/pelomon), [ihaque.org/tag/pelomon.html](https://ihaque.org/tag/pelomon.html) | **Not a vision project.** PeloMon taps the Peloton bike's serial bus, decodes the proprietary cadence/power/speed frames, and re-broadcasts them over BLE CSC/FTMS. The 4-part blog series is a superb reference for *BLE GATT CSC/FTMS service implementation*, which VeloTracker also does. The `BLE_NOTIFY_INTERVAL_SEC = 0.5` (2 Hz) value in VeloTracker's config is indeed "PeloMon-proven" — PeloMon discovered that 2 Hz is the minimum cadence notification rate that Zwift/MyWhoosh will accept without dropouts. | ⭐⭐⭐ For BLE GATT reference only — *not* for vision. The user's prompt conflated PeloMon with a vision approach; this audit corrects that. |
| **blum.bike** (J. Blum) | [github.com/sciguy14/blumbike](https://github.com/sciguy14/blumbike), [jeremyblum.com/2020/09/13/blumbike](https://www.jeremyblum.com/2020/09/13/blumbike) | **Hardware optical sensor (QRD1114 reflective IR)** pointed at a piece of black tape on the trainer's flywheel. A comparator + hysteresis circuit produces one pulse per flywheel revolution. A Particle Photon counts pulses and pushes to the cloud. | ⭐⭐⭐ This is what the user calls the **"trainer-bearing approach"**: a hardware reflective sensor on a marked rotating surface. It's *the* reliable baseline. VeloTracker is essentially a software emulation of this approach using a camera instead of a photointerrupter — but the camera brings color-detection and lighting problems the hardware version never has. Worth noting that blum.bike's design **counts revolutions** rather than fitting a circle, which is a simpler and more failure-resistant pattern. |
| **SmartSpin2k** | [smartspin2k.com](https://www.smartspin2k.com), [github.com/doudar/SmartSpin2k](https://github.com/doudar/SmartSpin2k) | Hardware: stepper motor that physically turns the resistance knob on a Peloton/spin bike. Reads cadence from the bike's existing sensor bus (Peloton fly), not vision. | ⭐⭐ Tangential — resistance-control hardware, not cadence sensing. |
| **zwack** (paixaop) | [github.com/paixaop/zwack](https://github.com/paixaop/zwack) | Pure BLE library — emulates a smart-trainer FTMS peripheral. Viscyc uses it. | ⭐⭐ Useful as a comparison to VeloTracker's `bless`-based server. |
| **pycycling** (zacharyedwardbull) | [github.com/zacharyedwardbull/pycycling](https://github.com/zacharyedwardbull/pycycling) | Python CSC/FTMS *client* library. | ⭐ For testing — use it as a debug sink to read VeloTracker's advertisements. |

**Conclusion:** VeloTracker is currently the **only open-source project that does continuous-angle vision-based cadence estimation** (as opposed to revolution-counting). This is a strength (richer signal, faster RPM response) but also a fragility (more failure modes). Viscyc and blum.bike both demonstrate that the simpler *event-based* paradigm is more robust. A hybrid mode for VeloTracker (degrade to event counting when circle fit quality is poor) is recommended in §11.

### 1.3 Commercial approaches

**No major commercial cadence sensor uses vision.** Wahoo RPM, Garmin Cadence Sensor 2, Polar Cadence, Magene C406 — all use either a **3-axis accelerometer + thresholding** (modern) or a **reed switch + magnet on the crank** (legacy). The reason is economics: an IMU-based sensor costs <$5 BOM, runs 200+ hours on a coin cell, and is immune to lighting. A camera-based approach requires line-of-sight, external lighting compensation, and significantly more compute. Vision is only attractive when:

- You cannot (or will not) attach hardware to the bike (VeloTracker's case).
- The bike already has a camera in the right place (e.g., indoor-bike tablets — no commercial product exploits this).
- You need additional signals (posture, knee tracking) that justify the camera cost.

There is **no public Wahoo or Garmin vision-based cadence product**. The closest commercial relative is **Kinetic Fit** (Kinetic trainers), which uses the trainer's built-in flywheel sensor, not vision. The Peloton bike uses a Hall-effect sensor on the flywheel.

### 1.4 Optical flow vs marker tracking vs color blob — tradeoffs

| Approach | Pros | Cons | Verdict for pedal tracking |
|---|---|---|---|
| **Color blob (HSV/Lab threshold + contour)** — what VeloTracker uses | Simple, fast (sub-ms), no marker geometry needed, gives sub-pixel centroid via image moments | Fails on lighting changes, specular highlights, similar-colored objects in frame, motion blur smears the blob | Best for this use case IF lighting is controlled. The sticker is essentially a manually-placed color marker — color blob is the right primitive. |
| **Optical flow (Lucas-Kanade / Farnebäck)** | No marker needed; tracks natural texture | Pedal arms are often textureless metal; flow vectors are noisy on a small object; aperture problem; assumes brightness constancy (violated by specular highlights) | Worse than color blob for this case. LK is appropriate for *features* not *uniform blobs*. Could supplement color blob to predict between frames. |
| **Fiducial markers (ArUco / AprilTag)** | Sub-pixel pose, rotation directly readable, lighting-robust due to binary pattern | Marker must be ~3-5 cm square and flat; small markers from far away are unreliable; computationally heavier; fails if marker is occluded or angled >~45° | **Underused alternative.** An ArUco marker on the pedal arm would give direct rotation measurement without circle fitting. VeloTracker has `test_aruco_cam.py` already — explore this. Downsides: marker must remain reasonably flat to the camera (not on a rotating pedal), and most pedals don't have a flat 5 cm face. |
| **Deep-learning keypoint (YOLO-pose / MoveNet)** | Robust to lighting, no sticker needed | Needs training data; ~10× slower than color blob; overkill for a single tracked point | Not justified for this constrained problem. |
| **rPPG-style signal extraction** (track average color in ROI, FFT for cadence frequency) | No centroid needed; very robust to occlusion | Output is frequency (RPM) not phase; cannot give instantaneous angular position for BLE revolution events | Interesting alternative for RPM-only output. Would lose the revolution-count feature. |

**Recommendation:** Stay with color blob, but add an **ArUco fallback mode** and a **frequency-domain fallback** (run an FFT on the centroid-x time series in the tracking window — the dominant non-DC frequency is the cadence, robust to dropouts).

---

## 2. HSV Color Detection — Limitations and Improvements

### 2.1 Color-space comparison

| Space | Separates luma? | Hue uniformity | Shadow robustness | OpenCV support | Verdict for pedal sticker |
|---|---|---|---|---|---|
| **HSV** | Yes (V) | Poor near low saturation (red wraps around 0/180) | Medium — V varies but H/S roughly stable | Native, fast | Good default; what VeloTracker uses. Main weakness: Hue is unstable when Saturation is low (white-ish or shadowed sticker). |
| **CIELAB (L*a*b*)** | Yes (L) | Excellent — a*, b* are perceptually uniform | **Best** — L fully separated from chroma | `cv2.COLOR_BGR2LAB`, fast | **Recommended upgrade.** Threshold on a\* and b\* channels only (ignore L). Robust to massive brightness swings. Marginally slower due to per-pixel RGB→XYZ→LAB transform. |
| **YCrCb** | Yes (Y) | Moderate — Cr/Cb are less perceptually uniform than a/b | Good | Native, fast | Common in skin detection; works for stickers too but slightly worse than LAB. |
| **YUV** | Yes (Y) | Similar to YCrCb | Good | Native | Equivalent to YCrCb for this purpose. |
| **RGB** | No | Poor | Poor | Native | Never use directly for lighting-invariant detection. |

**Empirical comparison** (e.g., [ResearchGate: Comparing the Performance of LAB and HSV Color Spaces with Respect to Color Image Segmentation](https://www.researchgate.net/publication/277722637_Comparing_the_Performance_of_LAB_and_HSV_Color_Spaces_with_Respect_to_Color_Image_Segmentation), [arxiv.org/pdf/1506.01472](https://arxiv.org/pdf/1506.01472)) consistently shows LAB outperforms HSV for segmentation under varying illumination, at a small compute cost.

**Recommendation for VeloTracker:** Add a `COLOR_SPACE` config option that switches between HSV (default) and LAB. For LAB, threshold on `a*` and `b*` only (typical range for a green sticker: a* ∈ [−40, −10], b* ∈ [10, 40]). Keep HSV as fallback for backward compatibility with `calibrate.py`.

### 2.2 The specular highlight problem

A reflective sticker under direct illumination produces a bright white hotspot where the BRDF peaks. In HSV this manifests as **S → 0, V → 255** in the highlight region — the Hue becomes undefined. Symptoms:

- The blob fragments into a colored part + a white part with a gap.
- `cv2.moments` centroid shifts toward the highlight.
- Inter-frame centroid jitter increases 2-5×.

Mitigations (in order of effectiveness):

1. **Matte sticker material.** Replace glossy stickers with matte paper tape or painted wooden discs. This is the single most effective fix and costs nothing. **Strongly recommended in README.**
2. **Use LAB and ignore L.** Specular highlights are mostly in L (brightness), not a/b. Thresholding only on chrominance naturally rejects highlights.
3. **Polarization filter.** A linear polarizer on the camera, rotated to minimize specular glare, can suppress 70-90% of highlights. Impractical for a phone-in-IriunWebcam setup unless the user has a clip-on filter.
4. **Highlight detection + inpainting.** Detect saturated-white pixels (V > 240 AND S < 30), then `cv2.inpaint` with `INPAINT_TELEA` before HSV threshold. ~3-5 ms overhead at 640×480. Worth implementing if matte sticker isn't an option.
5. **Specular detection via dichromatic model.** The Tan-Ikeuchi algorithm (1985) separates diffuse and specular components per-pixel. Heavy; only justified if (1)-(4) fail.

### 2.3 Motion blur at high RPM (90+ RPM)

At 90 RPM the pedal completes 1.5 rev/s = 540°/s. At a typical 640×480 frame with a 200-px-radius pedal arc, the sticker moves up to ~1900 px/s tangentially. At 1/60 s exposure (default on most phone cameras in good light), that's **~32 px of motion blur per frame** — a 20 px sticker becomes an elongated streak.

At 120 RPM with 1/30 s exposure (low light): **~85 px of blur** — the streak is longer than the sticker, the centroid is still correct (in the *middle* of the streak) but the contour's circularity drops below `MIN_CIRCULARITY=0.5` and detection fails.

Mitigations:

1. **Force a short exposure time.** iOS AVFoundation exposes `activeVideoMaxFrameDuration`; IriunWebcam does *not* expose this control. **Workaround:** shoot in brighter light so the phone's auto-exposure chooses a faster shutter. **Recommendation in README:** "Aim for 1/200 s or faster; use a desk lamp."
2. **Lower the circularity threshold when motion is high.** Replace `MIN_CIRCULARITY = 0.5` with an adaptive threshold based on detected angular velocity: `circ_threshold = max(0.2, 0.5 - 2*abs(omega)/max_omega)`.
3. **Use the streak's principal axis as an angular velocity estimate.** If the contour is elongated, fit an ellipse (`cv2.fitEllipse`) and use its orientation as a tangent angle. The major-axis direction equals the velocity vector — useful when the centroid is noisy.
4. **Detect via color *projection* not contour.** Sum the mask along rows/columns of the ROI; the 1-D peak is the centroid. This is more blur-tolerant than 2-D contour area thresholding.
5. **Run detection at 60+ FPS.** See §7. Half the inter-frame motion means half the blur per frame.

### 2.4 Adaptive thresholding

VeloTracker currently implements a simple running-mean adaptive scheme (`detector.py` L121-141):

```python
mean_hsv = np.array(cv2.mean(hsv, mask=c_mask)[:3])
drift = mean_hsv - self._initial_mean_hsv
target_lower = self._hsv_lower_init + drift
alpha = 0.02
self._hsv_lower = (1.0 - alpha) * self._hsv_lower + alpha * target_lower
# Clipped to ±15 of init, then clipped to [0,255]/[0,179]
```

**Critique:**

- **α=0.02 is too slow.** At 30 FPS this has a time-constant of ~1.6 s; a cloud passing over the bike takes <2 s. Use α=0.1-0.15 (time-constant ~0.2-0.3 s).
- **±15 clip is asymmetric and arbitrary.** Hue should be allowed to drift more (lighting color temperature shifts can move H by 20+), but saturation should barely move (S is intrinsic to the sticker). Per-channel clips: H ±20, S ±30, V ±40.
- **Updating the range on every detection is dangerous** when the contour includes occluding background — the mean is then biased. Update only when `detection_confidence > 0.7` (which VeloTracker already computes as circularity, L171).
- **No outlier rejection.** A single bad frame with a glare hotspot will permanently corrupt the running mean. Add a Mahalanobis-distance check: if `|mean_hsv - prev_mean_hsv| > 3*σ`, skip the update.

**Better alternatives (in order of complexity):**

1. **MOG2 background subtraction** (`cv2.createBackgroundSubtractorMOG2`) trained on a few stationary-bike frames, then mask the foreground for color matching. Handles lighting drift implicitly because the model adapts. ~5 ms overhead.
2. **CAMShift** (`cv2.CamShift`) — continuously adapts the *color histogram* (not just the mean) of the tracked object. Built into OpenCV, designed exactly for this. **Highly recommended.** VeloTracker would benefit from replacing its hand-rolled adaptive HSV with `cv2.CamShift` on the HSV back-projection.
3. **Mean-shift with a learned histogram** — same idea, no scale adaptation. Less suitable than CAMShift here.
4. **k-means on the ROI** (k=2 or 3) to separate sticker/background/other; pick the cluster whose centroid best matches the initial HSV. Slower (~10-15 ms at 640×480) but very robust. Use only when CAMShift loses lock.

### 2.5 Shadow handling

VeloTracker already does the right thing: `CLAHE` on the V channel (`detector.py` L62-66). CLAHE (Contrast-Limited Adaptive Histogram Equalization) locally renormalizes brightness, suppressing shadows.

**Tuning recommendations:**

- `CLAHE_CLIP_LIMIT = 3.0` (current) is reasonable. Range 2.0-4.0; lower = less aggressive (preserves more natural contrast), higher = more aggressive (better shadow suppression but more noise amplification).
- `CLAHE_TILE_SIZE = 8` (current) is a good default. For pedal-scale shadows (10-50 px), 8×8 tiles at 640×480 (so 80×60 px per tile) is appropriate. Smaller tiles (4×4) give finer shadow correction but more noise.
- Apply CLAHE **before** bilateral filter, not after (current code is `bilateralFilter → split → CLAHE on V`). The bilateral filter will smooth the CLAHE-enhanced V channel and partly undo the contrast boost. **Reorder:** split → CLAHE on V → merge → bilateral filter on the RGB image.
- Consider **CLAHE on the L channel of LAB** if migrating to LAB (see §2.1).

### 2.6 Preprocessing filter comparison

| Filter | Edge preservation | Color noise reduction | Speed (640×480) | Use case |
|---|---|---|---|---|
| **Gaussian blur** | Poor — blurs edges | Good | ~1 ms | General smoothing; loses edge info needed for crisp contours |
| **Median blur** | Good — preserves step edges | Excellent for salt-and-pepper | ~3 ms (k=5) | Best for impulsive noise; preserves contour boundaries well |
| **Bilateral filter** | **Best** — preserves edges while smoothing | Good | ~8-12 ms (d=9, σs=75, σr=75) | Best for color blob detection; what VeloTracker uses |
| **Guided filter** | Good | Good | ~5 ms | Alternative to bilateral; sometimes faster on CPU |

VeloTracker uses `cv2.bilateralFilter(processed_frame, 9, 75, 75)`. This is a defensible choice. **Tuning notes:**

- `d=9` (kernel diameter) is small. For 640×480 with a ~20 px sticker, `d=5` is sufficient and ~2× faster.
- `σs=75, σr=75` (spatial and color sigma) are large — heavy smoothing. For a noisy phone camera this is appropriate. If the sticker is matte (so specular highlights are absent), you can drop σr to 50.
- **Alternative:** switch to median blur (`cv2.medianBlur(frame, 5)`). It's faster, edge-preserving, and immune to the salt-and-pepper noise that phone cameras produce in low light. Empirically it outperforms bilateral for HSV blob detection in many comparative studies.

**Recommendation:** Benchmark bilateral vs median on real VeloTracker footage; median is likely faster with equivalent quality. Keep both as config options.

---

## 3. Circle Fitting Algorithms — Mathematical Comparison

### 3.1 The problem

Given n points {(xᵢ, yᵢ)} observed with isotropic Gaussian noise around a true circle with center (ã, b̃) and radius R̃, recover (a, b, R). This is a classic problem in computer vision, statistics, and metrology. The reference work is **Al-Sharadqah & Chernov, "Error analysis for circle fitting algorithms," arXiv:0907.0421** ([arxiv.org/pdf/0907.0421](https://arxiv.org/pdf/0907.0421)), with extensions in **Chernov et al., "Further statistical analysis of circle fitting," EJS 8(2), 2014** ([projecteuclid.org/.../10.1214/14-EJS971.pdf](https://projecteuclid.org/journals/electronic-journal-of-statistics/volume-8/issue-2/Further-statistical-analysis-of-circle-fitting/10.1214/14-EJS971.pdf)) and **Kanatani & Rangarajan, "Hyper least squares fitting of circles and ellipses," Computational Statistics & Data Analysis 55(6), 2011** ([ideas.repec.org/a/eee/csdana/v55y2011i6p2197-2208.html](https://ideas.repec.org/a/eee/csdana/v55y2011i6p2197-2208.html)).

Key general result from Al-Sharadqah & Chernov: **all algebraic circle fits (Kasa, Pratt, Taubin, Hyper) have the same covariance matrix to leading order in noise.** They differ only in second-order (essential bias) terms. So the choice of algorithm matters most when (a) noise is high, (b) the arc is partial, or (c) the number of points is small — all three apply to pedal tracking during the calibration phase.

### 3.2 Algorithm summary

| Method | Year | Type | Closed form? | Essential bias | Behavior on partial arcs | Cost |
|---|---|---|---|---|---|---|
| **Kasa** | 1976 (Delogne), rediscovered by Kasa 1976 | Algebraic, linear LS | Yes (3×3 linear solve) | **Large, biased toward small circles** | **Poor** — "heavily biased toward small circles when data points are sampled along a small arc" (Al-Sharadqah & Chernov §4) | Fastest (~μs) |
| **Taubin** | 1991 | Algebraic, generalized eigenvalue | Yes (3×3 eigen) | Small | **Good** — significantly better than Kasa on partial arcs | ~2× Kasa |
| **Pratt** | 1987 | Algebraic, generalized eigenvalue with `B²+C²−4AD=1` constraint | Yes (3×3 or 4×4 eigen) | Small, similar to Taubin | **Good** — comparable to Taubin | ~2× Kasa |
| **Hyperfit / Hyper** | 2009 (Al-Sharadqah & Chernov) | Algebraic, generalized eigenvalue with constraint `N = H` (linear combination of Taubin and Pratt constraint matrices) | Yes (SVD + small eigen) | **Zero** | **Best of the algebraic methods** | ~3× Kasa |
| **Geometric (ODR, Levenberg-Marquardt / Gauss-Newton)** | 1950s+ | Iterative minimization of ∑dᵢ² | No (iterative) | Zero (MLE) | Excellent, but: slow, can diverge, needs good initialization | 10-100× Kasa |
| **Kåsa with bracketing / RANSAC pre-filter** | various | Robust wrapper | varies | varies | Robust to outliers | varies |

### 3.3 Mathematical detail — why Kasa fails on partial arcs

Kasa minimizes the algebraic distance squared: `F_K = Σ(rᵢ² − R²)²` where `rᵢ² = (xᵢ−a)² + (yᵢ−b)²`. Algebraic distance `fᵢ = rᵢ² − R²` factors as:

```
fᵢ = (rᵢ − R)(rᵢ + R) = dᵢ · (2R + dᵢ) ≈ 2R · dᵢ
```

So `F_K ≈ Σ (2R)² · dᵢ² = 4R² Σdᵢ²`. **The Kasa fit minimizes `R² · Σdᵢ²`, not `Σdᵢ²`.** When the arc is partial (less than a full circle is observed), the fit can reduce `F_K` either by reducing `Σdᵢ²` *or by reducing R*. The optimizer finds a balance that systematically *underestimates* R. This is "Kasa's bias toward small circles." For pedal tracking during a 90-frame calibration at 30 FPS (3 seconds, ~4 revolutions at 80 RPM), the arc is full enough that Kasa works acceptably — but during the *first 30 frames* (before the full circle is observed) Kasa will give a poor fit.

### 3.4 Mathematical detail — Taubin and Pratt

Both replace the algebraic distance with a *normalized* algebraic distance:

- **Pratt:** minimizes `Σ[A·zᵢ + B·xᵢ + C·yᵢ + D]²` subject to `B² + C² − 4AD = 1`. The constraint `B² + C² − 4AD = 1` (instead of Kasa's `A = 1`) prevents the trivial solution and rescales the cost so that R² doesn't appear as a multiplicative factor.
- **Taubin:** minimizes the same numerator but with constraint `4A²·z̄ + 4AB·x̄ + 4AC·ȳ + B² + C² = 1`. This is *approximately* the inverse-variance normalization and removes most of Kasa's bias.

Empirically, **Pratt and Taubin perform nearly equally well** (Al-Sharadqah & Chernov §4) — there is no clear winner between them in practice, but Taubin is slightly more numerically stable when the data is poorly centered.

VeloTracker's implementation (`rpm_calculator.py` L128-212) uses Taubin. This is correct.

### 3.5 Hyperfit

Al-Sharadqah & Chernov's key contribution: by analyzing the *fourth-order* error terms, they construct a constraint matrix `H` (a specific linear combination of Taubin's `T` and Pratt's `P`) such that the essential bias vanishes exactly. The resulting fit:

- Is closed-form (generalized eigenvalue problem).
- Has **zero essential bias** — the only algebraic method with this property.
- **Outperforms the geometric fit** in their Monte Carlo experiments at high noise.
- Is 100% reliable (no divergence, unlike iterative geometric fit).

The Python package [`circle-fit`](https://pypi.org/project/circle-fit/) provides ready-to-use implementations: `circle_fit.kasa_fit`, `taubin_svd_fit`, `pratt_fit`, `hyper_fit`, `levenberg_marquardt` (geometric). **Drop-in replacement for VeloTracker's hand-rolled Taubin** — see §11.

### 3.6 Which is best for partial arcs (1/4 of circle)?

For pedal tracking, "partial arc" means the calibration phase before a full revolution is observed. At 80 RPM and 30 FPS, one revolution = 22.5 frames. So:

- After 5 frames: ~80° of arc (less than 1/4 circle) — *very* partial
- After 15 frames: ~240° (~2/3 circle)
- After 23 frames: full revolution

For <1/4 arc: **all algebraic fits struggle.** Kasa fails badly (bias 50%+). Pratt/Taubin give acceptable results (bias <10%) if the noise is low. Hyperfit is best.

**Recommendation:** Use **Hyperfit** for the calibration-phase fit. Keep Taubin for the continuous re-fit (L268-272 in `rpm_calculator.py`), where the 150-frame window contains 6+ revolutions and the difference between Hyperfit and Taubin is negligible.

Alternatively, **delay calibration** until at least one full revolution is captured (a simple heuristic: at least 360° of accumulated angular motion from the first detected position). This is more robust than fitting partial arcs at all.

### 3.7 Coefficient of Variation (CV) thresholds for acceptance

VeloTracker computes `cv = std(dists) / r` (`rpm_calculator.py` L207-208) and accepts the fit if `cv < 0.5` during calibration (L243) or `cv < 0.3` during tracking (L270).

| CV range | Quality | VeloTracker's action |
|---|---|---|
| < 0.05 | Excellent — near-perfect circle | Accept (correct) |
| 0.05 - 0.15 | Good — typical for stable tracking | Accept (correct) |
| 0.15 - 0.30 | Marginal — sticker path is elliptical or noisy | Accept with re-fit (correct) |
| 0.30 - 0.50 | Poor — likely partial arc, elliptical camera angle, or bad detections | Accept only during calibration (correct — better than no fit) |
| > 0.50 | Reject — camera moved, multiple stickers, or noise | Reject (correct) |

**The thresholds are well chosen.** One refinement: track CV over time and trigger a recalibration (`reset_center`) automatically if CV has been > 0.4 for > 2 seconds (camera moved). Currently the user must press `C`.

### 3.8 Minimum number of points

- **Theoretical minimum:** 3 points determine a unique circle (if not collinear).
- **Practical minimum for stable algebraic fit:** 5-10 points (VeloTracker uses 5, `rpm_calculator.py` L132).
- **Recommended minimum:** 10-15 points for Taubin, 15-20 for Hyperfit (more points needed to realize its accuracy advantage).
- **For full-circle calibration:** 1.5 revolutions minimum (33 frames at 80 RPM, 30 FPS) to average over any angular bias.

---

## 4. RPM Calculation from Angular Position

### 4.1 Phase unwrapping of atan2

VeloTracker's implementation (`rpm_calculator.py` L279-300) is the textbook approach:

```python
angle = math.atan2(dy, dx)
delta = angle - self._prev_angle
if delta > math.pi:    delta -= 2 * math.pi
elif delta < -math.pi: delta += 2 * math.pi
self._total_angle += delta
```

This is correct for the standard case. Edge cases:

1. **Wrapped delta larger than π** (e.g., the sticker teleports due to a mis-detection). The unwrap logic treats a true +3π/2 jump as a −π/2 jump. The glitch rejection (`MAX_DELTA_ANGLE`) catches this.
2. **Reversed direction.** VeloTracker tracks `self._rotation_direction` but doesn't use it in the unwrap logic. If a user backpedals, the `total_angle` will continue accumulating in the original direction (wrong). Add: if `sign(delta) != rotation_direction` for > 5 consecutive frames, flip the direction.
3. **Sticker passing through the center region.** `if math.sqrt(dx*dx + dy*dy) < 5: return` (L276) — skips points too close to the center. Good, but 5 px is arbitrary. Make it `0.1 * radius`.

### 4.2 Glitch rejection

```python
max_delta = self.MAX_DELTA_ANGLE * (dt / 0.033)  # = 1.0 * (dt / 0.033)
if abs(delta) > max_delta:
    self._prev_angle = angle
    return
```

At 30 FPS (`dt=0.033`), `max_delta = 1.0 rad ≈ 57°/frame`. To exceed this at 30 FPS you'd need > 1725 RPM — impossible. **This threshold is effectively disabled for human cadence.** Recommended tightening:

```python
# Tightest plausible: 200 RPM at 30 FPS = 200/60 * 2π / 30 = 0.70 rad/frame
# Add 3× margin for safety: 2.1 rad/frame
max_delta = 0.7 * (dt / 0.033) * 3.0  # = 2.1 * (dt/0.033)
```

Or better, **use the predicted angular velocity from the Kalman filter**:

```python
expected_delta = self._omega * dt
if abs(delta - expected_delta) > 0.5:  # > 0.5 rad off prediction
    # glitch — reject
```

This is much tighter and adaptive. VeloTracker already has `_omega` (L389) — just use it.

### 4.3 Smoothing methods compared

| Method | Latency | Noise rejection | Handles acceleration | Implementation | Verdict |
|---|---|---|---|---|---|
| **Moving average** (over last N RPM samples) | N/2 frames | Good for stationary | Poor (lags) | Trivial | Bad — introduces phase lag, fails during sprints |
| **Simple OLS regression** of (t, total_angle) | Window/2 | Good | Yes (slope) | Trivial | Decent. VeloTracker's base. |
| **Weighted OLS with exponential decay** (VeloTracker's choice) | ~1/λ seconds | Excellent | Yes | Trivial | **Good.** Best balance for this use case. |
| **Kalman filter (1-D, on angle)** | Zero (instantaneous) | Good | Yes (with accel) | Medium | Better than OLS but harder to tune |
| **Complementary filter** (high-pass gyro + low-pass vision) | Low | Good | Yes | Trivial | **Irrelevant** — no gyro here |
| **Savitzky-Golay** | Window/2 | Good | Yes | Medium | Equivalent to weighted OLS with different weights |
| **Median filter** | Window/2 | Excellent for impulsive noise | Poor | Trivial | Bad for RPM (lags during sprints) |

VeloTracker's choice (weighted OLS with exponential decay, λ=2.0, window=20) is sound. **Issues:**

1. **Window is in samples, not seconds.** At 30 FPS this is 0.67 s; at 15 FPS (IriunWebcam in poor Wi-Fi) this is 1.33 s — much more lag. **Fix: make the window time-based** (`window_seconds = 0.7`, `window_size = int(window_seconds * fps)`).
2. **λ=2.0 corresponds to a half-life of ln(2)/2 ≈ 0.35 s.** This is good for responsiveness, but the recent samples dominate the regression so heavily that with only 20 samples the effective sample count is ~5. **Either increase λ (slower decay) or increase the window.** Recommend λ=1.0 (half-life 0.69 s) and window=30 (1.0 s at 30 FPS).
3. **No outlier rejection within the window.** A single bad angle sample (e.g., glitch that survived the max_delta check) biases the slope. Use IRLS (iteratively reweighted least squares) or simply drop samples with studentized residual > 3.

### 4.4 Weighted OLS — recommended λ

The decay is `wᵢ = exp(−λ · (t_now − tᵢ))`. The effective sample count is `N_eff = Σwᵢ² / Σwᵢ² ≈ 1/(2λ·Δt)` for a long window at constant Δt. To target `N_eff ≈ 10`:

- At 30 FPS (Δt=0.033): λ = 1/(2·10·0.033) = **1.5**
- At 60 FPS (Δt=0.017): λ = 1/(2·10·0.017) = **3.0**

So **λ should scale with frame rate**, not be a fixed constant. Current λ=2.0 is reasonable for 30 FPS only.

### 4.5 Angular acceleration smoothing

VeloTracker computes `alpha` (angular acceleration) as:

```python
raw_alpha = (omega - prev_omega) / dt
self._alpha = 0.1 * raw_alpha + 0.9 * self._alpha  # heavy smoothing
```

The 0.1/0.9 split gives a time-constant of ~10 frames (~0.33 s at 30 FPS). This is fine for the prediction use case (extrapolating during occlusions up to 3 s). The issue is that the *prediction* uses `_omega + _alpha * time_since` (L326), which extrapolates linearly — fine for 0.5 s, but for 2+ s of occlusion the angular acceleration model will diverge. **Add a quadratic damping:** after 1 s of occlusion, decay `_alpha` toward zero (`_alpha *= 0.95` per frame).

### 4.6 Circular predictor for occlusion handling

The current predictor (`update_lost`, L310-353) is well-engineered:

- Extrapolates `prev_angle + omega_pred * dt` using `omega + alpha*dt`
- Caps `omega_pred` at 1.5× last known omega (prevents runaway acceleration)
- Refuses to reverse direction (prevents oscillation near ω≈0)
- After `RPM_TIMEOUT_SEC` (3 s), decays RPM by `RPM_DECAY_FACTOR=0.85` per frame

**Issues:**

- The 3 s timeout is generous. Real occlusions (pedal arm passes in front of sticker) last <0.5 s. After 1 s of no detection, you should *not* predict — you should signal "tracking lost" and let the dashboard show 0.
- The decay factor 0.85 per frame at 30 FPS gives a time-constant of ~0.19 s (RPM drops to 1/e in 6 frames). **Too fast** — RPM visually collapses during a 0.5 s occlusion. Use 0.95 (time-constant ~0.65 s).
- No use of the known radius/center for prediction. If the sticker is occluded, the predictor knows the *circle* it should be on but doesn't check the predicted position against the *next* detection — the next detection could be anywhere on the circle. Add: when detection resumes, snap `_prev_angle` to the actual measured angle (already done implicitly via the unwrap, but the *total_angle* may have accumulated too much — verify with the revolution count).

### 4.7 Recommended window sizes for different RPMs

For weighted OLS, the optimal window contains ~1-2 revolutions (so the regression has multiple "cycles" of data to average over):

| RPM | Rev period | Window at 30 FPS | Window at 60 FPS |
|---|---|---|---|
| 30 | 2.0 s | 60 frames | 120 frames |
| 60 | 1.0 s | 30 frames | 60 frames |
| 90 | 0.67 s | 20 frames | 40 frames |
| 120 | 0.5 s | 15 frames | 30 frames |
| 150 | 0.4 s | 12 frames | 24 frames |

**VeloTracker's fixed 20-frame window** is right for 90 RPM only. **Adaptive window:** `window = max(10, min(60, int(1.5 * 60 / max(rpm, 30))))` — keeps ~1.5 revolutions in the window.

---

## 5. Kalman Filter for Sticker Position Tracking

### 5.1 VeloTracker's current implementation

`StickerKalmanFilter` (`rpm_calculator.py` L12-76) is a textbook 4-D linear Kalman filter:

- **State:** `[x, y, vx, vy]` (constant-velocity model)
- **Transition:** `F = [[1,0,dt,0],[0,1,0,dt],[0,0,1,0],[0,0,0,1]]`
- **Process noise:** `Q = q · [[dt³/3, 0, dt²/2, 0], [0, dt³/3, 0, dt²/2], [dt²/2, 0, dt, 0], [0, dt²/2, 0, dt]]` (the standard discrete-time approximation of continuous white-noise acceleration)
- **Measurement:** `H = [[1,0,0,0],[0,1,0,0]]` (observe position only)
- **Tuning:** `q=100000`, `r=9`, initial `P=10·I`
- **dt clamping:** `min(dt, 0.2)` to prevent huge jumps on frame drops

### 5.2 4-D state vs 6-D state vs EKF on circular model

| Model | State | Captures | Linear? | Verdict |
|---|---|---|---|---|
| **4-D CV (VeloTracker)** | (x, y, vx, vy) | Position + linear velocity | Yes | OK for short-term smoothing. Fails to exploit the known circular motion — the filter will resist the natural curvature of the pedal path. |
| **6-D CA** | (x, y, vx, vy, ax, ay) | Position + velocity + acceleration | Yes | Better for sudden stops/starts. Still doesn't exploit circularity. |
| **6-D CTRV** | (x, y, v, θ, ω, ?) | Position + speed + heading + yaw rate | No (EKF) | The standard model for vehicle tracking. Adapts perfectly to circular motion. |
| **3-D on (θ, ω, α)** | (angle, angular velocity, angular acceleration) | Phase only | Yes (after center known) | **Recommended.** Once the circle is fit, switch from Cartesian filtering to filtering the angular state directly. Linear, simple, optimal for this problem. |

**Recommendation:**

1. **During calibration:** use 4-D CV (current implementation).
2. **During tracking:** switch to a 1-D or 3-D Kalman filter on the *angular* state `(θ, ω, α)`. This is linear, exploits the circular constraint exactly, and directly produces the RPM estimate. The Cartesian Kalman becomes redundant.

### 5.3 Process noise Q tuning

VeloTracker uses `q=100000`. The process noise variance per unit time is `q`, and the implied "expected acceleration std" is `sqrt(q) ≈ 316 px/s²`. For a pedal at 80 RPM with radius 150 px, the centripetal acceleration is `ω²r = (8.4)²·150 ≈ 10600 px/s²` — **30× larger than the filter expects.** This means the filter is *overly smoothing* and lags the actual motion significantly.

**Recommended:** For the Cartesian filter, set `q` to roughly the centripetal acceleration squared: `q ≈ (ω²·r)² ≈ 10⁸`. For an angular filter, the process noise should be the expected angular acceleration variance, ~`0.1 rad²/s⁴` (allows for typical cadence changes).

### 5.4 Measurement noise R tuning

`r=9` implies a measurement std of `3 px`. For a phone camera over Wi-Fi at 640×480, the actual centroid measurement noise is closer to `1-2 px` in good lighting and `5-10 px` in poor lighting. **Make R adaptive:** `r = base_r * (1 + blur_factor)`, where `blur_factor` is estimated from the contour's elongation (motion blur → larger measurement uncertainty).

### 5.5 Common pitfalls

1. **Numerical stability.** VeloTracker catches `LinAlgError` on `np.linalg.inv(S)` (L71-72), but doesn't use the more numerically stable Joseph-form covariance update `P = (I − KH)P(I − KH)ᵀ + KRKᵀ`. For a 4-D filter this is fine; for higher-dim filters use the Joseph form or a square-root Kalman filter (UD decomposition).
2. **dt variability.** `dt = min(dt, 0.2)` is a hard clamp — if a frame is delayed by 0.3 s, the filter uses dt=0.2 and the prediction is wrong by 0.1 s worth of motion. Better: detect the stall and reset the filter (`if dt > 0.2: reset()`).
3. **Initial P.** `P = 10·I` is small. The filter trusts its (uninitialized) state too much early on. Use `P = 1000·I` for the first 10 frames.
4. **State explosion on reset.** `reset()` sets `state = None`, but if a `predict_and_update` call comes in before any detection, it returns the uninitialized state. Add a guard.
5. **dt sign.** `if dt <= 0: return state` (L33) — this drops out-of-order timestamps. Good. But it returns the *predicted* state, not the *smoothed* state. For display purposes this is fine; for RPM estimation it's slightly wrong.

### 5.6 EKF for circular motion (CTRV)

If you want to keep filtering in Cartesian coordinates and exploit the circular motion, use the **Constant Turn Rate and Velocity (CTRV)** model. State: `(x, y, v, θ, ω)`. Transition:

```
x' = x + (v/ω) · [sin(θ + ω·dt) − sin(θ)]
y' = y + (v/ω) · [cos(θ) − cos(θ + ω·dt)]
v' = v
θ' = θ + ω·dt
ω' = ω
```

The Jacobian (for EKF) is well-documented; see [balzer82.github.io/Kalman](https://balzer82.github.io/Kalman) for a complete Python implementation. **For pedal tracking, v ≈ ω·r, so this reduces to a 3-D angular filter anyway** — use that instead (§5.2).

### 5.7 Tuning summary

| Parameter | VeloTracker | Recommended | Rationale |
|---|---|---|---|
| `q` (process noise) | 100000 | 10⁷–10⁸ (Cartesian) or 0.1 (angular) | Must match expected motion magnitude |
| `r` (measurement noise) | 9 | 4 (good light) / 25 (poor light), adaptive | Centroid noise in px² |
| `P₀` (initial cov) | 10·I | 1000·I | Don't trust uninitialized state |
| dt clamp | 0.2 s | reset on dt > 0.2 s | Stalls shouldn't be smoothed over |
| Update form | Simple `P = (I−KH)P` | Joseph form | Numerical stability |

---

## 6. Smart ROI / Dynamic Cropping

VeloTracker's current implementation (`detector.py` L40-57) is well-designed:

```python
if roi_circle is not None:
    cx_c, cy_c, r_c = roi_circle
    padding = max(30, int(r_c * 0.25))
    x_min = max(0, int(cx_c - r_c - padding))
    ...
    processed_frame = frame[y_min:y_max, x_min:x_max]
```

### 6.1 When to crop

- **Always crop when tracking.** The padding (25% of radius, min 30 px) is sensible.
- **Don't crop during calibration** — the full frame is searched, allowing the sticker to be found even if the user's initial guess of where the pedal is was wrong.
- **Don't crop too tightly.** If the predicted position is wrong (e.g., after a missed frame), a tight ROI will miss the sticker entirely. The 25% padding gives ~50 px of slack on a 200 px radius — adequate for one missed frame at 90 RPM.

### 6.2 Padding strategies

| Strategy | Pros | Cons |
|---|---|---|
| **Fixed padding** (VeloTracker's `max(30, 0.25·r)`) | Simple | Doesn't adapt to motion uncertainty |
| **Padding ∝ velocity** | Accounts for fast motion needing more search area | Slightly more compute |
| **Padding ∝ Kalman P** | Optimal — grows with filter uncertainty | Requires Kalman diagonal extraction |
| **Padding ∝ detection_confidence** | Inverse: tight when confident, loose when uncertain | Adaptive to lighting |

**Recommended:** Combine velocity-based and confidence-based padding:

```python
v = sqrt(vx**2 + vy**2)  # from Kalman
padding = max(30, 0.25 * r_c + v * dt)  # base + expected motion
if detection_confidence < 0.5:
    padding *= 1.5  # widen when uncertain
```

### 6.3 False positives vs detection failure

Tight ROI → false negatives (miss the sticker).
Loose ROI → false positives (catch other green objects: a green floor mat, a houseplant, clothing).

VeloTracker handles this with **proximity filtering** (`detector.py` L98-112): if `expected_position` is the circle `(cx, cy, r)`, it accepts only contours whose distance from the center is within `±0.3·r` of `r`. This is a strong constraint — it effectively means "the new detection must lie on the fitted circle."

**Issue:** This rejects valid detections if the circle fit drifted. If the camera moved, the old circle is wrong, and *every* new detection will be rejected by the proximity filter, locking the system into failure. **Mitigation:** if 10 consecutive detections are rejected by the proximity filter, reset the circle (`reset_center()`).

### 6.4 ROI size and compute scaling

At 640×480, full-frame HSV conversion + morphology takes ~5-8 ms. With a 200×200 ROI it drops to ~1 ms. This is the difference between 30 FPS achievable and 60 FPS achievable. **Always use ROI during tracking.**

---

## 7. Frame Rate Considerations

### 7.1 Nyquist and beyond

The naive Nyquist argument: at 120 RPM = 2 Hz cadence, sample at ≥4 Hz (15 FPS) to capture the fundamental. This is **wrong** for several reasons:

1. **You need the *angle*, not just the frequency.** To locate the pedal to ±10° (1/36 of a revolution), you need either very high SNR per sample or many samples per revolution. At 30 FPS and 120 RPM, you have 15 samples/rev → 24°/sample — too coarse for smooth RPM.
2. **Marker confusion.** As shown by [PMC 4788418](https://pmc.ncbi.nlm.nih.gov/articles/PMC4788418), the minimum frame rate scales as `v_max / d_min` where `d_min` is the minimum spacing between distinguishable features. For a single sticker, this is just the sticker diameter — so the relevant criterion is "frame rate × sticker blur < sticker diameter." At 120 RPM, radius 150 px: tangential velocity 1885 px/s, sticker diameter 20 px → need exposure × v < 20 px → exposure < 10 ms → 1/100 s shutter. Frame rate just needs to be fast enough to capture the position 15-30× per revolution.
3. **Phase lag.** A weighted-OLS window of N frames introduces ~N/2 frames of latency. At 30 FPS with window=20, that's 0.33 s = 1/2 revolution at 90 RPM. The displayed RPM is half a second behind reality. Higher FPS reduces this latency.

### 7.2 Minimum FPS for accurate RPM at 120 RPM

| FPS | Samples/rev (at 120 RPM) | Phase resolution | Verdict |
|---|---|---|---|
| 15 | 7.5 | 48°/sample | Inadequate — OLS slope estimate noisy |
| 30 | 15 | 24°/sample | Marginal — works but laggy |
| 60 | 30 | 12°/sample | **Recommended minimum for 120 RPM** |
| 120 | 60 | 6°/sample | Excellent — smooth even during sprints |
| 240 | 120 | 3°/sample | Overkill for cadence; useful for biomechanics |

**Practical minimum: 60 FPS for high-cadence (120+ RPM) tracking. 30 FPS is acceptable for typical 60-90 RPM riding.**

### 7.3 IriunWebcam latency characteristics

IriunWebcam streams the iPhone camera over Wi-Fi (or USB) to the computer, appearing as a virtual webcam. Empirical characteristics (from community reports — no formal benchmarks published):

- **Wi-Fi latency:** 80-200 ms one-way (depends on router, interference, encoding settings)
- **USB latency:** 30-80 ms (much better; use USB if possible)
- **Frame rate cap:** 60 FPS at 1080p, 30 FPS at 4K (iPhone 12+), configurable in Iriun settings
- **Variable frame rate:** Yes — Iriun drops frames silently when the network can't keep up. **This is a critical issue for VeloTracker** because the Kalman filter and OLS regression assume regular timestamps. Always use `time.time()` (wall clock) per frame, never assume `1/fps`.
- **Encoding:** H.264 over Wi-Fi. This introduces additional motion blur via inter-frame compression (B-frames can smear moving objects).

**Recommendations:**

1. **Use USB if possible** — 3× lower latency and more stable frame rate.
2. **Set Iriun to 720p@60 FPS** instead of 1080p@30 FPS. Lower resolution = less encoding time, higher frame rate = better cadence resolution.
3. **Disable video stabilization** in Iriun/iOS — stabilization adds latency and can introduce artificial motion.
4. **Monitor effective FPS** and warn the user if it drops below 20: `if camera.fps < 20: dashboard.show_warning("Low FPS — tracking quality degraded")`.

### 7.4 Handling variable frame rate

VeloTracker already does the right things:

- All state updates use wall-clock `time.time()` (e.g., `main.py` L102-104)
- Kalman filter uses `dt = timestamp - last_time` (not `1/fps`)
- OLS regression uses timestamps not frame indices

**One issue:** the `RPM_REGRESSION_WINDOW = 20` is in samples. At 30 FPS this is 0.67 s; at 15 FPS (network stutter) this is 1.33 s. **Fix:** make the window time-based and the sample count derived from the actual frame rate observed over the last second.

---

## 8. Edge Cases and Failure Modes

### 8.1 Sticker partially occluded by pedal arm

**Symptoms:** Detection drops out for 50-200 ms while the pedal arm passes between camera and sticker. Contour area drops below `MIN_CONTOUR_AREA`, detection returns `None`.

**Current handling:** `update_lost` extrapolates the position using `_omega + _alpha * dt`. Works for short occlusions (<0.5 s).

**Improvements:**

- Mount the camera at an angle where the pedal arm *never* occludes the sticker (e.g., slightly above the pedal plane). Document this in README.
- Lower `MIN_CONTOUR_AREA` temporarily when the predictor says the sticker should be visible.
- Use the contour *closest to the predicted position* rather than the *largest* contour (VeloTracker currently picks `max(valid, key=lambda x: x[1])` — the largest by area, L119). Switch to `min(valid, key=lambda x: dist_to_predicted)`.

### 8.2 Camera moves slightly during ride

**Symptoms:** The fitted circle (center, radius) is now wrong. New detections are off the old circle, get rejected by the proximity filter, RPM goes to zero.

**Current handling:** User must press `C` to recalibrate.

**Improvements:**

- Detect drift automatically: if the running CV (`_fit_circle` on `TRACKING_FIT_WINDOW`) exceeds 0.4 for > 2 s, trigger `reset_center()`.
- After a re-fit, the `_total_angle` accumulator should be preserved (you don't lose revolution count). VeloTracker's `reset_center` does preserve `_total_angle` (L113-126) — good.
- Use the Kalman filter's *predicted* center as a sanity check on the fitted center. If they diverge by > 0.5×radius, the camera has moved.

### 8.3 Lighting changes (cloud, sun, indoor)

**Symptoms:** Adaptive HSV drifts too slowly (or too fast and overshoots). Detection fails.

**Current handling:** Adaptive HSV update at α=0.02; CLAHE on V channel.

**Improvements:**

- Switch to LAB color space (§2.1) — far more robust.
- Use CAMShift instead of running-mean HSV (§2.4).
- Add a "lighting change detector": if the average V of the ROI changes by > 30 in one frame, suspend detection for 0.5 s while the adaptive HSV catches up.

### 8.4 Sweat on the sticker

**Symptoms:** Sticker color shifts (water film refracts), specularity increases, contour fragments.

**Mitigations:**

- Matte waterproof tape (gaffer tape, vinyl) instead of paper stickers.
- Apply LAB thresholding (§2.1) — water affects brightness more than chroma.
- Detect "wet" condition: high specular highlight ratio (count of pixels with V>240 & S<30 in the contour). When detected, widen the HSV range dynamically.

### 8.5 Very low RPM (<30)

**Symptoms:** At 30 RPM, the pedal moves slowly. Centroid jitter (1-3 px) becomes comparable to the inter-frame motion (e.g., 5 px/frame at 30 RPM, 30 FPS). The OLS slope estimate becomes dominated by noise.

**Mitigations:**

- Increase the regression window at low RPM (§4.7).
- Lower `RPM_MIN` threshold (currently 18) — but be careful: a stationary pedal should report 0 RPM, not 18. The current logic (L406-407) handles this: if `raw_rpm < RPM_MIN`, target=0.
- Add dead-band: if `|raw_rpm - current_rpm| < 2`, don't update (prevent jitter).
- Below 20 RPM, switch from continuous-angle tracking to **revolution counting** (count zero-crossings of the angle). More robust at low speed.

### 8.6 Very high RPM (>150)

**Symptoms:** Motion blur dominates (§2.3). Centroid becomes the center of an elongated streak, which is still correct, but contour circularity drops below threshold.

**Mitigations:**

- Lower `MIN_CIRCULARITY` adaptively at high RPM (§2.3).
- Force shorter exposure (recommend brighter lighting in README).
- Use `cv2.fitEllipse` and the ellipse's major axis as a tangent indicator.
- Switch to frequency-domain RPM (FFT of centroid-x time series) — robust to blur because the centroid is still correct, just noisy.

### 8.7 Multiple stickers in frame

**Symptoms:** Multiple contours pass the area/circularity filter. VeloTracker picks the largest.

**Mitigations:**

- Use the predicted position to pick the right one (§8.1).
- Require stickers to have a unique color (e.g., document "use only one green sticker; do not also have a green water bottle in frame").
- During calibration, detect all matching contours and let the user click the correct one (already done in `calibrate.py`).

---

## 9. Open Source References — Detailed

### 9.1 Viscyc ([github.com/csachs/viscyc](https://github.com/csachs/viscyc))

**Approach (from README, quoted above):**

> Nothing fancy. The camera image is thresholded using Otsu's method, and the mean intensity value of a centered, rectangular region of interest (ROI) is assessed. If something dark enters this region, a rotation will be detected.

**Architecture:**

1. User positions a rectangular ROI on a "dark feature" of the rotating part (e.g., the pedal arm, or a piece of tape).
2. Each frame: Otsu threshold the ROI, compute mean intensity. If below a threshold, a "dark thing is here" event fires.
3. Each pair of events = one revolution. RPM = 60 / (t_now − t_prev_event).
4. Power = custom function of RPM (user-supplied Python script; examples for Christopeit AM-6 cross-trainer).
5. BLE via the `zwack` library. Web UI via Flask.

**Lessons for VeloTracker:**

- **Simplicity wins for robustness.** Viscyc's pipeline has ~5 stages; VeloTracker has ~10. Viscyc's failure mode (missed event) is graceful (RPM stale for one rev); VeloTracker's failure mode (lost centroid) is catastrophic (RPM collapses).
- **Decoupled architecture.** Viscyc separates detector, sender, and manager as independent processes communicating via IPC. VeloTracker is monolithic. If the BLE thread blocks, the detector stalls. Consider splitting.
- **User-supplied power function.** Viscyc lets users define `calculate_watt(rpm)` per trainer. VeloTracker has hardcoded `POWER_MODEL` options. More flexible.

### 9.2 PeloMon ([github.com/ihaque/pelomon](https://github.com/ihaque/pelomon))

**Correcting the user's prompt:** PeloMon is **not a vision project.** It's a serial-tap + BLE relay for the Peloton bike. The four-part blog series ([ihaque.org/tag/pelomon.html](https://ihaque.org/tag/pelomon.html)) covers:

- **Part I:** Decoding the Peloton's RS-485 serial protocol between the head unit and the resistance sensor.
- **Part Ib:** How the Peloton computes speed (from flywheel RPM via Hall sensor).
- **Part II:** Emulating the Peloton's resistance sensor (so the bike thinks it's getting a resistance command).
- **Part III:** Hardware (custom PCB with STM32 Blue Pill, power delivery, signaling).
- **Part IV:** The code — implementing BLE CSC (0x1816) and FTMS (0x1826) services.

**Relevance to VeloTracker:**

- **Part IV is essential reading** for anyone implementing BLE CSC/FTMS. PeloMon's 2 Hz notify interval is the proven minimum that Zwift/TrainerRoad/MyWhoosh accept. VeloTracker correctly uses `BLE_NOTIFY_INTERVAL_SEC = 0.5`.
- **CSC Service Revolution Counter:** PeloMon documents the importance of monotonic revolution counting. VeloTracker's `_rev_count` (L305-308) follows this pattern correctly.
- **FTMS control point handling:** PeloMon's implementation of FTMS negotiation is more complete than VeloTracker's; worth comparing if MyWhoosh users report FTMS issues.

### 9.3 blum.bike ([github.com/sciguy14/blumbike](https://github.com/sciguy14/blumbike))

**This is the "trainer-bearing approach" the user asked about.** Jeremy Blum's design:

- A piece of **black tape** is applied to the trainer's flywheel.
- A **QRD1114 reflective IR optical sensor** is mounted ~5 mm from the flywheel surface.
- The sensor's phototransistor output goes through an **OPA344 op-amp with hysteresis** (Schmitt trigger), producing a clean digital pulse each time the black tape passes.
- A **Particle Photon** microcontroller counts pulses via interrupt, computes RPM = 60 / pulse_period, and publishes to the Particle Cloud.
- A Python/Plotly Dash web app on Heroku receives the data and displays speed/cadence in real time.
- Stepper motor on the resistance knob for ERG mode.

**Lessons for VeloTracker:**

- **Hardware sensing is 100× more reliable than vision.** If the user is willing to attach *anything* to the bike, a $2 reflective sensor beats a camera. VeloTracker's value proposition is precisely the "no hardware attached" case.
- **Pulse-per-revolution is the simplest possible signal.** VeloTracker's continuous-angle tracking is *richer* (you get instantaneous RPM, not just event intervals), but also *more fragile*. A future version of VeloTracker could offer a "low-quality mode" that drops to pulse-counting when the circle fit is unreliable.
- **Cloud/web UI.** blum.bike's web dashboard is much more polished than VeloTracker's OpenCV HUD. If VeloTracker wants to compete as a product, a web/mobile UI is needed. (Out of scope for this audit, but noted.)

### 9.4 Other "smart trainer from webcam" projects

The audit found **no other mature open-source project doing vision-based cadence.** VeloTracker appears to be unique in this niche. Related projects:

- **OpenTracks** (Android) — uses phone sensors, not camera. Has cadence aggregation logic relevant to smoothing ([OpenTracks issue #1839](https://github.com/OpenTracksApp/OpenTracks/issues/1839)).
- **QZ (qdomyos-zwift)** — supports many trainers via Bluetooth; no vision. Useful as a comparison for BLE FTMS implementation.
- **Instructables DIY Smart Bike Trainer** — uses reed switches; no vision.

### 9.5 The "trainer-bearing approach" — what it looks like

Summarizing the pattern shared by blum.bike, SmartSpin2k, and most commercial smart trainers:

```
[rotating part] --(marking: magnet, tape, hole)--> [sensor: reed, optical, hall]
                                                           |
                                                           v
                                              [pulse per revolution]
                                                           |
                                                           v
                                                  [RPM = 60 / Δt]
                                                           |
                                                           v
                                              [BLE CSC service broadcast]
```

The key insight: **one pulse per revolution is the minimum viable signal**, and it's what every commercial smart trainer uses. VeloTracker's continuous-angle tracking is an *upgrade* on this, but should be willing to *fall back* to it when vision is unreliable. Recommended pattern:

```
[vision centroid] --> [circle fit]
                          |
                  +-------+-------+
                  |               |
              [CV < 0.3]      [CV > 0.3]
                  |               |
                  v               v
        [angle tracking]    [pulse counting]
        [weighted OLS]      [RPM = 60/Δt_rev]
                  |               |
                  +-------+-------+
                          |
                          v
                     [BLE broadcast]
```

---

## 10. Anti-Patterns to Avoid

### 10.1 Naive thresholding without lighting compensation

**Anti-pattern:** Hardcoded HSV range, no CLAHE, no adaptive update. Fails within minutes as lighting drifts.

**VeloTracker status:** ✅ Already does CLAHE + adaptive HSV. Improvements possible (LAB, CAMShift) per §2.

### 10.2 Single-frame circle fitting (vs sliding window)

**Anti-pattern:** Fit a circle to the current frame's detections only. One bad frame → garbage circle.

**VeloTracker status:** ✅ Uses sliding windows: 90 frames for calibration, 150 for tracking re-fit (L268). Good.

### 10.3 Fixed smoothing factor (vs adaptive)

**Anti-pattern:** One EMA α for all conditions. Lags during sprints, jitters during steady-state.

**VeloTracker status:** ⚠️ `RPM_EMA_ALPHA = 0.15` is fixed. The weighted-OLS λ is also fixed. **Fix:** make α depend on `|omega|` (lower α at high omega for responsiveness, higher α at low omega for stability). Make λ scale with FPS (§4.4).

### 10.4 Not handling phase unwrapping correctly

**Anti-pattern:** Use raw `atan2` as the angle. Jumps from +π to −π every revolution. RPM is garbage.

**VeloTracker status:** ✅ Correctly unwraps (L285-289). One subtle bug: the `_prev_angle` is set to the wrapped `angle` even when the delta is rejected (L297). On the next frame, the unwrap is computed against this stale `_prev_angle`, which may have been the glitch. **Fix:** don't update `_prev_angle` when the delta is rejected.

### 10.5 Picking the largest contour instead of the closest-to-predicted

**Anti-pattern:** `largest = max(contours, key=area)`. Wrong if a larger but irrelevant colored object enters the frame.

**VeloTracker status:** ⚠️ `max(valid, key=lambda x: x[1])` (L119) — picks largest area. **Fix:** when `expected_position` is available, pick `min(valid, key=lambda x: dist_to_predicted)`.

### 10.6 Mixing units (samples vs seconds)

**Anti-pattern:** Window sizes in frames, dt computations assuming constant FPS. Breaks under variable FPS.

**VeloTracker status:** ⚠️ `RPM_REGRESSION_WINDOW = 20` is in samples. **Fix:** convert to seconds (`window_seconds = 0.7`) and derive sample count from observed FPS.

### 10.7 Redundant smoothers in cascade

**Anti-pattern:** EMA on the centroid *and* Kalman filter on the centroid *and* EMA on the RPM. Three low-pass filters in series → massive phase lag.

**VeloTracker status:** ⚠️ `detector.py` L148-154 has an EMA with α=0.8 on the centroid. `rpm_calculator.py` L222 puts the (already EMA'd) centroid through a Kalman filter. `rpm_calculator.py` L422 puts the (already Kalman'd) RPM through another EMA with α=0.15. **Fix:** remove the centroid EMA in `detector.py` (let the Kalman filter do its job). Keep the RPM EMA only for *display* (not for BLE broadcast — BLE should get the Kalman-estimated omega).

### 10.8 Not validating circle fit results

**Anti-pattern:** Trust whatever the fit returns. NaN, inf, negative radius all propagate.

**VeloTracker status:** ✅ Checks `center and radius >= MIN_RADIUS_PX and cv < 0.5` (L243). Good. Could also check `radius < MAX_RADIUS_PX` (e.g., 500 px) to reject garbage fits.

### 10.9 Treating detection loss as RPM=0

**Anti-pattern:** When the sticker isn't detected, set RPM=0 immediately. The user sees RPM flicker between 80 and 0.

**VeloTracker status:** ✅ Has `update_lost` with circular prediction. Good. The 3 s timeout and 0.85/frame decay are slightly too aggressive (§4.6) but the pattern is correct.

### 10.10 Using wall-clock time for inter-revolution intervals when BLE expects monotonic counters

**Anti-pattern:** Compute revolution time as `time.time()` and store in BLE CSC. Drops frames due to clock drift.

**VeloTracker status:** Should verify. The CSC service expects a 1/1024 s resolution monotonic counter. VeloTracker's `last_rev_event_time` (L308) uses `time.time()` (wall clock). **Fix:** use a monotonic clock (`time.monotonic()` in Python) for all timing, then convert to the BLE CSC time format at broadcast time.

---

## 11. Prioritized Improvement Recommendations for VeloTracker

Ordered by impact-to-effort ratio.

### Tier 1 — High impact, low effort (do these first)

1. **Fix the README.** "Kasa circle fit" → "Taubin circle fit" (the code already uses Taubin).
2. **Remove the redundant EMA in `detector.py` L148-154.** The Kalman filter already smooths the centroid. Removing this EMA reduces phase lag.
3. **Tighten glitch rejection** from 1.0 rad to a predicted-omega-based threshold (§4.2).
4. **Switch from `time.time()` to `time.monotonic()`** for all internal timing (§10.10).
5. **Pick contour closest to predicted position**, not largest (§10.5, §8.1).
6. **Make `RPM_REGRESSION_WINDOW` time-based** instead of sample-based (§4.3, §10.6).
7. **Add automatic recalibration** when CV drifts high for >2 s (§8.2).

### Tier 2 — High impact, medium effort

8. **Migrate to LAB color space** (§2.1). Big robustness gain for lighting changes.
9. **Replace adaptive HSV with CAMShift** (§2.4). Better tracking of color distribution.
10. **Replace Taubin with Hyperfit** for calibration (§3.5). Drop in via `pip install circle-fit`.
11. **Add an angular Kalman filter** `(θ, ω, α)` that runs in parallel with the Cartesian one during tracking (§5.2). Use its omega for RPM.
12. **Add a "low-quality mode" fallback** to revolution counting when the circle fit is unreliable (§9.5).
13. **Reorder preprocessing:** CLAHE on V *before* bilateral filter (§2.5).
14. **Adaptive `MIN_CIRCULARITY`** based on detected angular velocity (§2.3).

### Tier 3 — High impact, high effort (consider for v2)

15. **Add ArUco marker mode** as an alternative to colored sticker (§1.4). Already prototyped in `test_aruco_cam.py`.
16. **Add a frequency-domain RPM fallback** (FFT of centroid-x time series) (§1.4, §8.6).
17. **EKF with CTRV model** for Cartesian state (§5.6). Marginal benefit over the angular Kalman.
18. **Specular highlight detection + inpainting** (§2.2).
19. **Per-trainer power calibration UI** (viscyc-style user-supplied function) (§9.1).
20. **Decouple detector/sender/manager into separate processes** (viscyc-style) (§9.1).

### Tier 4 — Documentation

21. Document the **camera mounting angle** that avoids pedal-arm occlusion (§8.1).
22. Recommend **matte waterproof stickers** (§2.2, §8.4).
23. Recommend **USB over Wi-Fi** for IriunWebcam (§7.3).
24. Document the **60 FPS minimum** for high-cadence (120+ RPM) tracking (§7.2).

---

## 12. Key Bibliography

### Circle fitting

- Al-Sharadqah, A. & Chernov, N. **"Error analysis for circle fitting algorithms."** arXiv:0907.0421 (2009). [arxiv.org/pdf/0907.0421](https://arxiv.org/pdf/0907.0421)
- Chernov, N. et al. **"Further statistical analysis of circle fitting."** Electronic Journal of Statistics 8(2), 2014. [projecteuclid.org/.../10.1214/14-EJS971.pdf](https://projecteuclid.org/journals/electronic-journal-of-statistics/volume-8/issue-2/Further-statistical-analysis-of-circle-fitting/10.1214/14-EJS971.pdf)
- Kanatani, K. & Rangarajan, P. **"Hyper least squares fitting of circles and ellipses."** Computational Statistics & Data Analysis 55(6):2197-2208, 2011. [ideas.repec.org/a/eee/csdana/v55y2011i6p2197-2208.html](https://ideas.repec.org/a/eee/csdana/v55y2011i6p2197-2208.html)
- Taubin, G. **"Estimation of Planar Curves, Surfaces, and Nonplanar Space Curves Defined by Implicit Equations with Applications to Edge and Range Image Segmentation."** IEEE TPAMI 13(11):1115-1138, 1991.
- Pratt, V. **"Direct least-squares fitting of algebraic surfaces."** Computer Graphics 21:145-152, 1987.
- Kåsa, I. **"A curve fitting procedure and its error analysis."** IEEE Trans. Inst. Meas. 25:8-14, 1976.
- `circle-fit` Python library: [pypi.org/project/circle-fit](https://pypi.org/project/circle-fit/) — implementations of all methods.

### Kalman filtering

- Balzer, J. **"Extended Kalman Filter with Constant Turn Rate and Velocity (CTRV) Model."** [balzer82.github.io/Kalman](https://balzer82.github.io/Kalman) — complete Python implementation.
- ArduPilot EKF documentation: [ardupilot.org/dev/docs/extended-kalman-filter.html](https://ardupilot.org/dev/docs/extended-kalman-filter.html) — practical tuning guidance.
- FlexKalmanNet (arXiv:2405.03034): [arxiv.org/html/2405.03034v1](https://arxiv.org/html/2405.03034v1) — learned noise covariances for EKF.

### Color detection and preprocessing

- **Comparing the Performance of L*A*B* and HSV Color Spaces with Respect to Color Image Segmentation** (arXiv:1506.01472): [arxiv.org/pdf/1506.01472](https://arxiv.org/pdf/1506.01472) — LAB outperforms HSV under varying illumination.
- LearnOpenCV color spaces: [learnopencv.com/color-spaces-in-opencv-cpp-python](https://learnopencv.com/color-spaces-in-opencv-cpp-python)
- PyImageSearch smoothing/blurring: [pyimagesearch.com/2021/04/28/opencv-smoothing-and-blurring](https://pyimagesearch.com/2021/04/28/opencv-smoothing-and-blurring)
- OpenCV CAMShift tutorial: [datahacker.rs/object-tracking-with-mean-shift-and-camshift-algorithms](https://datahacker.rs/object-tracking-with-mean-shift-and-camshift-algorithms)

### Frame rate and motion tracking

- **"How Fast Is Your Body Motion? Determining a Sufficient Frame Rate for an Optical Motion Tracking System."** PMC4788418: [pmc.ncbi.nlm.nih.gov/articles/PMC4788418](https://pmc.ncbi.nlm.nih.gov/articles/PMC4788418) — frame rate must exceed `v_max / d_min`, not just 2× Nyquist.
- Ultralytics FPS guide: [ultralytics.com/blog/understanding-the-role-of-fps-in-computer-vision](https://www.ultralytics.com/blog/understanding-the-role-of-fps-in-computer-vision)

### Cycling cadence specifically

- **"Cadence Detection in Road Cycling Using Saddle Tube Motion and Machine Learning."** Sensors 22(16):6140, 2022: [mdpi.com/1424-8220/22/16/6140](https://www.mdpi.com/1424-8220/22/16/6140) — IMU-based, useful as ground-truth comparison.
- **"Virtual reality application for real-time pedalling cadence estimation."** Springer VR 2022: [link.springer.com/article/10.1007/s10055-022-00668-w](https://link.springer.com/article/10.1007/s10055-022-00668-w)

### Open source projects

- **Viscyc:** [github.com/csachs/viscyc](https://github.com/csachs/viscyc) — Otsu + ROI mean-intensity revolution counter.
- **PeloMon:** [github.com/ihaque/pelomon](https://github.com/ihaque/pelomon), [ihaque.org/tag/pelomon.html](https://ihaque.org/tag/pelomon.html) — Peloton serial decoder + BLE relay. **Not vision.**
- **blum.bike:** [github.com/sciguy14/blumbike](https://github.com/sciguy14/blumbike), [jeremyblum.com/2020/09/13/blumbike](https://www.jeremyblum.com/2020/09/13/blumbike) — hardware reflective IR sensor + Particle Photon.
- **SmartSpin2k:** [github.com/doudar/SmartSpin2k](https://github.com/doudar/SmartSpin2k) — stepper-motor resistance control.
- **zwack:** [github.com/paixaop/zwack](https://github.com/paixaop/zwack) — BLE smart-trainer emulator (used by Viscyc).
- **pycycling:** [github.com/zacharyedwardbull/pycycling](https://github.com/zacharyedwardbull/pycycling) — BLE CSC/FTMS client for testing.

---

## 13. Audit Methodology

This audit was conducted by:

1. Inspecting the VeloTracker source code in `/home/z/my-project/velotracker/` — README, `config.py`, `main.py`, `modules/detector.py`, `modules/rpm_calculator.py`.
2. Reviewing the Viscyc, PeloMon, blum.bike, SmartSpin2k, zwack, and pycycling GitHub repositories via direct README fetches.
3. Reviewing the Al-Sharadqah & Chernov (2009) circle-fitting error analysis paper in full (PDF download + text extraction).
4. Reviewing the PMC 4788418 frame-rate criterion paper (abstract + introduction).
5. Reviewing the MDPI cadence detection paper (citation confirmed; full text behind access wall, abstract read).
6. Targeted web searches on each of the 10 topics in the audit scope.
7. Cross-referencing claims between academic sources, OSS implementations, and the VeloTracker code.

**Limitations:**

- The MDPI 2022 cadence paper full text was inaccessible (Akamai block); abstract and citations were used.
- The blum.bike website is Cloudflare-protected; the GitHub README was used instead.
- IriunWebcam latency figures are from community reports, not formal benchmarks.
- No empirical benchmarking of VeloTracker was performed — all recommendations are based on code inspection and literature review.
