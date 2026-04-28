#!/usr/bin/env python3
"""
blender_repaire.py
------------------
Pipeline:
1) Repair mesh with Blender (voxel remesh + optional decimate)
2) Optimize orientation to reduce support demand
3) Center in XY and place on bed with slight Z sink for first-layer contact

Usage:
    python blender_repaire.py model.stl
    python blender_repaire.py model.obj --voxel-ratio 0.001 --decimate 0.7
"""

import argparse
import importlib
import importlib.util
import math
import os
import platform
import shutil
import subprocess
import sys
import tempfile


def ensure_dependencies():
    """Install required Python packages if missing."""
    missing = []
    for name in ("numpy", "trimesh", "scipy"):
        if importlib.util.find_spec(name) is None:
            missing.append(name)
    if not missing:
        return
    print(f"Installing missing packages ({', '.join(missing)}) ...")
    subprocess.check_call([sys.executable, "-m", "pip", "install", *missing])


def find_blender_executable(explicit_path=None):
    """
    Resolve Blender executable path.
    Priority:
    1) --blender-path argument
    2) BLENDER_PATH environment variable
    3) Common OS install locations
    4) PATH lookup
    """
    if explicit_path:
        candidate = os.path.abspath(explicit_path)
        if os.path.isfile(candidate):
            return candidate
        raise FileNotFoundError(f"Blender not found at --blender-path: {candidate}")

    env_path = os.environ.get("BLENDER_PATH")
    if env_path:
        candidate = os.path.abspath(env_path)
        if os.path.isfile(candidate):
            return candidate

    # Check same folder as this script first
    script_dir = os.path.dirname(os.path.abspath(__file__))
    local_candidates = [
        os.path.join(script_dir, "blender.exe"),
        os.path.join(script_dir, "blender"),
    ]
    for path in local_candidates:
        if os.path.isfile(path):
            return path

    # Check platform-specific locations
    candidates = []
    system = platform.system()
    if system == "Windows":
        local = os.environ.get("LOCALAPPDATA", "")
        program_files = os.environ.get("ProgramFiles", r"C:\Program Files")
        program_files_x86 = os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")
        candidates = [
            r"C:\Program Files\Blender Foundation\Blender 5.1\blender.exe",
            os.path.join(local, "Programs", "Blender Foundation", "Blender", "blender.exe"),
            os.path.join(program_files, "Blender Foundation", "Blender", "blender.exe"),
            os.path.join(program_files_x86, "Blender Foundation", "Blender", "blender.exe"),
        ]
    elif system == "Darwin":
        candidates = [
            "/Applications/Blender.app/Contents/MacOS/Blender",
        ]
    else:
        candidates = [
            "/usr/bin/blender",
            "/usr/local/bin/blender",
            os.path.expanduser("~/blender/blender"),
        ]

    for path in candidates:
        if path and os.path.isfile(path):
            return path

    which_path = shutil.which("blender")
    if which_path:
        return which_path

    raise FileNotFoundError(
        "Blender executable not found. Install Blender or pass --blender-path."
    )


BLENDER_REPAIR_CODE = r"""
import bpy
import json
import sys

def repair(input_path, output_path, voxel_ratio, decimate_ratio):
    bpy.ops.wm.read_factory_settings(use_empty=True)

    # Import supported mesh types
    if input_path.lower().endswith(".stl"):
        bpy.ops.wm.stl_import(filepath=input_path)
    elif input_path.lower().endswith(".obj"):
        bpy.ops.wm.obj_import(filepath=input_path)
    else:
        raise RuntimeError("Only STL/OBJ input is supported in this script")

    obj = bpy.context.active_object
    if obj is None:
        raise RuntimeError("No object imported")

    bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)

    dims = obj.dimensions
    max_dim = max(dims.x, dims.y, dims.z)
    voxel_size = max(0.02, max_dim * voxel_ratio)

    remesh = obj.modifiers.new(name="RepairRemesh", type='REMESH')
    remesh.mode = 'VOXEL'
    remesh.voxel_size = voxel_size
    remesh.adaptivity = 0.0
    bpy.ops.object.modifier_apply(modifier=remesh.name)

    if 0.0 < decimate_ratio < 1.0:
        dec = obj.modifiers.new(name="RepairDecimate", type='DECIMATE')
        dec.ratio = decimate_ratio
        bpy.ops.object.modifier_apply(modifier=dec.name)

    bpy.ops.wm.stl_export(filepath=output_path, export_selected_objects=True)
    return {"status": "success", "voxel_size": voxel_size}

if __name__ == "__main__":
    args = sys.argv[sys.argv.index("--") + 1:]
    in_path = args[0]
    out_path = args[1]
    voxel_ratio = float(args[2])
    decimate_ratio = float(args[3])
    result = repair(in_path, out_path, voxel_ratio, decimate_ratio)
    print("RESULT_START" + json.dumps(result) + "RESULT_END")
"""


def run_blender_repair(input_path, output_path, voxel_ratio, decimate_ratio, blender_exec):
    """Run Blender headless repair and return parsed result dict."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as tmp:
        tmp.write(BLENDER_REPAIR_CODE)
        temp_script = tmp.name

    try:
        cmd = [
            blender_exec,
            "-b",
            "-P",
            temp_script,
            "--",
            input_path,
            output_path,
            str(voxel_ratio),
            str(decimate_ratio),
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError(result.stderr or result.stdout or "Blender repair failed")

        out = result.stdout or ""
        if "RESULT_START" not in out or "RESULT_END" not in out:
            raise RuntimeError("Blender finished without structured result payload")

        payload = out.split("RESULT_START", 1)[1].split("RESULT_END", 1)[0]
        import json
        return json.loads(payload)
    finally:
        try:
            os.remove(temp_script)
        except OSError:
            pass


def _rotation_matrix_xyz(rx_deg, ry_deg, rz_deg):
    trimesh = importlib.import_module("trimesh")
    rx = math.radians(rx_deg)
    ry = math.radians(ry_deg)
    rz = math.radians(rz_deg)
    mx = trimesh.transformations.rotation_matrix(rx, [1, 0, 0])
    my = trimesh.transformations.rotation_matrix(ry, [0, 1, 0])
    mz = trimesh.transformations.rotation_matrix(rz, [0, 0, 1])
    return mz @ my @ mx


def support_risk_score(mesh, overhang_angle_deg=45.0):
    """Lower score generally means less support required."""
    if len(mesh.faces) == 0:
        return float("inf")

    face_normals = mesh.face_normals
    face_areas = mesh.area_faces
    face_centroids = mesh.triangles_center

    z_min = float(mesh.bounds[0][2])
    z_max = float(mesh.bounds[1][2])
    height = max(1e-6, z_max - z_min)

    threshold = -math.sin(math.radians(overhang_angle_deg))
    downward_overhang = face_normals[:, 2] < threshold
    not_on_bed = face_centroids[:, 2] > (z_min + 0.15)
    support_faces = downward_overhang & not_on_bed
    support_area = float(face_areas[support_faces].sum())

    near_bed = face_centroids[:, 2] <= (z_min + 0.05)
    downward_flat = face_normals[:, 2] < -0.95
    base_faces = near_bed & downward_flat
    base_area = float(face_areas[base_faces].sum())

    if base_area < 1.0:
        return support_area + (0.15 * height) + 500.0

    return support_area + (0.15 * height) - (0.25 * base_area)


def optimize_orientation(mesh):
    """Pick best coarse orientation and place mesh onto bed."""
    candidates = [
        (0, 0, 0),
        (90, 0, 0), (180, 0, 0), (270, 0, 0),
        (0, 90, 0), (0, 180, 0), (0, 270, 0),
        (0, 0, 90), (0, 0, 180), (0, 0, 270),
        (90, 90, 0), (90, 270, 0), (270, 90, 0), (270, 270, 0),
    ]

    best_mesh = mesh.copy()
    best_angles = (0, 0, 0)
    best_score = support_risk_score(best_mesh)

    for angles in candidates:
        candidate = mesh.copy()
        candidate.apply_transform(_rotation_matrix_xyz(*angles))
        score = support_risk_score(candidate)
        if score < best_score:
            best_mesh = candidate
            best_angles = angles
            best_score = score

    center_xy = best_mesh.bounding_box.centroid[:2]
    best_mesh.apply_translation([-float(center_xy[0]), -float(center_xy[1]), 0.0])

    z_min = float(best_mesh.bounds[0][2])
    bed_sink_mm = 0.02
    best_mesh.apply_translation([0.0, 0.0, -z_min - bed_sink_mm])
    return best_mesh, best_angles, best_score


def main():
    parser = argparse.ArgumentParser(
        description="Repair with Blender, then orient for low-support printing."
    )
    parser.add_argument("input_model", help="Input STL/OBJ model path")
    parser.add_argument(
        "--blender-path",
        default=None,
        help="Path to blender executable (optional)",
    )
    parser.add_argument(
        "--voxel-ratio",
        type=float,
        default=0.001,
        help="Voxel size ratio of max dimension for Blender remesh (default: 0.001)",
    )
    parser.add_argument(
        "--decimate",
        type=float,
        default=0.6,
        help="Decimate ratio after remesh; 0<r<1, or >=1 to skip (default: 0.6)",
    )
    args = parser.parse_args()

    ensure_dependencies()
    trimesh = importlib.import_module("trimesh")

    input_path = os.path.abspath(args.input_model)
    blender_exec = find_blender_executable(args.blender_path)
    print(f"[OK] Blender executable: {blender_exec}")
    if not os.path.isfile(input_path):
        print(f"[FAIL] Input file not found: {input_path}")
        sys.exit(1)

    base, _ = os.path.splitext(input_path)
    repaired_path = base + "_blender_fixed.stl"
    final_path = base + "_blender_repaired_oriented.stl"

    print(f"Repairing with Blender: {input_path}")
    try:
        blender_result = run_blender_repair(
            input_path,
            repaired_path,
            voxel_ratio=args.voxel_ratio,
            decimate_ratio=args.decimate,
            blender_exec=blender_exec,
        )
        print(f"[OK] Blender repair done: {repaired_path}")
        if "voxel_size" in blender_result:
            print(f"[OK] Used voxel size: {blender_result['voxel_size']:.5f}")
    except Exception as e:
        print(f"[FAIL] Blender repair failed: {e}")
        print("Make sure Blender is installed and available as 'blender' in PATH.")
        sys.exit(1)

    try:
        mesh = trimesh.load(repaired_path)
        if hasattr(mesh, "dump"):
            # Handle loaded Scene by concatenating meshes.
            try:
                mesh = mesh.dump(concatenate=True)
            except Exception:
                pass

        oriented, angles, score = optimize_orientation(mesh)
        oriented.export(final_path)
        rx, ry, rz = angles
        print(f"[OK] Best rotation: X={rx}, Y={ry}, Z={rz}")
        print(f"[OK] Support score: {score:.2f}")
        print(f"[OK] Final output: {final_path}")
        
        # Clean up intermediate file
        try:
            os.remove(repaired_path)
            print(f"[OK] Cleaned up intermediate file: {repaired_path}")
        except OSError:
            pass
    except Exception as e:
        print(f"[FAIL] Orientation/export failed: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
