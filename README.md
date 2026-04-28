# STL to G-code Pipeline

A Python-based pipeline for preparing and slicing STL files into G-code ready for 3D printing. The pipeline handles mesh repair, orientation optimization, and G-code generation in two focused scripts.

---

## Overview

This project automates the steps between a raw STL model and a print-ready G-code file:

1. **`repair.py`** — Validates and prepares the mesh for printing.
2. **`slice.py`** — Slices the prepared STL into G-code using an external slicer.

---

## Features

### `repair.py`
- Checks mesh printability (watertight, non-manifold geometry, normals)
- Detects and flags overhang issues before slicing
- Optimizes model rotation to minimize support material
- Scales models to fit defined printer bed constraints
- Optional Blender-based repair for non-manifold or broken geometry

### `slice.py`
- Generates G-code from a validated STL file
- Supports three print profiles:
  | Mode | Quality | Speed | Supports |
  |------|---------|-------|----------|
  | `heavy` | High | Slow | Full |
  | `normal` | Balanced | Medium | Standard |
  | `demo` | Low | Fast | Standard |

---

## Requirements

- Python 3.8+
- An external slicer CLI — [CuraEngine](https://github.com/Ultimaker/CuraEngine) or [PrusaSlicer](https://github.com/prusa3d/PrusaSlicer)
- [`trimesh`](https://trimsh.org/) — `pip install trimesh`
- Blender *(optional)* — required only for automatic mesh repair

---

## Installation

```bash
git clone https://github.com/your-username/stl-to-gcode-pipeline.git
cd stl-to-gcode-pipeline
pip install trimesh
```

Set your slicer path if not on system `PATH`:
```bash
export SLICER_PATH="/path/to/CuraEngine"   # Linux/macOS
set SLICER_PATH=C:\path\to\CuraEngine.exe  # Windows
```

Optionally, set Blender path for repair support:
```bash
export BLENDER_PATH="/path/to/blender"
```

---

## Usage

### Step 1 — Repair & Orient

```bash
python repair.py model.stl
```

Outputs `model_repaired_oriented.stl` (or `model_oriented.stl` if already printable).

### Step 2 — Slice to G-code

```bash
python slice.py model_oriented.stl --mode normal
```

```bash
# Available modes:
python slice.py model.stl --mode heavy   # High quality, full supports
python slice.py model.stl --mode normal  # Balanced (default)
python slice.py model.stl --mode demo    # Fast, low detail, no supports
```

---

## Project Structure

```
stl-to-gcode-pipeline/
├── repair.py        # Mesh analysis, orientation optimization, optional repair
├── slice.py         # G-code generation via external slicer CLI
├── 3d_engine.py     # Shared core: mesh utilities, Blender bridge, scoring
└── README.md
```

---

## Notes

- Repair uses voxel remeshing via Blender, which alters mesh topology. Expect minor surface deviation on complex models.
- The `demo` mode is intended for fit/placement testing only — do not use for final prints.
- Orientation optimization tests 14 candidate rotations and selects the one with the lowest support score based on overhang analysis.
- Models with severe geometry issues (self-intersections, inverted shells) may require manual repair in a tool like [Meshmixer](https://www.meshmixer.com/) or [Netfabb](https://www.autodesk.com/products/netfabb).
