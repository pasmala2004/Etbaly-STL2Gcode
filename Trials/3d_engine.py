#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import os
import sys
import subprocess
import json
import tempfile
import math

# Set UTF-8 encoding for Python on Windows
os.environ['PYTHONIOENCODING'] = 'utf-8'

# Force UTF-8 output on Windows
if sys.platform.startswith('win'):
    # Reconfigure stdout/stderr to handle UTF-8
    import io
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')

# تأكد من تثبيت مكتبة trimesh: pip install trimesh
try:
    import trimesh
except ImportError:
    print("Installing trimesh...")
    subprocess.check_call([sys.executable, "-m", "pip", "install", "trimesh"])
    import trimesh

# ==========================================
# 1. كود بلندر (سيتم كتابته في ملف مؤقت أثناء التشغيل)
# ==========================================
BLENDER_REPAIR_CODE = """
import bpy
import bmesh
import sys
import json

def repair(input_p, output_p):
    bpy.ops.wm.read_factory_settings(use_empty=True)

    # FIX 1: Deselect all before import so we can reliably find the new object
    bpy.ops.object.select_all(action='DESELECT')

    if input_p.lower().endswith(".stl"):
        bpy.ops.wm.stl_import(filepath=input_p)
    else:
        bpy.ops.wm.obj_import(filepath=input_p)

    # FIX 2: Don't rely on active_object — grab the selected object after import
    imported = [o for o in bpy.context.selected_objects if o.type == 'MESH']
    if not imported:
        raise RuntimeError("No mesh object found after import")
    obj = imported[0]
    bpy.context.view_layer.objects.active = obj

    # FIX 3: Must be in OBJECT mode before applying transforms/modifiers
    bpy.ops.object.mode_set(mode='OBJECT')
    bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)

    dims = obj.dimensions
    # FIX 4: voxel_size was too small (0.05% of max dim) — caused memory explosion
    # Use 0.5% of max dim for a good quality/performance balance, floor at 0.1
    voxel_res = max(0.1, max(dims) * 0.005)

    remesh = obj.modifiers.new(name="Fix", type='REMESH')
    remesh.mode = 'VOXEL'
    remesh.voxel_size = voxel_res
    remesh.adaptivity = 0.0
    bpy.ops.object.modifier_apply(modifier=remesh.name)

    # Light Decimation (preserve more details)
    decimate = obj.modifiers.new(name="Opt", type='DECIMATE')
    decimate.ratio = 0.7
    bpy.ops.object.modifier_apply(modifier=decimate.name)

    # FIX 5: Ensure object is still active/selected before export
    bpy.ops.object.select_all(action='DESELECT')
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj

    # FIX 6: Use export_mesh.stl (works in Blender 3.x AND 4.x/5.x)
    try:
        bpy.ops.wm.stl_export(filepath=output_p, export_selected_objects=True)
    except AttributeError:
        bpy.ops.export_mesh.stl(filepath=output_p, use_selection=True)

    return {"status": "success", "dims": [dims.x, dims.y, dims.z], "voxel_size": voxel_res}

if __name__ == "__main__":
    args = sys.argv[sys.argv.index("--") + 1:]
    try:
        res = repair(args[0], args[1])
        print(f"RESULT_START{json.dumps(res)}RESULT_END")
    except Exception as e:
        import traceback
        print(f"RESULT_START{json.dumps({'status': 'error', 'message': str(e), 'trace': traceback.format_exc()})}RESULT_END")
"""

# ==========================================
# 2. وظائف التحليل والإدارة (Main Pipeline)
# ==========================================
def analyze_mesh(path):
    """تحليل الموديل باستخدام Trimesh"""
    mesh = trimesh.load(path)
    if isinstance(mesh, trimesh.Scene):
        mesh = mesh.dump(concatenate=True)
        
    return {
        "is_watertight": mesh.is_watertight,
        "is_manifold": mesh.is_winding_consistent,
        "area": float(mesh.area),
        "faces": len(mesh.faces),
        "printable": mesh.is_watertight
    }

def find_blender_exe():
    """Find Blender executable on Windows"""
    blender_paths = [
        r"C:\Program Files\Blender Foundation\Blender 5.1\blender.exe",
        r"C:\Program Files\Blender Foundation\Blender\blender.exe",
    ]
    for path in blender_paths:
        if os.path.isfile(path):
            return path
    
    # Try environment variable
    env_blender = os.environ.get("BLENDER_PATH")
    if env_blender and os.path.isfile(env_blender):
        return env_blender
    
    # Try system PATH
    import shutil
    blender = shutil.which("blender")
    if blender:
        return blender
    
    raise FileNotFoundError("Blender not found. Install Blender or set BLENDER_PATH environment variable.")

def run_blender_repair(input_path, output_path):
    """إنشاء سكربت بلندر مؤقت وتشغيله"""
    with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False, encoding='utf-8') as tf:
        tf.write(BLENDER_REPAIR_CODE)
        temp_script = tf.name

    try:
        blender_exe = find_blender_exe()
        cmd = [
            blender_exe, "-b", "-P", temp_script, "--",
            input_path, output_path
        ]
        # FIX: Don't use check=True — Blender sometimes exits non-zero even on success.
        # Instead, check the output ourselves for RESULT_START.
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)

        # Combine stdout+stderr so we never miss the RESULT token
        full_output = result.stdout + result.stderr

        if "RESULT_START" in full_output:
            data = full_output.split("RESULT_START")[1].split("RESULT_END")[0]
            return json.loads(data)
        else:
            # Print raw output to help with debugging
            print("[DEBUG] Blender stdout:", result.stdout[-2000:])
            print("[DEBUG] Blender stderr:", result.stderr[-2000:])
            return {"status": "error", "message": "Blender did not return expected output", "output": result.stdout[-500:]}
    except FileNotFoundError as e:
        print(f"[ERROR] {e}")
        return {"status": "error", "message": str(e)}
    except subprocess.TimeoutExpired:
        return {"status": "error", "message": "Blender timed out after 300 seconds — model may be too complex"}
    except Exception as e:
        return {"status": "error", "message": str(e)}
    finally:
        # Always clean up temp script regardless of success/failure
        try:
            os.remove(temp_script)
        except OSError:
            pass


def _rotation_matrix_xyz(rx_deg, ry_deg, rz_deg):
    """Create rotation matrix from Euler angles (degrees)"""
    rx = math.radians(rx_deg)
    ry = math.radians(ry_deg)
    rz = math.radians(rz_deg)
    mx = trimesh.transformations.rotation_matrix(rx, [1, 0, 0])
    my = trimesh.transformations.rotation_matrix(ry, [0, 1, 0])
    mz = trimesh.transformations.rotation_matrix(rz, [0, 0, 1])
    return mz @ my @ mx


def support_risk_score(mesh, overhang_angle_deg=45.0):
    """Calculate support material needed. Lower score = less support required."""
    if len(mesh.faces) == 0:
        return float("inf")

    face_normals = mesh.face_normals
    face_areas = mesh.area_faces
    face_centroids = mesh.triangles_center

    z_min = float(mesh.bounds[0][2])
    z_max = float(mesh.bounds[1][2])
    height = max(1e-6, z_max - z_min)

    # Detect overhanging faces
    threshold = -math.sin(math.radians(overhang_angle_deg))
    downward_overhang = face_normals[:, 2] < threshold
    not_on_bed = face_centroids[:, 2] > (z_min + 0.15)
    support_faces = downward_overhang & not_on_bed
    support_area = float(face_areas[support_faces].sum())

    # Detect base contact area
    near_bed = face_centroids[:, 2] <= (z_min + 0.05)
    downward_flat = face_normals[:, 2] < -0.95
    base_faces = near_bed & downward_flat
    base_area = float(face_areas[base_faces].sum())

    # Calculate risk score
    if base_area < 1.0:
        return support_area + (0.15 * height) + 500.0
    
    return support_area + (0.15 * height) - (0.25 * base_area)


def optimize_orientation(mesh):
    """Find optimal rotation to minimize support material and maximize bed contact."""
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

    # Test all candidates and find best
    for angles in candidates:
        candidate = mesh.copy()
        candidate.apply_transform(_rotation_matrix_xyz(*angles))
        score = support_risk_score(candidate)
        if score < best_score:
            best_mesh = candidate
            best_angles = angles
            best_score = score

    # Center in XY plane
    center_xy = best_mesh.bounding_box.centroid[:2]
    best_mesh.apply_translation([-float(center_xy[0]), -float(center_xy[1]), 0.0])

    # Place on bed with slight sink for first-layer contact
    z_min = float(best_mesh.bounds[0][2])
    bed_sink_mm = 0.02
    best_mesh.apply_translation([0.0, 0.0, -z_min - bed_sink_mm])

    return best_mesh, best_angles, best_score


def main(input_file):
    if not os.path.exists(input_file):
        print(f"[FAIL] File {input_file} not found!")
        return

    print(f"[INFO] Analyzing: {input_file}...")
    before = analyze_mesh(input_file)

    if before["printable"]:
        print("[OK] Model is already printable. Optimizing orientation...")
        mesh = trimesh.load(input_file)
        if isinstance(mesh, trimesh.Scene):
            mesh = mesh.dump(concatenate=True)
        
        # Apply orientation optimization even if already printable
        oriented, angles, score = optimize_orientation(mesh)
        final_file = os.path.splitext(input_file)[0] + "_oriented.stl"
        oriented.export(final_file)
        rx, ry, rz = angles
        print(f"[OK] Best rotation: X={rx}, Y={ry}, Z={rz}")
        print(f"[OK] Support score: {score:.2f}")
        print(f"[OK] Final output: {final_file}")
        return {"status": "ready", "file": final_file, "rotation": angles, "support_score": score}

    print("[INFO] Repairing with Blender (High Fidelity Mode)...")
    fixed_file = os.path.splitext(input_file)[0] + "_fixed.stl"
    
    blender_res = run_blender_repair(input_file, fixed_file)
    
    if blender_res.get("status") == "success":
        print("[INFO] Optimizing orientation for minimal support...")
        mesh = trimesh.load(fixed_file)
        if isinstance(mesh, trimesh.Scene):
            mesh = mesh.dump(concatenate=True)
        
        # Apply orientation optimization
        oriented, angles, score = optimize_orientation(mesh)
        final_file = os.path.splitext(input_file)[0] + "_repaired_oriented.stl"
        oriented.export(final_file)
        
        after = analyze_mesh(final_file)
        loss = abs(after["area"] - before["area"]) / before["area"] * 100
        
        rx, ry, rz = angles
        report = {
            "status": "repaired",
            "file": final_file,
            "quality_loss_percent": round(loss, 2),
            "is_printable": after["printable"],
            "rotation": {"x": rx, "y": ry, "z": rz},
            "support_score": round(score, 2),
            "stats": after
        }
        print("[OK] Repair & Orientation Complete!")
        print(f"[OK] Best rotation: X={rx}, Y={ry}, Z={rz}")
        print(f"[OK] Support score: {score:.2f}")
        print(json.dumps(report, indent=4))
        
        # Cleanup intermediate file
        try:
            os.remove(fixed_file)
        except OSError:
            pass
        
        return report
    else:
        error_msg = blender_res.get("message", "Unknown error")
        print(f"[FAIL] Repair failed: {error_msg}")
        return blender_res

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python 3d_engine.py model.obj")
    else:
        main(sys.argv[1])