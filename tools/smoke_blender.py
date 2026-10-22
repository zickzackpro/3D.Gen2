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

sys.path.insert(0, str(Path(__file__).resolve().parent))
from validate_exports import GLB  # noqa: E402


EXPECTED_ROLES = {
    "enduro": {"enduro_steering_pivot", "enduro_front_wheel_pivot", "enduro_rear_wheel_pivot"},
    "helicopter": {"main_rotor_pivot", "tail_rotor_pivot"},
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

    objects = reset_and_import(lod_path)
    actual = mesh_statistics(objects)
    check(actual["triangles"] == lod["triangles"],
          f"{name} LOD0 triangle count differs from manifest")
    check(actual["mesh_objects"] == lod["mesh_objects"],
          f"{name} LOD0 mesh object count differs from manifest")
    check_bounds(actual["bounds"], manifest["source_bounds"], name + " LOD0")
    roles = {}
    for role in sorted(EXPECTED_ROLES[name]):
        matches = [obj for obj in objects if obj.get("part_role") == role]
        check(len(matches) == 1, f"Expected one preserved {role} pivot; found {len(matches)}")
        pivot = matches[0]
        check(pivot.type == "EMPTY", f"{role} is no longer an empty pivot")
        children = descendant_meshes(pivot)
        check(children, f"Pivot {role} has no mesh descendants")
        roles[role] = len(children)
    textures = check_images(lod_path)
    check(textures > 0, f"{name} lost all embedded textures")
    for obj in objects:
        if obj.type == "MESH":
            check(bool(obj.data.uv_layers), f"Mesh {obj.name} has no UV layer after import")

    collision_objects = reset_and_import(collision_path)
    collision = mesh_statistics(collision_objects)
    check(collision["mesh_objects"] == manifest["collision_proxies"],
          f"{name} collision count differs from manifest")
    check(collision["triangles"] == reference_collision["triangles"],
          f"{name} collision triangles differ from saved source")
    check_bounds(collision["bounds"], reference_collision["bounds"], name + " collision")
    check(all(obj.get("collision_proxy") for obj in collision_objects if obj.type == "MESH"),
          "Collision proxy extras did not survive import")
    return {"asset": name, "ok": True, "lod0": actual,
            "pivot_mesh_descendants": roles, "embedded_images": textures,
            "collision": collision}


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
              "scope": "Blender GLB import, transforms, articulation hierarchy and embedded textures; no rendering or engine acceptance."}
    print("BLENDER_ROUNDTRIP_RESULT " + json.dumps(result, indent=2), flush=True)
    check(not errors, "Blender GLB round-trip validation failed; see JSON report")


if __name__ == "__main__":
    main()
