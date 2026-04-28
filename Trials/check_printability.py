# check_printability.py
# Run: python check_printability.py output.stl
# Installs numpy + trimesh via pip on first run if missing.

import importlib
import importlib.util
import math
import os
import subprocess
import sys


def ensure_dependencies():
    """Install required packages with pip if they are not available."""
    missing = []
    for name in ("numpy", "trimesh", "scipy"):
        if importlib.util.find_spec(name) is None:
            missing.append(name)
    if not missing:
        return
    print(f"Installing missing packages ({', '.join(missing)}) ...")
    try:
        subprocess.check_call(
            [sys.executable, "-m", "pip", "install", *missing],
            stdout=sys.stdout,
            stderr=sys.stderr,
        )
    except subprocess.CalledProcessError:
        print(
            "[FAIL] pip install failed. Run manually: python -m pip install numpy trimesh scipy",
            file=sys.stderr,
        )
        sys.exit(1)


def check_stl(stl_path):
    print(f"\n{'='*50}")
    print(f"Checking: {stl_path}")
    print(f"{'='*50}\n")

    results = {
        "passed": [],
        "warnings": [],
        "errors": []
    }

    try:
        trimesh = importlib.import_module("trimesh")
        mesh = trimesh.load(stl_path)

        # ── Check 1: File loaded ──────────────────────────
        if mesh is None or len(mesh.vertices) == 0:
            results["errors"].append("[FAIL] File could not be loaded or is empty")
            return results
        results["passed"].append("[OK] File loaded successfully")

        # ── Check 2: Watertight (most important) ──────────
        if mesh.is_watertight:
            results["passed"].append("[OK] Mesh is watertight (no holes)")
        else:
            results["errors"].append(
                "[FAIL] Mesh is NOT watertight - has holes or open edges. "
                "Will fail to print or cause slicer errors"
            )

        # ── Check 3: Face count ───────────────────────────
        face_count = len(mesh.faces)
        if face_count < 100:
            results["errors"].append(
                f"[FAIL] Too few faces ({face_count}) - mesh is too simple or broken"
            )
        elif face_count > 1_000_000:
            results["warnings"].append(
                f"[WARN] Very high face count ({face_count:,}) - "
                "may slow down slicer. Consider decimating."
            )
        else:
            results["passed"].append(f"[OK] Face count OK ({face_count:,} faces)")

        # ── Check 4: Dimensions ───────────────────────────
        bounds = mesh.bounds
        dimensions = bounds[1] - bounds[0]
        x, y, z = dimensions

        print(f"Dimensions: {x:.2f} x {y:.2f} x {z:.2f} mm\n")

        # Check if too small
        if any(d < 1.0 for d in [x, y, z]):
            results["errors"].append(
                f"[FAIL] Object is too small ({x:.2f}x{y:.2f}x{z:.2f}mm) - "
                "minimum printable size is ~1mm"
            )
        else:
            results["passed"].append(
                f"[OK] Size OK ({x:.2f} x {y:.2f} x {z:.2f} mm)"
            )

        # Check if too large for common printers
        # Standard FDM bed: 220x220x250mm
        # Standard Resin bed: 130x80x150mm
        if x > 220 or y > 220 or z > 250:
            results["warnings"].append(
                f"[WARN] Object exceeds standard FDM bed size (220x220x250mm) - "
                "check your printer's build volume"
            )
        if x > 130 or y > 80 or z > 150:
            results["warnings"].append(
                f"[WARN] Object exceeds standard Resin bed size (130x80x150mm)"
            )

        # ── Check 5: Normals ──────────────────────────────
        if hasattr(mesh, 'is_winding_consistent'):
            if mesh.is_winding_consistent:
                results["passed"].append("[OK] Face normals are consistent")
            else:
                results["errors"].append(
                    "[FAIL] Inconsistent face normals - "
                    "some faces are flipped. Needs repair."
                )

        # ── Check 6: Degenerate faces ─────────────────────
        if hasattr(mesh, 'is_valid'):
            if mesh.is_valid:
                results["passed"].append("[OK] No degenerate faces found")
            else:
                results["warnings"].append(
                    "[WARN] Mesh has degenerate faces (zero-area triangles) - "
                    "may cause slicer issues"
                )

        # ── Check 7: Volume ───────────────────────────────
        if mesh.is_watertight:
            volume_cm3 = mesh.volume / 1000
            if volume_cm3 <= 0:
                results["errors"].append(
                    "[FAIL] Negative or zero volume - mesh normals may be inverted"
                )
            else:
                results["passed"].append(
                    f"[OK] Volume OK ({volume_cm3:.2f} cm^3)"
                )

                # Estimate filament weight (PLA density = 1.24 g/cm^3)
                weight_g = volume_cm3 * 1.24 * 0.2  # 20% infill
                results["passed"].append(
                    f"[OK] Estimated weight at 20% infill: ~{weight_g:.1f}g (PLA)"
                )

        # ── Check 8: Self intersections ───────────────────
        try:
            intersecting = trimesh.repair.broken_faces(mesh)
            if len(intersecting) > 0:
                results["warnings"].append(
                    f"[WARN] {len(intersecting)} broken faces found - "
                    "may cause issues with some slicers"
                )
            else:
                results["passed"].append("[OK] No broken faces found")
        except Exception:
            pass

        # ── Check 9: Wall thickness (basic estimate) ──────
        try:
            if mesh.is_watertight:
                # Rough estimate using volume vs surface area
                volume = mesh.volume
                area = mesh.area
                thickness_estimate = (3 * volume) / area
                min_wall = 0.8  # mm minimum for FDM

                if thickness_estimate < min_wall:
                    results["warnings"].append(
                        f"[WARN] Estimated wall thickness ({thickness_estimate:.2f}mm) "
                        f"may be too thin for FDM printing (min {min_wall}mm)"
                    )
                else:
                    results["passed"].append(
                        f"[OK] Wall thickness estimate OK (~{thickness_estimate:.2f}mm)"
                    )
        except Exception:
            pass

        # ── Check 10: Manifold edges ──────────────────────
        try:
            unique_edges = mesh.edges_unique
            edge_count = len(unique_edges)
            if edge_count > 0:
                results["passed"].append(
                    f"[OK] Edge count OK ({edge_count:,} unique edges)"
                )
        except Exception:
            pass

    except Exception as e:
        results["errors"].append(f"[FAIL] Unexpected error: {str(e)}")

    return results


def try_repair_mesh(mesh):
    """
    Attempt to repair common STL issues.

    Returns
    -------
    tuple[trimesh.Trimesh, list[str], bool]
        repaired_mesh, repair_steps, repair_success
    """
    repair_steps = []
    repaired = mesh.copy()

    def _try_mesh_method(obj, names):
        """Call first available mesh method from names list."""
        for name in names:
            fn = getattr(obj, name, None)
            if callable(fn):
                try:
                    return True, fn()
                except Exception:
                    return False, None
        return False, None

    def _remove_background_components(input_mesh):
        """
        Remove disconnected background artifacts/noise by keeping dominant component(s).
        Returns cleaned mesh and a status message (or None).
        """
        try:
            parts = input_mesh.split(only_watertight=False)
        except Exception:
            return input_mesh, None

        if not parts or len(parts) == 1:
            return input_mesh, None

        # Prefer by area (works even for non-watertight parts), then keep parts
        # that are at least 8% of the largest area.
        areas = []
        for p in parts:
            try:
                areas.append(float(p.area))
            except Exception:
                areas.append(0.0)

        if not any(a > 0 for a in areas):
            return input_mesh, None

        max_area = max(areas)
        keep_indices = [i for i, a in enumerate(areas) if a >= (0.08 * max_area)]
        removed_count = len(parts) - len(keep_indices)
        if removed_count <= 0:
            return input_mesh, None

        trimesh = importlib.import_module("trimesh")
        kept_meshes = [parts[i] for i in keep_indices]
        cleaned = trimesh.util.concatenate(kept_meshes)
        return cleaned, f"Removed {removed_count} disconnected background/noise component(s)"

    def _remove_thin_backplate(input_mesh):
        """
        Heuristic: remove a thin planar "background plate" attached to the model.
        We detect dominant near-vertical face layer around a narrow X or Y band.
        """
        if len(input_mesh.faces) == 0:
            return input_mesh, None

        normals = input_mesh.face_normals
        centroids = input_mesh.triangles_center
        areas = input_mesh.area_faces

        # Identify near-vertical faces (typical for a photo/background slab).
        vertical = abs(normals[:, 2]) < 0.2
        if not vertical.any():
            return input_mesh, None

        x_vals = centroids[:, 0]
        y_vals = centroids[:, 1]

        x_span = max(1e-6, float(x_vals.max() - x_vals.min()))
        y_span = max(1e-6, float(y_vals.max() - y_vals.min()))

        # Probe thin bands near bounds where backplates usually sit.
        x_band = max(0.05, 0.03 * x_span)
        y_band = max(0.05, 0.03 * y_span)

        near_x_min = vertical & (x_vals < (x_vals.min() + x_band))
        near_x_max = vertical & (x_vals > (x_vals.max() - x_band))
        near_y_min = vertical & (y_vals < (y_vals.min() + y_band))
        near_y_max = vertical & (y_vals > (y_vals.max() - y_band))

        candidates = [near_x_min, near_x_max, near_y_min, near_y_max]
        candidate_areas = [float(areas[m].sum()) if m.any() else 0.0 for m in candidates]
        best_idx = max(range(len(candidates)), key=lambda i: candidate_areas[i])
        best_mask = candidates[best_idx]
        best_area = candidate_areas[best_idx]

        total_area = float(areas.sum())
        if total_area <= 0:
            return input_mesh, None

        # Plate must be meaningful but not most of the model surface.
        ratio = best_area / total_area
        if ratio < 0.06 or ratio > 0.55:
            return input_mesh, None

        # Remove faces in the detected plate band, keep the rest.
        keep_faces = ~best_mask
        if keep_faces.sum() < 100:
            return input_mesh, None

        cleaned = input_mesh.copy()
        try:
            cleaned.update_faces(keep_faces)
            cleaned.remove_unreferenced_vertices()
            return cleaned, "Removed likely thin connected backplate/background shell"
        except Exception:
            return input_mesh, None

    # Remove obviously problematic geometry first.
    before_faces = len(repaired.faces)
    ok_deg_remove, _ = _try_mesh_method(repaired, ["remove_degenerate_faces"])
    if not ok_deg_remove and hasattr(repaired, "nondegenerate_faces"):
        try:
            mask = repaired.nondegenerate_faces()
            repaired.update_faces(mask)
        except Exception:
            pass

    ok_dup_remove, _ = _try_mesh_method(repaired, ["remove_duplicate_faces"])
    if not ok_dup_remove and hasattr(repaired, "unique_faces"):
        try:
            mask = repaired.unique_faces()
            repaired.update_faces(mask)
        except Exception:
            pass
    _try_mesh_method(repaired, ["remove_unreferenced_vertices"])
    after_faces = len(repaired.faces)
    if after_faces != before_faces:
        repair_steps.append(
            f"Removed problematic faces/vertices ({before_faces - after_faces} face difference)"
        )

    # Remove disconnected background artifacts/noise components.
    repaired, split_msg = _remove_background_components(repaired)
    if split_msg:
        repair_steps.append(split_msg)

    # If background is connected to main body, try thin backplate heuristic.
    repaired, plate_msg = _remove_thin_backplate(repaired)
    if plate_msg:
        repair_steps.append(plate_msg)

    # Merge tiny vertex gaps to close small cracks.
    _try_mesh_method(repaired, ["merge_vertices"])

    # Fix normal directions and winding consistency.
    ok_normals, _ = _try_mesh_method(repaired, ["fix_normals"])
    if ok_normals:
        repair_steps.append("Recomputed and fixed normals")

    # Try to close open boundaries.
    _, holes_filled = _try_mesh_method(repaired, ["fill_holes"])
    if holes_filled:
        repair_steps.append(f"Filled holes where possible ({holes_filled} patch operations)")

    # Trimesh repair helper may fix additional small defects.
    trimesh = importlib.import_module("trimesh")
    try:
        trimesh.repair.fix_inversion(repaired)
        trimesh.repair.fix_winding(repaired)
        repair_steps.append("Applied winding/inversion repair")
    except Exception:
        pass

    # Final cleanup after topology edits.
    _try_mesh_method(repaired, ["remove_unreferenced_vertices"])
    # New and old trimesh versions accept different process() signatures.
    process_fn = getattr(repaired, "process", None)
    if callable(process_fn):
        try:
            process_fn(validate=True)
        except ModuleNotFoundError as e:
            repair_steps.append(f"Skipped full mesh processing (missing dependency: {e.name})")
        except TypeError:
            try:
                process_fn()
            except ModuleNotFoundError as e:
                repair_steps.append(f"Skipped full mesh processing (missing dependency: {e.name})")

    repair_success = bool(
        len(repaired.vertices) > 0
        and len(repaired.faces) > 0
        and repaired.is_watertight
        and repaired.is_winding_consistent
    )

    return repaired, repair_steps, repair_success


def _rotation_matrix_xyz(rx_deg, ry_deg, rz_deg):
    """Build a 4x4 transform matrix from XYZ Euler angles (degrees)."""
    trimesh = importlib.import_module("trimesh")
    rx = math.radians(rx_deg)
    ry = math.radians(ry_deg)
    rz = math.radians(rz_deg)
    mx = trimesh.transformations.rotation_matrix(rx, [1, 0, 0])
    my = trimesh.transformations.rotation_matrix(ry, [0, 1, 0])
    mz = trimesh.transformations.rotation_matrix(rz, [0, 0, 1])
    return mz @ my @ mx


def _support_risk_score(mesh, overhang_angle_deg=45.0):
    """
    Estimate support demand after orientation.
    Lower score means easier printing with fewer supports.
    """
    if len(mesh.faces) == 0:
        return float("inf")

    face_normals = mesh.face_normals
    face_areas = mesh.area_faces
    face_centroids = mesh.triangles_center

    z_min = float(mesh.bounds[0][2])
    z_max = float(mesh.bounds[1][2])
    height = max(1e-6, z_max - z_min)

    # Faces that slope downward more than threshold are support-prone.
    threshold = -math.sin(math.radians(overhang_angle_deg))
    downward_overhang = face_normals[:, 2] < threshold
    not_on_bed = face_centroids[:, 2] > (z_min + 0.15)
    support_faces = downward_overhang & not_on_bed
    support_area = float(face_areas[support_faces].sum())

    # Approximate bed contact area by almost-flat bottom faces.
    near_bed = face_centroids[:, 2] <= (z_min + 0.05)
    downward_flat = face_normals[:, 2] < -0.95
    base_faces = near_bed & downward_flat
    base_area = float(face_areas[base_faces].sum())

    # Strongly penalize tiny base contact so slicers can create first-layer extrusion.
    if base_area < 1.0:
        return support_area + (0.15 * height) + 500.0

    # Weighted score: reduce support area and height, increase base stability.
    return support_area + (0.15 * height) - (0.25 * base_area)


def optimize_orientation(mesh):
    """
    Find a better rotation to reduce support usage.

    Returns
    -------
    tuple[trimesh.Trimesh, tuple[float, float, float], float]
        rotated_mesh, chosen_angles_xyz_deg, score
    """
    # Compact candidate set that covers common practical print orientations.
    candidates = [
        (0, 0, 0),
        (90, 0, 0), (180, 0, 0), (270, 0, 0),
        (0, 90, 0), (0, 180, 0), (0, 270, 0),
        (0, 0, 90), (0, 0, 180), (0, 0, 270),
        (90, 90, 0), (90, 270, 0), (270, 90, 0), (270, 270, 0),
    ]

    best_mesh = mesh.copy()
    best_angles = (0, 0, 0)
    best_score = _support_risk_score(best_mesh)

    for angles in candidates:
        candidate = mesh.copy()
        rot = _rotation_matrix_xyz(*angles)
        candidate.apply_transform(rot)
        score = _support_risk_score(candidate)
        if score < best_score:
            best_mesh = candidate
            best_angles = angles
            best_score = score

    # Center model in XY and place on build plate; sink by tiny epsilon so first layer intersects.
    center_xy = best_mesh.bounding_box.centroid[:2]
    best_mesh.apply_translation([-float(center_xy[0]), -float(center_xy[1]), 0.0])

    z_min = float(best_mesh.bounds[0][2])
    bed_sink_mm = 0.02
    best_mesh.apply_translation([0.0, 0.0, -z_min - bed_sink_mm])

    return best_mesh, best_angles, best_score


def generate_output_stl_path(input_path, suffix):
    base, ext = os.path.splitext(input_path)
    return f"{base}{suffix}{ext or '.stl'}"


def print_results(results):
    """Print results in a clear format"""

    if results["passed"]:
        print("PASSED CHECKS:")
        for item in results["passed"]:
            print(f"  {item}")

    if results["warnings"]:
        print("\nWARNINGS:")
        for item in results["warnings"]:
            print(f"  {item}")

    if results["errors"]:
        print("\nERRORS:")
        for item in results["errors"]:
            print(f"  {item}")

    # ── Final verdict ─────────────────────────────────────
    print(f"\n{'='*50}")
    error_count = len(results["errors"])
    warning_count = len(results["warnings"])

    if error_count == 0 and warning_count == 0:
        print("VERDICT: READY TO PRINT - no issues found!")
    elif error_count == 0 and warning_count > 0:
        print(f"VERDICT: PROBABLY PRINTABLE - {warning_count} warning(s) to review")
    else:
        print(f"VERDICT: NOT PRINTABLE - {error_count} error(s) must be fixed")

    print(f"{'='*50}\n")

    return error_count == 0


def main():
    ensure_dependencies()

    # Get STL path from command line or use default
    if len(sys.argv) > 1:
        stl_path = sys.argv[1]
    else:
        stl_path = "output.stl"

    results = check_stl(stl_path)
    is_printable = print_results(results)

    # Automatic repair + rotation optimization pipeline.
    # Runs when there are errors or warnings to improve printability.
    needs_help = bool(results["errors"] or results["warnings"])
    if needs_help:
        print("Attempting automatic repair and orientation optimization...\n")
        try:
            trimesh = importlib.import_module("trimesh")
            original = trimesh.load(stl_path)

            repaired, repair_steps, repair_success = try_repair_mesh(original)
            if repair_steps:
                print("Repair actions:")
                for step in repair_steps:
                    print(f"  - {step}")
                print("")

            if repair_success:
                print("[OK] Mesh looks repairable and was repaired successfully.")
                oriented, angles, score = optimize_orientation(repaired)
                rx, ry, rz = angles
                print(
                    f"[OK] Suggested rotation (degrees): X={rx}, Y={ry}, Z={rz} "
                    f"(support score: {score:.2f})"
                )

                out_path = generate_output_stl_path(stl_path, "_repaired_oriented")
                oriented.export(out_path)
                print(f"[OK] Saved repaired + oriented STL: {out_path}\n")

                # Re-check the generated mesh and use that verdict for exit code.
                print("Re-checking generated STL...\n")
                post_results = check_stl(out_path)
                is_printable = print_results(post_results)
            else:
                print(
                    "[WARN] Mesh does not appear fully repairable automatically. "
                    "Manual modeling repair may still be required.\n"
                )
        except Exception as e:
            print(f"[WARN] Auto-repair/orientation step failed: {e}\n")

    # Return exit code (0 = printable, 1 = not printable)
    # Useful when called from other scripts
    sys.exit(0 if is_printable else 1)


if __name__ == "__main__":
    main()
