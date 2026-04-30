    #!/usr/bin/env python3
"""
Rotate a mesh to reduce support requirement using a support-risk score.

Usage:
    python rotation_support_fix.py input.stl
    python rotation_support_fix.py input.obj --output fixed.stl --overhang-angle 45
"""

import argparse
import math
import os
import sys

import trimesh


def rotation_matrix_xyz(rx_deg, ry_deg, rz_deg):
    rx = math.radians(rx_deg)
    ry = math.radians(ry_deg)
    rz = math.radians(rz_deg)
    mx = trimesh.transformations.rotation_matrix(rx, [1, 0, 0])
    my = trimesh.transformations.rotation_matrix(ry, [0, 1, 0])
    mz = trimesh.transformations.rotation_matrix(rz, [0, 0, 1])
    return mz @ my @ mx


def support_risk_score(mesh, overhang_angle_deg=45.0):
    """Lower score means less expected support."""
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


def optimize_orientation(mesh, overhang_angle_deg=45.0):
    candidates = [
        (0, 0, 0),
        (90, 0, 0), (180, 0, 0), (270, 0, 0),
        (0, 90, 0), (0, 180, 0), (0, 270, 0),
        (0, 0, 90), (0, 0, 180), (0, 0, 270),
        (90, 90, 0), (90, 270, 0), (270, 90, 0), (270, 270, 0),
    ]

    best_mesh = mesh.copy()
    best_angles = (0, 0, 0)
    best_score = support_risk_score(best_mesh, overhang_angle_deg)

    for angles in candidates:
        candidate = mesh.copy()
        candidate.apply_transform(rotation_matrix_xyz(*angles))
        score = support_risk_score(candidate, overhang_angle_deg)
        if score < best_score:
            best_mesh = candidate
            best_angles = angles
            best_score = score

    center_xy = best_mesh.bounding_box.centroid[:2]
    best_mesh.apply_translation([-float(center_xy[0]), -float(center_xy[1]), 0.0])

    z_min = float(best_mesh.bounds[0][2])
    best_mesh.apply_translation([0.0, 0.0, -z_min - 0.02])
    return best_mesh, best_angles, best_score


def place_on_bed(mesh, bed_sink_mm=0.02):
    """Center in XY and place mesh on bed in Z."""
    placed = mesh.copy()
    center_xy = placed.bounding_box.centroid[:2]
    placed.apply_translation([-float(center_xy[0]), -float(center_xy[1]), 0.0])
    z_min = float(placed.bounds[0][2])
    placed.apply_translation([0.0, 0.0, -z_min - bed_sink_mm])
    return placed


def fit_mesh_to_print_volume(mesh, bed_x, bed_y, bed_z, allow_upscale=False):
    """
    Scale mesh to fit printer volume.
    Returns (fitted_mesh, scale_used, was_scaled).
    """
    if bed_x <= 0 or bed_y <= 0 or bed_z <= 0:
        raise ValueError("Print volume dimensions must be > 0.")

    fitted = mesh.copy()
    extents = fitted.extents
    sx = float(bed_x) / max(float(extents[0]), 1e-9)
    sy = float(bed_y) / max(float(extents[1]), 1e-9)
    sz = float(bed_z) / max(float(extents[2]), 1e-9)
    scale_to_fit = min(sx, sy, sz)

    if not allow_upscale:
        scale_to_fit = min(scale_to_fit, 1.0)

    if scale_to_fit <= 0:
        raise ValueError("Computed invalid scale for print volume fit.")

    was_scaled = abs(scale_to_fit - 1.0) > 1e-12
    if was_scaled:
        fitted.apply_scale(scale_to_fit)

    fitted = place_on_bed(fitted)
    return fitted, scale_to_fit, was_scaled


def load_mesh(path):
    mesh = trimesh.load(path)
    if isinstance(mesh, trimesh.Scene):
        mesh = mesh.dump(concatenate=True)
    return mesh


def resize_mesh(mesh, scale_factor=1.0, target_max_dim=None):
    """
    Resize mesh before orientation.
    - scale_factor: direct multiplier (default 1.0, no change)
    - target_max_dim: if provided, scales mesh so max dimension matches this value
    """
    resized = mesh.copy()

    if target_max_dim is not None:
        extents = resized.extents
        current_max = float(max(extents))
        if current_max <= 0:
            raise ValueError("Mesh has invalid dimensions for target sizing.")
        scale_factor = float(target_max_dim) / current_max

    if scale_factor <= 0:
        raise ValueError("Scale factor must be greater than 0.")

    if abs(scale_factor - 1.0) > 1e-12:
        resized.apply_scale(float(scale_factor))

    return resized


def evaluate_printability(mesh, support_score, max_support_score=400.0):
    """
    Simple printability check:
    - must have faces
    - should be watertight
    - support score should be under threshold
    """
    reasons = []
    printable = True

    if len(mesh.faces) == 0:
        printable = False
        reasons.append("mesh has no faces")

    if not mesh.is_watertight:
        printable = False
        reasons.append("mesh is not watertight")

    if not math.isfinite(support_score):
        printable = False
        reasons.append("support score is invalid")
    elif support_score > max_support_score:
        printable = False
        reasons.append(
            f"support score {support_score:.2f} is above threshold {max_support_score:.2f}"
        )

    if printable:
        reasons.append("mesh is watertight and support score is acceptable")

    return printable, reasons


def main():
    parser = argparse.ArgumentParser(description="Fix mesh rotation using support score.")
    parser.add_argument("input_model", help="Input mesh path (STL/OBJ/etc supported by trimesh)")
    parser.add_argument("--output", default=None, help="Output mesh path")
    parser.add_argument("--overhang-angle", type=float, default=45.0, help="Overhang angle in degrees")
    parser.add_argument(
        "--scale",
        type=float,
        default=1.0,
        help="Uniform scale factor (default: 1.0, no size change)",
    )
    parser.add_argument(
        "--target-max-dim",
        type=float,
        default=None,
        help="Target max dimension (same units as model). Overrides --scale if set.",
    )
    parser.add_argument(
        "--printable-threshold",
        type=float,
        default=400.0,
        help="Max support score considered printable (default: 400.0)",
    )
    parser.add_argument(
        "--auto-fit-bed",
        action="store_true",
        default=True,
        help="Automatically scale down model to fit printer volume (default: enabled).",
    )
    parser.add_argument(
        "--no-auto-fit-bed",
        dest="auto_fit_bed",
        action="store_false",
        help="Disable automatic bed fit scaling.",
    )
    parser.add_argument(
        "--bed-x",
        type=float,
        default=220.0,
        help="Printer X size in mm (default: 220)",
    )
    parser.add_argument(
        "--bed-y",
        type=float,
        default=220.0,
        help="Printer Y size in mm (default: 220)",
    )
    parser.add_argument(
        "--bed-z",
        type=float,
        default=250.0,
        help="Printer Z size in mm (default: 250)",
    )
    args = parser.parse_args()

    input_path = os.path.abspath(args.input_model)
    if not os.path.isfile(input_path):
        print(f"[FAIL] Input file not found: {input_path}")
        sys.exit(1)

    if args.output:
        output_path = os.path.abspath(args.output)
    else:
        base, _ = os.path.splitext(input_path)
        output_path = base + "_rotation_fixed.stl"

    mesh = load_mesh(input_path)
    mesh = resize_mesh(mesh, scale_factor=args.scale, target_max_dim=args.target_max_dim)
    oriented, angles, score = optimize_orientation(mesh, args.overhang_angle)
    fit_scale = 1.0
    fit_scaled = False
    if args.auto_fit_bed:
        oriented, fit_scale, fit_scaled = fit_mesh_to_print_volume(
            oriented, bed_x=args.bed_x, bed_y=args.bed_y, bed_z=args.bed_z
        )
    oriented.export(output_path)
    printable, reasons = evaluate_printability(
        oriented, score, max_support_score=args.printable_threshold
    )

    rx, ry, rz = angles
    print(f"[OK] Best rotation: X={rx}, Y={ry}, Z={rz}")
    print(f"[OK] Support score: {score:.2f}")
    if args.target_max_dim is not None:
        print(f"[OK] Resized to target max dimension: {args.target_max_dim}")
    elif args.scale != 1.0:
        print(f"[OK] Applied scale factor: {args.scale}")
    if args.auto_fit_bed:
        if fit_scaled:
            print(
                f"[OK] Auto-fit applied for bed {args.bed_x}x{args.bed_y}x{args.bed_z} mm "
                f"(scale={fit_scale:.4f})"
            )
        else:
            print(f"[OK] Model already fits bed {args.bed_x}x{args.bed_y}x{args.bed_z} mm")
    print(f"[OK] Printable: {'YES' if printable else 'NO'}")
    for reason in reasons:
        print(f"[INFO] {reason}")
    print(f"[OK] Output saved: {output_path}")
    
    if not printable:
        print("\n" + "="*70)
        print("[⚠️  ALERT] File is NOT printable and must go to the ADMIN DASHBOARD")
        print("="*70)


if __name__ == "__main__":
    main()
