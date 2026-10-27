#!/usr/bin/env python3
"""Import generated GLBs into fresh Blender scenes and check the round trip.

Run from the repository:
  blender --background --factory-startup --python-exit-code 1 \
    --python tools/smoke_blender.py -- generated

Only reads assets. No rendering or target-engine certification is performed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
import sys

import bpy
from mathutils import Matrix, Vector

sys.path.insert(0, str(Path(__file__).resolve().parent))
from validate_exports import GLB  # noqa: E402


EXPECTED_ROLES = {
    "enduro": {"enduro_steering_pivot", "enduro_front_wheel_pivot", "enduro_rear_wheel_pivot"},
    "helicopter": {"main_rotor_pivot", "tail_rotor_pivot"},
    "airplane": {
        "propeller_pivot", "nose_wheel_pivot", "left_wheel_pivot", "right_wheel_pivot",
        "left_aileron_pivot", "right_aileron_pivot", "left_flap_pivot", "right_flap_pivot",
        "elevator_pivot", "rudder_pivot",
    },
    "offroader": {
        "front_left_steering_pivot", "front_right_steering_pivot",
        "front_left_wheel_pivot", "front_right_wheel_pivot",
        "rear_left_wheel_pivot", "rear_right_wheel_pivot",
        "hood_hinge_pivot", "cabin_steering_pivot", "gear_lever_pivot",
        "transfer_lever_pivot", "handbrake_pivot", "accelerator_pivot",
        "brake_pivot", "clutch_pivot",
    },
    "motorboat": {
        "outboard_trim_pivot", "outboard_steering_pivot",
        "boat_propeller_pivot", "boat_steering_wheel_pivot",
    },
}

PIVOT_AXES = {
    "enduro_steering_pivot": "Z", "enduro_front_wheel_pivot": "Y",
    "enduro_rear_wheel_pivot": "Y", "main_rotor_pivot": "Z", "tail_rotor_pivot": "Y",
    "propeller_pivot": "X", "nose_wheel_pivot": "Y", "left_wheel_pivot": "Y",
    "right_wheel_pivot": "Y", "left_aileron_pivot": "Y", "right_aileron_pivot": "Y",
    "left_flap_pivot": "Y", "right_flap_pivot": "Y", "elevator_pivot": "Y",
    "rudder_pivot": "Z",
    "front_left_steering_pivot": "Z", "front_right_steering_pivot": "Z",
    "front_left_wheel_pivot": "Y", "front_right_wheel_pivot": "Y",
    "rear_left_wheel_pivot": "Y", "rear_right_wheel_pivot": "Y",
    "hood_hinge_pivot": "Y", "cabin_steering_pivot": "X", "gear_lever_pivot": "Y",
    "transfer_lever_pivot": "Y", "handbrake_pivot": "Y", "accelerator_pivot": "Y",
    "brake_pivot": "Y", "clutch_pivot": "Y",
    "outboard_trim_pivot": "Y", "outboard_steering_pivot": "Z",
    "boat_propeller_pivot": "X", "boat_steering_wheel_pivot": "X",
}


def check(condition, message):
    if not condition:
        raise AssertionError(message)


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def mesh_statistics(objects):
    meshes = [obj for obj in objects if obj.type == "MESH"]
    check(meshes, "Scene contains no mesh objects")
    minimum, maximum = [math.inf] * 3, [-math.inf] * 3
    triangles = 0
    for obj in meshes:
        obj.data.calc_loop_triangles()
        triangles += len(obj.data.loop_triangles)
        for vertex in obj.data.vertices:
            point = obj.matrix_world @ vertex.co
            check(all(math.isfinite(value) for value in point), "Imported vertex is non-finite")
            for axis in range(3):
                minimum[axis] = min(minimum[axis], point[axis])
                maximum[axis] = max(maximum[axis], point[axis])
    check(triangles > 0, "Scene contains no triangles")
    return {"mesh_objects": len(meshes), "triangles": triangles,
            "bounds": {"min": minimum, "max": maximum,
                       "dimensions": [maximum[i] - minimum[i] for i in range(3)]}}


def check_bounds(actual, expected, label):
    for key in ("min", "max", "dimensions"):
        for axis, (got, want) in enumerate(zip(actual[key], expected[key])):
            check(math.isclose(got, want, rel_tol=1e-5, abs_tol=2e-5),
                  f"{label}: {key}[{axis}] changed: {want} -> {got}")


def reset_and_import(path):
    bpy.ops.wm.read_factory_settings(use_empty=True)
    outcome = bpy.ops.import_scene.gltf(filepath=str(path))
    check("FINISHED" in outcome, f"Blender could not import {path}")
    bpy.context.view_layer.update()
    return list(bpy.context.scene.objects)


def descendant_meshes(root):
    found = []
    stack = list(root.children)
    while stack:
        obj = stack.pop()
        if obj.type == "MESH":
            found.append(obj)
        stack.extend(obj.children)
    return found


def check_pivot_rotation(pivot, children, objects, axis_name):
    """Exercise the exported hierarchy, then restore the imported scene exactly."""
    role = pivot["part_role"]
    basis = pivot.matrix_basis.copy()
    world = pivot.matrix_world.copy()
    origin = world.translation.copy()
    axis = Vector({"X": (1, 0, 0), "Y": (0, 1, 0), "Z": (0, 0, 1)}[axis_name])
    local_from_world = world.inverted()
    candidate = None
    largest_radius = -1.0
    # A hub-centre or hinge vertex may legitimately stay still. Choose the
    # descendant vertex furthest from the pivot's declared local axis.
    for child in children:
        local_from_mesh = local_from_world @ child.matrix_world
        for vertex in child.data.vertices:
            local = local_from_mesh @ vertex.co
            radius = (local - axis * local.dot(axis)).length
            if radius > largest_radius:
                largest_radius = radius
                candidate = (child, vertex.co.copy())
    check(candidate is not None and largest_radius > 1e-4,
          f"{role} has no geometry away from its rotation axis")
    child, vertex = candidate
    point_before = child.matrix_world @ vertex
    radius_before = (point_before - origin).length
    unrelated = [obj for obj in objects if obj.type == "MESH" and obj not in children]
    check(unrelated, f"{role} has no independent geometry to test isolation")
    references = [(obj, obj.matrix_world.copy()) for obj in unrelated]
    low, high = pivot.get("rotation_min_degrees", -180), pivot.get("rotation_max_degrees", 180)
    angle_degrees = min(17, high) if high > 0 else max(-17, low)
    check(abs(angle_degrees) > 0, f"{role} has no permitted travel")
    angle = math.radians(angle_degrees)
    try:
        pivot.matrix_basis = basis @ Matrix.Rotation(angle, 4, axis_name)
        bpy.context.view_layer.update()
        point_after = child.matrix_world @ vertex
        displacement = (point_after - point_before).length
        check((pivot.matrix_world.translation - origin).length < 2e-5,
              f"{role} rotation moved its own pivot origin")
        check(displacement > 1e-5, f"{role} rotation did not move its descendant geometry")
        check(math.isclose((point_after - origin).length, radius_before, rel_tol=1e-5, abs_tol=2e-5),
              f"{role} rotation changed the distance between its geometry and pivot")
        expected = pivot.matrix_world @ local_from_world @ point_before
        check((point_after - expected).length < 3e-5,
              f"{role} descendant did not follow the pivot's rigid rotation")
        for obj, transform in references:
            delta = max(abs(obj.matrix_world[row][col] - transform[row][col])
                        for row in range(4) for col in range(4))
            check(delta < 2e-5, f"Rotating {role} also moved unrelated geometry {obj.name}")
    finally:
        pivot.matrix_basis = basis
        bpy.context.view_layer.update()
    check((child.matrix_world @ vertex - point_before).length < 3e-5,
          f"{role} geometry did not return to its initial position")
    return {"local_axis": axis_name, "angle_degrees": angle_degrees,
            "tested_vertex_displacement_m": round(displacement, 6)}


def check_images(path):
    expected = len(GLB(path).doc.get("images", []))
    referenced = {
        node.image for material in bpy.data.materials if material.use_nodes
        for node in material.node_tree.nodes if node.type == "TEX_IMAGE" and node.image
    }
    check(len(referenced) == expected,
          f"Image references changed during import: expected {expected}, found {len(referenced)}")
    for image in referenced:
        check(image.size[0] > 0 and image.size[1] > 0 and image.has_data,
              f"Texture pixels unavailable: {image.name}")
        check(bool(image.packed_file) or bool(image.packed_files),
              f"Embedded GLB image was not packed during import: {image.name}")
    return len(referenced)


def check_glazing_transmission(objects, path, label, expected_factor=None):
    """Check transmission on materials actually assigned to exported meshes."""
    document = GLB(path).doc
    materials = document.get("materials", [])
    used_indices = {
        primitive["material"] for mesh in document.get("meshes", [])
        for primitive in mesh.get("primitives", []) if "material" in primitive
    }
    transmitted = []
    for index in used_indices:
        material = materials[index]
        extension = material.get("extensions", {}).get("KHR_materials_transmission", {})
        factor = extension.get("transmissionFactor", 0)
        if isinstance(factor, (int, float)) and factor > 0:
            check(math.isfinite(factor) and factor <= 1,
                  f"Invalid exported {label} glazing transmission")
            if expected_factor is not None:
                check(math.isclose(factor, expected_factor, rel_tol=1e-5, abs_tol=1e-6),
                      f"{label} glazing transmission changed: expected {expected_factor}, found {factor}")
            transmitted.append((material.get("name"), factor))
    check(transmitted, f"{label} GLB lost its nonzero KHR_materials_transmission glazing")
    check("KHR_materials_transmission" in document.get("extensionsUsed", []),
          f"{label} GLB does not declare its transmission extension")

    imported_materials = {
        material for obj in objects if obj.type == "MESH"
        for material in obj.data.materials if material
    }
    glazing = []
    for name, factor in transmitted:
        matches = [material for material in imported_materials if material.name == name]
        check(len(matches) == 1, f"Exported glazing {name!r} is not assigned after Blender import")
        material = matches[0]
        check(material.use_nodes, f"Imported glazing {name!r} has no shader nodes")
        weights = [node.inputs.get("Transmission Weight") for node in material.node_tree.nodes
                   if node.type == "BSDF_PRINCIPLED"]
        check(any(weight is not None and not weight.is_linked
                  and weight.default_value > 0
                  and math.isclose(weight.default_value, factor, rel_tol=1e-5, abs_tol=1e-6)
                  for weight in weights),
              f"Imported glazing {name!r} lost its Principled transmission value {factor}")
        glazing.append({"material": name, "transmission_factor": factor})
    return glazing


def check_offroader_components(objects, path):
    """Verify steering, optical materials and engine access through the opening hood."""
    by_role = {obj.get("part_role"): obj for obj in objects if obj.get("part_role")}
    parents = {}
    for side in ("left", "right"):
        wheel_role = f"front_{side}_wheel_pivot"
        steering_role = f"front_{side}_steering_pivot"
        check(by_role[wheel_role].parent == by_role[steering_role],
              f"{wheel_role} is not directly parented to {steering_role}")
        parents[wheel_role] = steering_role
    glazing = check_glazing_transmission(objects, path, "Offroader")
    check(any("optical_polycarbonate" in item["material"] for item in glazing),
          "Headlamp optics lost their transmitting material")
    document = GLB(path).doc
    coated = [mat for mat in document.get("materials", [])
              if mat.get("extensions", {}).get("KHR_materials_clearcoat", {}).get("clearcoatFactor", 0) > 0]
    check(any("sage_paint" in mat.get("name", "") for mat in coated), "Paint lost its clearcoat")
    paint_indices={i for i,mat in enumerate(document.get("materials", [])) if "sage_paint" in mat.get("name", "")}
    paint_primitives=[p for mesh in document.get("meshes", []) for p in mesh.get("primitives", [])
                      if p.get("material") in paint_indices]
    check(paint_primitives and all("COLOR_0" in p.get("attributes", {}) for p in paint_primitives),
          "Baked paint and road-dust vertex colors were lost during export")
    engine = by_role.get("engine_bay_assembly")
    check(engine is not None, "Engine bay assembly missing from export")
    engine_meshes = descendant_meshes(engine)
    check(engine_meshes, "Engine bay contains no exported geometry")
    hood = by_role["hood_hinge_pivot"]
    hood_meshes = descendant_meshes(hood)
    basis = hood.matrix_basis.copy()
    def downward_hit():
        bpy.context.view_layer.update()
        hit = bpy.context.scene.ray_cast(bpy.context.evaluated_depsgraph_get(),
                                         Vector((1.40, 0, 3.0)), Vector((0, 0, -1)), distance=3.0)
        check(hit[0], "No geometry beneath engine-bay inspection ray")
        return hit[4].original
    check(downward_hit() in hood_meshes, "Closed hood does not cover the engine bay")
    try:
        hood.matrix_basis = basis @ Matrix.Rotation(math.radians(-65), 4, "Y")
        check(downward_hit() in engine_meshes, "Opening the hood does not expose the modeled engine")
    finally:
        hood.matrix_basis = basis
        bpy.context.view_layer.update()
    return {"front_wheel_parents": parents, "glazing_transmission": glazing,
            "paint_clearcoat": True, "surface_vertex_colors": True, "engine_access_after_hood_open": True}


def check_motorboat_components(objects, path, bounds, source_bounds):
    """Preserve nested motor articulation and the source Z=0 waterline."""
    by_role = {obj.get("part_role"): obj for obj in objects if obj.get("part_role")}
    parents = {"outboard_steering_pivot": "outboard_trim_pivot",
               "boat_propeller_pivot": "outboard_steering_pivot"}
    for child, parent in parents.items():
        check(by_role[child].parent == by_role[parent],
              f"{child} is not directly parented to {parent}")
    root = by_role["outboard_trim_pivot"].parent
    check(root is not None and root.get("asset") == "motorboat",
          "Outboard trim pivot lost its motorboat asset root")
    check(by_role["boat_steering_wheel_pivot"].parent == root,
          "Helm wheel must stay under the asset root, outside the moving outboard")
    for label, box in (("saved source", source_bounds), ("imported LOD0", bounds)):
        check(box["min"][2] < -.2 and box["max"][2] > .5,
              f"Motorboat {label} no longer straddles the Z=0 waterline")
    glazing = check_glazing_transmission(objects, path, "Motorboat", expected_factor=1.0)
    return {"outboard_parents": parents, "waterline_z_m": 0,
            "source_z_extent_m": [source_bounds["min"][2], source_bounds["max"][2]],
            "imported_z_extent_m": [bounds["min"][2], bounds["max"][2]],
            "glazing_transmission": glazing}


def check_asset(manifest_path):
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    name, directory = manifest["asset"], manifest_path.parent
    check(name in EXPECTED_ROLES, f"Unknown vehicle asset {name!r}")
    records = {record["path"]: record for record in manifest["files"]}
    lod = next((item for item in manifest["lods"] if item["level"] == 0), None)
    check(lod is not None, f"Manifest has no LOD0: {name}")
    lod_path = directory / lod["file"]
    collision_path = directory / (name + "_collision.glb")
    blend_path = directory / (name + ".blend")
    for path in (lod_path, collision_path, blend_path):
        check(path.name in records, f"Missing file manifest record: {path.name}")
        check(path.stat().st_size == records[path.name]["bytes"], f"File size changed: {path}")
        check(digest(path) == records[path.name]["sha256"], f"File hash changed: {path}")

    # Collision bounds are not in manifest v1. Read the saved source scene as
    # the round-trip reference, and compare its count with the manifest.
    bpy.ops.wm.open_mainfile(filepath=str(blend_path), load_ui=False)
    source_collision = [obj for obj in bpy.context.scene.objects if obj.get("collision_proxy")]
    check(len(source_collision) == manifest["collision_proxies"],
          "Source collision proxy count differs from manifest")
    reference_collision = mesh_statistics(source_collision)
    boat_source_bounds = None
    if name == "motorboat":
        source = bpy.data.collections.get("motorboat_SOURCE")
        check(source is not None, "Saved boat scene has no editable source collection")
        boat_source_bounds = mesh_statistics(list(source.all_objects))["bounds"]

    objects = reset_and_import(lod_path)
    actual = mesh_statistics(objects)
    check(actual["triangles"] == lod["triangles"],
          f"{name} LOD0 triangle count differs from manifest")
    check(actual["mesh_objects"] == lod["mesh_objects"],
          f"{name} LOD0 mesh object count differs from manifest")
    check_bounds(actual["bounds"], manifest["source_bounds"], name + " LOD0")
    roles, rotations = {}, {}
    for role in sorted(EXPECTED_ROLES[name]):
        matches = [obj for obj in objects if obj.get("part_role") == role]
        check(len(matches) == 1, f"Expected one preserved {role} pivot; found {len(matches)}")
        pivot = matches[0]
        check(pivot.type == "EMPTY", f"{role} is no longer an empty pivot")
        children = descendant_meshes(pivot)
        check(children, f"Pivot {role} has no mesh descendants")
        roles[role] = len(children)
        rotations[role] = check_pivot_rotation(pivot, children, objects, PIVOT_AXES[role])
    textures = check_images(lod_path)
    check(textures > 0, f"{name} lost all embedded textures")
    for obj in objects:
        if obj.type == "MESH":
            check(bool(obj.data.uv_layers), f"Mesh {obj.name} has no UV layer after import")
    offroader = check_offroader_components(objects, lod_path) if name == "offroader" else None
    motorboat = check_motorboat_components(objects, lod_path, actual["bounds"], boat_source_bounds) if name == "motorboat" else None

    collision_objects = reset_and_import(collision_path)
    collision = mesh_statistics(collision_objects)
    check(collision["mesh_objects"] == manifest["collision_proxies"],
          f"{name} collision count differs from manifest")
    check(collision["triangles"] == reference_collision["triangles"],
          f"{name} collision triangles differ from saved source")
    check_bounds(collision["bounds"], reference_collision["bounds"], name + " collision")
    check(all(obj.get("collision_proxy") for obj in collision_objects if obj.type == "MESH"),
          "Collision proxy extras did not survive import")
    result = {"asset": name, "ok": True, "lod0": actual,
              "pivot_mesh_descendants": roles, "embedded_images": textures,
              "pivot_rotation_checks": rotations,
              "collision": collision}
    if offroader is not None:
        result["offroader_components"] = offroader
    if motorboat is not None:
        result["motorboat_components"] = motorboat
    return result


def main():
    check(bpy.app.background, "Run this test in background Blender to protect open work")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path, help="Generated root or one asset directory")
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    options = parser.parse_args(argv)
    manifests = sorted(options.directory.resolve().rglob("manifest.json"))
    check(manifests, "No manifests found; generate the assets first")
    reports, errors = [], []
    for path in manifests:
        try:
            reports.append(check_asset(path))
        except Exception as error:
            errors.append({"manifest": str(path), "error": str(error)})
    result = {"ok": not errors, "blender_version": bpy.app.version_string,
              "assets": reports, "errors": errors,
              "scope": "Blender GLB import, transforms, pivot rotation/isolation, steering/glazing, boat waterline and embedded textures; no rendering or engine acceptance."}
    print("BLENDER_ROUNDTRIP_RESULT " + json.dumps(result, indent=2), flush=True)
    check(not errors, "Blender GLB round-trip validation failed; see JSON report")


if __name__ == "__main__":
    main()
