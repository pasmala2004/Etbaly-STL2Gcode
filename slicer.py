#!/usr/bin/env python3
"""
slice_to_gcode.py
-----------------
Takes an STL file and exports G-code using PrusaSlicer's command-line interface.

Usage:
    python slice_to_gcode.py input.stl [output.gcode] [--config profile.ini]

Requirements:
    - PrusaSlicer installed on your system
    - Update PRUSA_SLICER_PATH below if needed
"""


import subprocess
import sys
import os
import argparse
import platform

# ── Adjust this path to your PrusaSlicer installation ──────────────────────
# Default profiles for AnkerMake M5
DEFAULT_PRINTER = "AnkerMake M5 (0.4 mm nozzle)"
DEFAULT_QUALITY_MAP = {
    "draft": "0.30 mm SUPERDRAFT (0.4 mm nozzle) @ANKER",
    "normal": "0.20 mm NORMAL (0.4 mm nozzle) @ANKER",
    "fine": "0.10 mm HIGHDETAIL (0.4 mm nozzle) @ANKER",
}
# ── Preset configurations ───────────────────────────────────────────────────
# Three categories: heavy (high quality/strength), normal, draft (fast/light)

PRESETS = {
    "heavy": {
        "quality": "0.10 mm HIGHDETAIL (0.4 mm nozzle) @ANKER",
        "infill": "40%",
        "support": "tree",
        "perimeters": "4",
    },
    "normal": {
        "quality": "0.20 mm NORMAL (0.4 mm nozzle) @ANKER",
        "infill": "20%",
        "support": "normal",
        "perimeters": "3",
    },
    "draft": {
        "quality": "0.30 mm SUPERDRAFT (0.4 mm nozzle) @ANKER",
        "infill": "10%",
        "support": "normal",
        "perimeters": "2",
    },
}

DEFAULT_MATERIAL_MAP = {
    "pla": "Generic PLA @ANKER",
    "abs": "Generic ABS @ANKER",
    "petg": "Generic PETG @ANKER",
    "pla+": "Generic PLA+ @ANKER",
}
def find_prusa_slicer() -> str:
    """Return the PrusaSlicer executable path based on the current OS."""
    system = platform.system()

    candidates = []

    if system == "Windows":
        candidates = [
            r"C:\Users\DELL\Downloads\PrusaSlicer-2.9.4\PrusaSlicer-2.9.4\prusa-slicer-console.exe",
            r"C:\Users\DELL\Downloads\PrusaSlicer-2.9.4\PrusaSlicer-2.9.4\prusa-slicer.exe",
        ]
    elif system == "Darwin":  # macOS
        candidates = [
            "/Applications/PrusaSlicer.app/Contents/MacOS/PrusaSlicer",
        ]
    else:  # Linux
        candidates = [
            "/usr/bin/prusa-slicer",
            "/usr/local/bin/prusa-slicer",
            os.path.expanduser("~/Applications/PrusaSlicer/prusa-slicer"),
        ]

    for path in candidates:
        if os.path.isfile(path):
            return path

    # Fall back to PATH lookup
    import shutil
    found = shutil.which("prusa-slicer") or shutil.which("PrusaSlicer")
    if found:
        return found

    raise FileNotFoundError(
        "PrusaSlicer executable not found. "
        "Please set the path manually in find_prusa_slicer()."
    )


def slice_stl(
    stl_path: str,
    output_path: str | None = None,
    config_path: str | None = None,
    extra_args: list[str] | None = None,
    printer_profile: str | None = None,
    print_profile: str | None = None,
    material_profile: str | None = None,
    scale: float | None = None,
) -> str:
    """
    Slice an STL file with PrusaSlicer and return the path to the G-code file.

    Parameters
    ----------
    stl_path    : Path to the input STL file.
    output_path : Desired output .gcode path. If None, saved next to the STL.
    config_path : Optional PrusaSlicer .ini config/profile file.
    extra_args  : Any additional CLI flags (e.g. ["--layer-height", "0.2"]).

    Returns
    -------
    str : Absolute path to the generated G-code file.
    """
    stl_path = os.path.abspath(stl_path)
    if not os.path.isfile(stl_path):
        raise FileNotFoundError(f"STL file not found: {stl_path}")

    def _ensure_gcode_extension(path: str) -> str:
        root, ext = os.path.splitext(path)
        if ext.lower() != ".gcode":
            return root + ".gcode" if ext else path + ".gcode"
        return path

    # Determine output path
    if output_path is None:
        base = os.path.splitext(stl_path)[0]
        output_path = base + ".gcode"
    else:
        output_path = _ensure_gcode_extension(output_path)
    output_path = os.path.abspath(output_path)

    slicer = find_prusa_slicer()

    # Build the base command
    base_cmd = [
        slicer,
        "--export-gcode",          # export G-code mode
        "--output", output_path,   # output file
    ]

    # Load printer/print/material profiles (required for proper slicing)
    if printer_profile:
        base_cmd += ["--printer-profile", printer_profile]
    if print_profile:
        base_cmd += ["--print-profile", print_profile]
    if material_profile:
        base_cmd += ["--material-profile", material_profile]

    if config_path:
        config_path = os.path.abspath(config_path)
        if not os.path.isfile(config_path):
            raise FileNotFoundError(f"Config file not found: {config_path}")
        base_cmd += ["--load", config_path]

    if extra_args:
        base_cmd += extra_args

    # Scale the model (useful for tiny STL files)
    if scale:
        base_cmd += ["--scale", str(scale)]

    def _run_with(additional_args: list[str] | None = None):
        cmd = list(base_cmd)
        if additional_args:
            cmd += additional_args
        cmd.append(stl_path)  # input file must come last
        print(f"Running: {' '.join(cmd)}\n")
        return subprocess.run(cmd, capture_output=True, text=True)

    result = _run_with()

    if result.returncode != 0 and "no extrusions in the first layer" in (result.stderr or "").lower():
        print(
            "[WARN] First layer has no extrusions. Retrying with safer bed-contact options...",
            file=sys.stderr,
        )

        # Retry 1: force bed placement + brim to create first-layer material.
        retry_args = ["--ensure-on-bed", "--brim-width", "8"]
        retry = _run_with(retry_args)
        if retry.returncode == 0:
            result = retry
        elif "unknown option" not in (retry.stderr or "").lower():
            # Retry 2: add raft as a fallback for point-contact geometries.
            retry2_args = retry_args + ["--raft-layers", "1"]
            retry2 = _run_with(retry2_args)
            if retry2.returncode == 0:
                result = retry2

    if result.stdout:
        print(result.stdout)
    if result.stderr:
        print(result.stderr, file=sys.stderr)

    if result.returncode != 0:
        raise RuntimeError(
            f"PrusaSlicer exited with code {result.returncode}.\n"
            f"stderr: {result.stderr}"
        )

    if not os.path.isfile(output_path):
        raise RuntimeError(
            f"PrusaSlicer finished but G-code file not found at: {output_path}"
        )

    print(f"\n✅  G-code written to: {output_path}")
    return output_path


# ── CLI entry point ─────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(
        description="Slice an STL file with PrusaSlicer and export G-code."
    )
    parser.add_argument("stl", help="Path to the input STL file")
    parser.add_argument(
        "output", nargs="?", default=None,
        help="Output G-code path (default: same directory as STL)"
    )
    parser.add_argument(
        "--config", default=None,
        help="PrusaSlicer config/profile .ini file"
    )
    # Preset: combines quality, infill, support, perimeters
    parser.add_argument(
        "--preset", choices=["heavy", "normal", "draft"], default="normal",
        help="Preset: heavy (0.1mm, 40%% infill, tree support), normal (0.2mm, 20%%), draft (0.3mm, 10%%)"
    )
    # Material settings
    parser.add_argument(
        "--material", choices=["pla", "abs", "petg", "pla+"], default="pla",
        help="Material type: pla, abs, petg, pla+"
    )
    # Individual overrides (optional, overrides preset values)
    parser.add_argument(
        "--quality-override", choices=["draft", "normal", "fine"], default=None,
        help="Override preset quality: draft (0.30mm), normal (0.20mm), fine (0.10mm)"
    )
    parser.add_argument(
        "--infill-override", choices=["light", "normal", "strong"], default=None,
        help="Override preset infill: light (10%%), normal (20%%), strong (40%%)"
    )
    parser.add_argument(
        "--support-override", choices=["none", "normal", "tree"], default=None,
        help="Override preset support: none, normal, tree"
    )
    # Scale factor (important for tiny models!)
    parser.add_argument(
        "--scale", type=float, default=50,
        help="Scale model by this factor (e.g., 100 for 100x放大)"
    )
    # Override specific profiles
    parser.add_argument(
        "--printer-profile", default=None,
        help="Printer profile name (default: AnkerMake M5)"
    )
    parser.add_argument(
        "--print-profile", default=None,
        help="Print profile name (overrides --quality)"
    )
    parser.add_argument(
        "--material-profile", default=None,
        help="Material profile name (overrides --material)"
    )
    # Legacy options
    parser.add_argument(
        "--layer-height", default=None,
        help="Layer height in mm (deprecated: use --quality)"
    )
    parser.add_argument(
        "--fill-density", default=None,
        help="Infill density, e.g. 15%%"
    )
    args = parser.parse_args()

    # Resolve printer profile
    printer_profile = args.printer_profile or DEFAULT_PRINTER

    # Resolve preset settings
    preset = PRESETS[args.preset]
    print_profile = preset["quality"]
    infill_density = preset["infill"]
    support_mode = preset["support"]
    perimeters = preset["perimeters"]

    # Apply individual overrides (if specified)
    quality_override_map = {
        "draft": "0.30 mm SUPERDRAFT (0.4 mm nozzle) @ANKER",
        "normal": "0.20 mm NORMAL (0.4 mm nozzle) @ANKER",
        "fine": "0.10 mm HIGHDETAIL (0.4 mm nozzle) @ANKER",
    }
    infill_override_map = {
        "light": "10%",
        "normal": "20%",
        "strong": "40%",
    }

    if args.quality_override:
        print_profile = quality_override_map[args.quality_override]
    if args.infill_override:
        infill_density = infill_override_map[args.infill_override]
    if args.support_override:
        support_mode = args.support_override

    # Resolve material profile
    if args.material_profile:
        material_profile = args.material_profile
    else:
        material_profile = DEFAULT_MATERIAL_MAP[args.material]

    # Build extra arguments
    extra = []
    if args.layer_height:
        extra += ["--layer-height", args.layer_height]
    if args.fill_density:
        extra += ["--fill-density", args.fill_density]
    else:
        extra += ["--fill-density", infill_density]

    # Perimeters from preset
    extra += ["--perimeters", perimeters]

    # Support settings
    if support_mode == "none":
        pass
    elif support_mode == "normal":
        extra += ["--support-material"]
    elif support_mode == "tree":
        # Older/newer PrusaSlicer CLIs differ; use generic support flags
        # so this mode remains compatible instead of failing on unknown options.
        extra += ["--support-material"]

    print(f"Printer: {printer_profile}")
    print(f"Preset: {args.preset.upper()}")
    print(f"  Quality: {print_profile}")
    print(f"  Infill: {infill_density}")
    print(f"  Support: {support_mode}")
    print(f"  Perimeters: {perimeters}")
    print(f"Material: {material_profile}")
    if args.scale:
        print(f"Scale: {args.scale}x")

    slice_stl(
        stl_path=args.stl,
        output_path=args.output,
        config_path=args.config,
        extra_args=extra or None,
        printer_profile=printer_profile,
        print_profile=print_profile,
        material_profile=material_profile,
        scale=args.scale,
    )


if __name__ == "__main__":
    main()