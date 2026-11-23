# Acquisition Guide: getting a light-sheet stack off the ZEISS Lightsheet 7

How to go from a cleared sample to a tiled `.czi` file that the [processing pipeline](../Lightsheet_Guide.md) can read.
Written from a hands-on training session on the core facility's **ZEISS Lightsheet 7** running **ZEN 3.1 (black edition)**.

> [!TIP]
> **The short version.** Our lab keeps a saved experiment called **`rg5x`** that preloads every acquisition setting
> (lasers, light path, light sheet, camera, pixel size, z-step). Most of a session is hardware setup and checks; once the
> sample is in, you load `rg5x`, set the z-range and tiles around your sample, and start.

**Contents**
[0. Before you go](#0-before-you-go) ·
[1. Prepare the optics and chamber](#1-prepare-the-optics-and-chamber) ·
[2. Check the laser blocking filter](#2-check-the-laser-blocking-filter) ·
[3. Power on](#3-power-on) ·
[4. Start ZEN](#4-start-zen) ·
[5. Verify the hardware](#5-verify-the-hardware-maintain-tab) ·
[6. Align the light sheet](#6-align-the-light-sheet-adjust-light-sheet-wizard) ·
[7. Load the sample](#7-load-the-sample) ·
[8. Load `rg5x`](#8-load-the-lab-settings-rg5x) ·
[9. Fit the stack to your sample](#9-fit-the-z-stack-and-tiles-to-your-sample) ·
[10. Acquire](#10-acquire) ·
[11. Hand-off to the pipeline](#11-hand-off-to-the-processing-pipeline) ·
[Quick reference](#quick-reference-rg5x-settings) ·
[Troubleshooting](#troubleshooting)

---

## 0. Before you go

- [ ] Instrument time is booked (a whole brain takes hours: plan for setup + acquisition + cleanup).
- [ ] Your sample is cleared and equilibrated in refractive-index **matching solution, RI 1.51 to 1.52**.
- [ ] You have enough matching solution to fill the chamber.
- [ ] You have a **portable hard drive** with room for the data (data is saved there, then moved to the mouse's folder). ZEN shows the expected size next to **Start Experiment** before you start
      (a 3 × 6-tile, 2,584-plane run was ~530 GB). Processing later needs another 1.5 to 2× that.
- [ ] *Tip:* warm the chamber and matching solution up a little before use. This helps prevent precipitation.

---

## 1. Prepare the optics and chamber

**Objectives used for cleared brains**

| Role | Objective | Shown in ZEN as |
|---|---|---|
| Detection | 5× / 0.16, RI-adjustable | `EC Plan-Neofluar 5x/0.16 foc` |
| Illumination (beam-forming, left and right) | 5× / 0.1 | `LSFM 5x/0.1 foc` |

- The 5× beam-forming (illumination) objectives go with the 2×, 5× and 10× objectives for **clearing / matching solution**.
- The illumination objectives have **coloured RI rings** (scale 1.40 to 1.55). Set them to match the clearing solution: **1.52**.
  Set the detection objective's correction collar to the same value.

![Detection objective (centre) and the two illumination objectives with their RI rings](images/acquisition/01_chamber_objectives.jpg)

- Use the **sample chamber for cleared samples** (below).

![Sample chamber for cleared samples](images/acquisition/02_sample_chamber.jpg)

> [!WARNING]
> **If someone else left different objectives in**, always remove the beam-forming objectives *before* putting in the
> working objective (the **5×**). Usually the 5× set is already installed and there is nothing to swap.

---

## 2. Check the laser blocking filter

You rarely need to touch this, but you should know where it is.

1. Open the hatch on the side of the stand.
2. Turn the **comma-shaped knob** to loosen it, then pull the handle to slide out the laser blocking filter wheel.

![Access hatch for the laser blocking filter wheel; the comma-shaped knob is lower right](images/acquisition/03_filter_hatch.jpg)

| ![Laser blocking filter wheel](images/acquisition/04_filter_wheel.jpg) | ![Filter cube label: LBF 405/488/561/640](images/acquisition/05_filter_label.jpg) |
|---|---|

The wheel positions as configured in ZEN (see [step 5](#5-verify-the-hardware-maintain-tab)):

| Position | Filter |
|---|---|
| 1 | Plate |
| 2 | LBF 445/514/561 |
| 3 | **LBF 405/488/561/640** ← used for 488 + 561 imaging |
| 4 | NoneLSM |
| 5 | None |
| 6 | ND 4 (RGB) |

---

## 3. Power on

1. On the switch box, turn on **SYSTEM** and **PC** (the top two switches). Leave **INCUBATION** off; cleared samples do not need it.
2. Also press the PC's own **power button** on the tower.

![Switch box: SYSTEM, PC, INCUBATION](images/acquisition/06_power_switches.jpg)

---

## 4. Start ZEN

1. Before starting the software, check that the **offset controller** is set to **DEFAULT**. It resets to DEFAULT after every
   restart, so this is usually already fine.
2. Open **ZEN 3.1 (black edition)**. Do **not** use ZEN blue on this computer.
3. In the login window, under **Hardware configuration database**, click **Choose…** and select the Lightsheet 7 database:
   `C:\ZEN\database\New folder\Lightsheet_7_2583000217.mdb`.
   Do **not** choose the *Clearing Objective* database: the filter wheel is installed, so the standard database is correct.

| ![ZEN login: hardware configuration database](images/acquisition/07_zen_login.jpg) | ![Choosing the database in New folder](images/acquisition/08_database_folder.jpg) |
|---|---|

4. Click **Start System**.
5. If you changed the database, a pop-up says the **attenuation is incorrect** and asks whether to proceed: click **Yes**.
6. A second pop-up says the **motorized stage will now configure**.

> [!CAUTION]
> Make sure there is **no sample in the chamber** before you confirm the stage configuration. The stage moves on its own.

---

## 5. Verify the hardware (Maintain tab)

Open the **Maintain** tab → **Objectives**.

1. **Detection Optic** should read `EC Plan-Neofluar 5x/0.16 foc`.
2. **Illumination Optic** should read `LSFM 5x/0.1 foc`.
3. If either is wrong, click the **objective icon** → **Change Objective** and pick the objective that is physically installed.
   The 5× is standard.

| ![Detection optic check](images/acquisition/09_detection_objective.jpg) | ![Change Objective list](images/acquisition/10_change_objective.jpg) |
|---|---|

4. Open **Filters** → **Laser Blocking Filter** and check the order matches the wheel ([table in step 2](#2-check-the-laser-blocking-filter)).
   Mismatches are very rare, but the check is quick.

![Illumination optic and laser blocking filter list](images/acquisition/11_illumination_and_filters.jpg)

---

## 6. Align the light sheet (Adjust Light Sheet wizard)

The light sheet is aligned with the chamber full of **matching solution only, no sample**.

1. Fill the cleared-sample chamber with matching solution and mount it in the microscope.

   ![Chamber mounted in the microscope](images/acquisition/14_chamber_mounted.jpg)

2. In **Maintain** → **Options** → **Adjustment**, click **Adjust Light Sheet**.
3. The wizard's first page is a checklist. Confirm each item, then tick **Verified**:
   - sample chamber is mounted correctly,
   - the chamber is filled with the correct medium,
   - the sample is **not** in front of the detection objective,
   - the detection objective's focus ring / correction collar is set to the correct RI,
   - the illumination objectives' focus rings are set to the correct RI.

   ![Adjust Light Sheet checklist](images/acquisition/12_wizard_checklist.jpg)

4. Select **one laser line: 488**, and set the **refractive index to 1.52**.

   > [!IMPORTANT]
   > The refractive index box may default to **1.560**. Change it to **1.52**.

   ![Laser line and refractive index selection](images/acquisition/13_wizard_laser_ri.jpg)

5. Follow the rest of the wizard so the laser is correctly aligned through the chamber.

---

## 7. Load the sample

1. Mount the specimen in the holder and lower it into the chamber.

   ![Specimen holder in the chamber (top view)](images/acquisition/15_sample_loaded_top.jpg)

2. In the **Locate** tab, use the **overview camera** and the **specimen locator** to bring the sample into view.
3. In **Specimen Navigator**, set:
   - **Specimen diameter: 1200 µm** (a larger preview cylinder, so a whole brain fits),
   - **Refractive index: 1.520**.

---

## 8. Load the lab settings (`rg5x`)

1. Go to the **Acquisition** tab.
2. In **Experiment Manager**, click the **folder icon** and load **`rg5x`**. Every setting below is now in place.
   - *Fallback:* if `rg5x` is missing, load an existing image acquired with the right settings and press **Reuse** in the
     image view. ZEN copies that image's settings.
3. Work through the panels **top to bottom** and check each one against the [quick reference](#quick-reference-rg5x-settings).
   You should not need to change anything except the z-range and tiles.

![rg5x loaded in ZEN (live test with 488 only, Cam1)](images/acquisition/17_rg5x_settings.jpg)

**What `rg5x` sets up, and why**

- **One track, two cameras.** 488 nm and 561 nm fire together; a 560 nm dichroic (**SBS LP 560**) splits the emission onto
  **Cam1 (BP 505–545, green)** and **Cam2 (LP 585, red)**. Both channels are captured at once, so there is no
  track switching.
- **Single-side illumination from the left**, sheet thickness **7.32 µm**, **pivot scan on** (reduces shadow stripes).
- **Zoom 0.36 → 2.54 µm pixels**, 1920 × 1200 px frame (4876.7 × 3047.0 µm field of view).
- **Z-step 7.32 µm** (equal to the sheet thickness).

![rg5x with both channels (488 + 561, Cam1 + Cam2)](images/acquisition/19_rg5x_two_channel.jpg)

---

## 9. Fit the z-stack and tiles to your sample

### 9.1 Live view and orientation

1. Click **Continuous** to see a live image.
2. Leave **Bit Depth at 16 bit** (the `rg5x` default; Acquisition Mode panel).
3. **Rotate the sample until both hemispheres look the same** (equal size and brightness on both sides).
4. Laser power and exposure come from `rg5x` (488 at 70 %, 561 at ~5 %, 29.97 ms). Check a bright and a dim region in the
   live view: the histogram should not clip.

### 9.2 Check the light-sheet calibration values

Under the laser sliders in **Channels → Track1** there are **Left** and **Right** values. These are calibration values for the
light sheet's y and x axes, and there is a separate pair for the left and the right side.

- **Check them every time the system boots up.** The numbers themselves are arbitrary: they differ from session to session
  (e.g. −2.95 / 4.20 one day, 0.34 / 0.62 another), so do not copy them from `rg5x` or from this guide.

<!-- TODO(add): how to check / set these values (which tool or wizard, what "correct" looks like). -->

### 9.3 Z-stack

In **Multidimensional Acquisition → Z-Stack**, use the **First/Last** tab:

1. Under **Keep**, select **Interval** (not *Slice*), so the step stays fixed while you change the range.
2. Make sure **Interval = 7.320 µm**. ZEN shows an "Optimal" of 3.515 µm next to it: **ignore it**. Halving the step would
   double the planes and the file size.
3. Leave **Continuous Drive** ticked and **Light Sheet Z-Offset Correction** unticked.
4. Focus to one end of the sample → **Set First**. Focus to the other end → **Set Last**.
5. Check **Range** and **Slices** (Slices ≈ Range ÷ 7.32 µm; a whole brain is ~1,100 planes, ~8,000 µm).

### 9.4 Tiling

In **Multidimensional Acquisition → Tiling**:

1. Choose the **Bounding grid** tab.
2. Set **Overlap between 10 and 25 %** (10 % is the minimum; use more if the tissue edges are sparse or stitching has
   struggled before) and tick **Bi-directional** (the stage zig-zags across the grid instead of returning to the
   start of every row, which is faster).
3. Drive the stage to the outer edges of the sample (left, right, front, back) in the live view and press **Add** at each one.
   The marked points appear in the minimap and in **Marked positions**. **Remove** / **Remove all** clear them.
4. ZEN fits the smallest grid of tiles that contains every marked point and shows the result. Check it covers the whole
   brain with some margin. Typical whole brains are **3 × 6 to 3 × 8 tiles**.
5. Optional: **Save…** the marked positions so you can **Load…** them for the next sample.

![Z-stack (First/Last, Keep: Interval, 7.320 µm) and Tiling (Bounding grid, 3 × 8 tiles, 10 % overlap)](images/acquisition/18_zstack_tiling_panel.jpg)

**Sanity check of the numbers ZEN shows.** Each tile is 1920 × 1200 px at 2.54 µm. Each shared edge loses overlap × tile size,
e.g. at 10 %: 192 px (x) / 120 px (y). So for 3 × 8 tiles at 10 % (the screenshot above):

| | Tiles | Pixels | Size |
|---|---|---|---|
| Horizontal | 3 | 3 × 1920 − 2 × 192 = **5376** | 13,662 µm |
| Vertical | 8 | 8 × 1200 − 7 × 120 = **8760** | 22,262 µm |

If ZEN's pixel numbers don't match this, the overlap or frame size isn't what you think.

> [!NOTE]
> Keep **Bounding grid** tiling. The processing pipeline expects ZEN's bounding-grid tile numbering (0 … N−1).

<!-- TODO(confirm): how the edges are marked in practice (live view at which zoom? one point per corner?). -->

### 9.5 Pre-flight checklist

- [ ] `rg5x` loaded; 488 + 561 on, Cam1 + Cam2 ticked
- [ ] 16 bit
- [ ] Left / Right calibration values checked this session
- [ ] Z interval **7.320 µm**, Keep: Interval, First and Last set
- [ ] Bounding grid, 10–25 % overlap, bi-directional, grid covers the sample
- [ ] Size estimate fits on the portable hard drive

---

## 10. Acquire

1. Plug in your **portable hard drive** and set it as the destination. Check the size estimate next to
   **Start Experiment** fits on it.
2. Click **Start Experiment**.
3. When it finishes, move the data from the portable drive into **that mouse's folder** on lab storage.

<!-- TODO(confirm): file-naming convention, and whether "Streaming and Separate Files" should be on or off. -->

### Clean up

1. Remove the sample and the chamber in the **reverse order** you loaded them.
2. Rinse the chamber under **warm water for about a minute** and leave it to dry.
3. **Exit ZEN** and turn off the system.

---

## 11. Hand-off to the processing pipeline

The `.czi` goes straight into the pipeline: see [Lightsheet_Guide.md](../Lightsheet_Guide.md), step A (Inspect).
Things that matter downstream:

- **Tiling must be bounding grid** with tiles numbered 0 … N−1.
- **One time point** only.
- **Overlap of 10 to 25 %.** More overlap gives the registration more shared tissue to match (the pipeline's example data used
  ~20 %), at the cost of more tiles and a bigger file.
- The pipeline reads the **voxel size, sheet thickness, NA and emission wavelength** from the `.czi` metadata, so leave those
  settings as `rg5x` sets them.

---

## Quick reference: `rg5x` settings

| Panel | Setting | Value |
|---|---|---|
| Laser | Lasers on | **488 nm** (Laser 488-30) and **561 nm** (Laser 561-20) |
| Light Path | Tracks | 1 track (Track1), both lasers together |
| | Laser blocking filter | LBF 405/488/561/640 |
| | Beam splitter | SBS LP 560 |
| | Cam1 emission filter | BP 505–545 (488 channel) |
| | Cam2 emission filter | LP 585 (561 channel) |
| | Detection objective | EC Plan-Neofluar 5x/0.16 foc |
| Acquisition Mode | Bit depth | 16 bit |
| | Zoom | 0.36 |
| | Light sheet | Single side, direction **Left** |
| | Thickness | 7.32 µm |
| | Pivot scan | On |
| | Online dual side fusion | Off |
| Acquisition Area | Frame | 1920 × 1200 px, 4876.7 × 3047.0 µm |
| | Pixel size | 2.54 µm |
| Channels | 488 power | 70.0 % |
| | 561 power | ~5 % |
| | Exposure (Cam1, Cam2) | 29.97 ms |
| | Camera temperature | 5.0 °C |
| Z-Stack | Interval | **7.320 µm** |
| Specimen Navigator | Specimen diameter | 1200 µm |
| | Refractive index | 1.520 |
| Z-Stack | Keep | Interval |
| Tiling | Mode | Bounding grid, bi-directional |
| | Overlap | 10–25 % |

Not in the table on purpose: the **Left / Right** calibration values (check them every session, [9.2](#92-check-the-light-sheet-calibration-values)),
and the z-range and tile grid (set for each sample).

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| "Attenuation is incorrect" pop-up at start | Expected after changing the database: click **Yes**. |
| Wrong objective shown in ZEN | Maintain → Objectives → objective icon → pick the installed one ([step 5](#5-verify-the-hardware-maintain-tab)). |
| Precipitate / haze in the chamber | Warm the chamber and matching solution slightly before use. |
| One hemisphere brighter or larger | Adjust the rotation until both sides match ([step 9](#9-fit-the-z-stack-and-tiles-to-your-sample)). |
| Stitching fails later in the pipeline | Check tiling was **bounding grid** and overlap was 10 to 25 %. |
