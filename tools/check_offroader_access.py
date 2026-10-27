#!/usr/bin/env python3
"""Check Offroader access geometry in an editable source .blend.

Run in a fresh background Blender process, for example:
  blender --background --factory-startup --python-exit-code 1 \
    --python tools/check_offroader_access.py -- \
    generated/offroader/offroader.blend --json /tmp/offroader-access.json

Checks initial 5/10-degree opening poses and representative wider angles
against the body, interior and other closed leaves. Intentional hinge/pin and
latch/striker mating interfaces are excluded as obstacles. Moving hinge straps are still checked against pillars.
Fixed tailgate hinge leaves must also touch their intended pillar mounts within
1 mm. BVH surface intersections are a geometric regression check, not a continuous
collision simulation, a clearance tolerance certificate or an engine test.
The input file is never saved or modified.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
import sys

import bpy
from mathutils import Vector
from mathutils.bvhtree import BVHTree


ROLES = {
    "front_left_door_hinge_pivot": (-1, 70),
    "rear_left_door_hinge_pivot": (-1, 65),
    "front_right_door_hinge_pivot": (1, 70),
    "rear_right_door_hinge_pivot": (1, 65),
    "tailgate_hinge_pivot": (1, 100),
}
MATING_INTERFACES = (
    "fixed_hinge", "hinge_knuckle", "fixed_latch_striker",
    "fixed_striker_base", "striker_screw",
)


def check(condition, message):
    if not condition:
        raise AssertionError(message)


def descendant(obj, parent):
    while obj.parent:
        obj = obj.parent
        if obj == parent:
            return True
    return False


def geometry(obj):
    vertices = [obj.matrix_world @ vertex.co for vertex in obj.data.vertices]
    check(vertices, f"Empty source mesh: {obj.name}")
    minimum = Vector(tuple(min(v[i] for v in vertices) for i in range(3)))
    maximum = Vector(tuple(max(v[i] for v in vertices) for i in range(3)))
    tree = BVHTree.FromPolygons(vertices, [tuple(p.vertices) for p in obj.data.polygons])
    return tree, minimum, maximum


def overlapping_bounds(a, b):
    return all(a[1][i] <= b[2][i] and a[2][i] >= b[1][i] for i in range(3))


def validate(path):
    bpy.ops.wm.open_mainfile(filepath=str(path))
    collection = bpy.data.collections.get("offroader_SOURCE")
    check(collection is not None, "Expected editable offroader_SOURCE collection")
    objects = list(collection.objects)
    meshes = [obj for obj in objects if obj.type == "MESH"]
    pivots = {}
    for role in ROLES:
        matches = [obj for obj in objects if obj.get("part_role") == role]
        check(len(matches) == 1, f"Expected one source access pivot: {role}")
        pivots[role] = matches[0]
        check(abs(matches[0].rotation_euler.z) < 1e-6, f"Source leaf is not closed: {role}")
    leaves = {role: [obj for obj in meshes if obj.get("access_role") == role] for role in ROLES}
    for role, parts in leaves.items():
        check(parts, f"No leaf geometry: {role}")
        for obj in parts:
            check(descendant(obj, pivots[role]), f"Incorrect leaf parent: {obj.name}")
    fixed = [obj for obj in meshes if not obj.get("access_role")]
    excluded = [obj.name for obj in fixed if any(token in obj.name for token in MATING_INTERFACES)]
    obstacles = [obj for obj in fixed if obj.name not in excluded]
    closed_geometry = {obj.name: geometry(obj) for obj in meshes}
    report = {
        "source": path.name,
        "source_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "scope": "Representative-pose BVH surface intersections and fixed hinge mounting contact; no runtime physics or continuous sweep certification",
        "fixed_obstacle_meshes": len(obstacles),
        "excluded_fixed_mating_interfaces": excluded,
        "sweeps": {},
    }
    all_hits = []
    for role, (direction, maximum) in ROLES.items():
        pivot = pivots[role]
        other_leaves = [obj for other, parts in leaves.items() if other != role for obj in parts]
        candidate_obstacles = obstacles + other_leaves
        samples = []
        original_rotation = pivot.rotation_euler.copy()
        try:
            for angle in (5, 10, 15, 35, 55, maximum):
                pivot.rotation_euler.z = math.radians(direction * angle)
                bpy.context.view_layer.update()
                hits = []
                for obj in leaves[role]:
                    moving = geometry(obj)
                    for obstacle in candidate_obstacles:
                        stationary = closed_geometry[obstacle.name]
                        if overlapping_bounds(moving, stationary):
                            intersections = moving[0].overlap(stationary[0])
                            if intersections:
                                hits.append({"moving": obj.name, "obstacle": obstacle.name,
                                             "intersecting_face_pairs": len(intersections)})
                samples.append({"degrees": direction * angle, "surface_intersections": hits})
                all_hits.extend(hits)
        finally:
            pivot.rotation_euler = original_rotation
            bpy.context.view_layer.update()
        report["sweeps"][role] = {"moving_meshes": len(leaves[role]), "samples": samples}

    margins = {}
    for side in ("left", "right"):
        bottom_parts = [obj for obj in leaves[f"front_{side}_door_hinge_pivot"]
                        if obj.name.endswith(("_door_skin", "_door_folded_hem"))]
        grips = [obj for obj in fixed if f"_{side}_step_grip" in obj.name]
        check(bottom_parts and grips, f"Missing front door/step construction: {side}")
        bottom = min(closed_geometry[obj.name][1].z for obj in bottom_parts)
        step_top = max(closed_geometry[obj.name][2].z for obj in grips)
        margins[f"front_{side}_door_above_step_m"] = bottom - step_top
    tailgate = [obj for obj in leaves["tailgate_hinge_pivot"] if obj.name.endswith("_tailgate_lower")]
    threshold = [obj for obj in fixed if obj.name.endswith("_rear_aperture_threshold")]
    check(len(tailgate) == len(threshold) == 1, "Missing tailgate/threshold construction")
    margins["tailgate_above_threshold_m"] = (
        closed_geometry[tailgate[0].name][1].z - closed_geometry[threshold[0].name][2].z)
    report["vertical_clearances_m"] = margins

    # The opening axis can remain correct while a fixed bracket floats beside
    # a narrowed pillar. Check actual mounting surfaces, not custom metadata.
    hinge_leaves = [obj for obj in fixed if "tailgate_fixed_hinge_leaf" in obj.name]
    mounts = [obj for obj in fixed if "rear_corner_pillar" in obj.name
              or "pressed_cabin_pillar_3" in obj.name]
    check(len(hinge_leaves) == 2 and mounts, "Missing fixed tailgate hinge mounts")
    contacts = {}
    for leaf in hinge_leaves:
        leaf_tree = closed_geometry[leaf.name][0]
        candidates = []
        for mount in mounts:
            mount_tree = closed_geometry[mount.name][0]
            overlap = leaf_tree.overlap(mount_tree)
            distance = 0.0 if overlap else min(
                [mount_tree.find_nearest(leaf.matrix_world @ vertex.co)[3]
                 for vertex in leaf.data.vertices]
                + [leaf_tree.find_nearest(mount.matrix_world @ vertex.co)[3]
                   for vertex in mount.data.vertices])
            candidates.append({"mount": mount.name,
                               "surface_distance_m": distance,
                               "intersecting_face_pairs": len(overlap)})
        contacts[leaf.name] = min(candidates, key=lambda item: item["surface_distance_m"])
    report["fixed_hinge_mount_contacts"] = contacts
    report["hinge_mount_tolerance_m"] = .001
    report["passed"] = (not all_hits and all(value > .003 for value in margins.values())
                        and all(item["surface_distance_m"] <= .001
                                for item in contacts.values()))
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("--json", type=Path, help="Optional verification receipt")
    args = parser.parse_args(sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else [])
    if not bpy.app.background:
        parser.error("Run in background mode to preserve any open Blender session")
    path = args.source.resolve()
    if not path.is_file() or path.suffix != ".blend":
        parser.error("Expected an existing editable source .blend")
    report = validate(path)
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))
    check(report["passed"], "Access geometry intersects an obstacle, has insufficient clearance, or has an unmounted hinge leaf")


if __name__ == "__main__":
    main()
