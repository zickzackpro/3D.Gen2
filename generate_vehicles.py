#!/usr/bin/env python3
"""Original procedural enduro, helicopter, airplane and 4x4 for Blender 4.3+.

Run with Python (launches Blender), or:
blender --background --factory-startup --python generate_vehicles.py -- --asset all --render

Units: metres. Source: X forward, Y left, Z up. GLB uses standard glTF Y up.
Copyright (c) 2026 zickzackpro. SPDX-License-Identifier: MIT
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

GENERATOR_SHA256 = hashlib.sha256(Path(__file__).resolve().read_bytes()).hexdigest()

try:
    import bpy
    import bmesh
    import numpy as np
    from mathutils import Matrix, Vector
except ModuleNotFoundError as error:
    if __name__ != "__main__" or error.name != "bpy":
        raise
    executable = shutil.which("blender")
    if not executable:
        raise SystemExit("Blender 4.3+ installieren und 'blender' zu PATH hinzufuegen.")
    raise SystemExit(subprocess.call([
        executable, "--background", "--factory-startup", "--python-exit-code", "1",
        "--python", str(Path(__file__).resolve()), "--", *sys.argv[1:]
    ]))


def world_matrix(obj):
    if obj.parent:
        return world_matrix(obj.parent) @ obj.matrix_parent_inverse @ obj.matrix_basis
    return obj.matrix_basis.copy()


class Builder:
    """Small deterministic modelling API. Dimensions are actual SI metres."""

    def __init__(self, name, texture_size=256, seed=7):
        self.name, self.texture_size, self.seed = name, texture_size, seed
        self.collection = bpy.data.collections.new(name + "_SOURCE")
        bpy.context.scene.collection.children.link(self.collection)
        self.collision_collection = bpy.data.collections.new(name + "_COLLISION")
        bpy.context.scene.collection.children.link(self.collision_collection)
        self.objects, self.materials = [], {}
        self.root = bpy.data.objects.new(name + "_ROOT", None)
        self.collection.objects.link(self.root)
        self.root["asset"] = name
        self.root["units"] = "metres"
        self.root["forward_axis"] = "+X (Blender source)"

    def mat(self, key, color, metallic=0, roughness=.5, transmission=0, emission=None):
        if key in self.materials:
            return self.materials[key]
        mat = bpy.data.materials.new(self.name + "_" + key)
        mat.use_nodes = True
        rgba = tuple(color) if len(color) == 4 else (*color, 1)
        mat.diffuse_color = rgba
        nodes, links = mat.node_tree.nodes, mat.node_tree.links
        bsdf = nodes.get("Principled BSDF")
        bsdf.inputs["Base Color"].default_value = rgba
        bsdf.inputs["Metallic"].default_value = metallic
        bsdf.inputs["Roughness"].default_value = roughness
        bsdf.inputs["Transmission Weight"].default_value = transmission
        bsdf.inputs["IOR"].default_value = 1.45
        if emission is not None:
            rgb = emission if not isinstance(emission, (int, float)) else rgba[:3]
            bsdf.inputs["Emission Color"].default_value = (*rgb[:3], 1)
            bsdf.inputs["Emission Strength"].default_value = 2
        if not transmission and emission is None:
            # Real image textures, embedded in GLB: no unsupported procedural nodes.
            size = self.texture_size
            seed = int(hashlib.sha256((key + str(self.seed)).encode()).hexdigest()[:8], 16)
            rng = np.random.default_rng(seed)
            grain = rng.standard_normal((size, size)).astype(np.float32)
            orm = np.ones((size, size, 4), dtype=np.float32)
            orm[:, :, 1] = np.clip(roughness + grain * .018, .025, 1)
            orm[:, :, 2] = metallic
            image = self._image(key + "_metallic_roughness", orm)
            tex = nodes.new("ShaderNodeTexImage")
            tex.image = image
            tex.label = "Packed roughness (G), metallic (B); R is unbaked white"
            separate = nodes.new("ShaderNodeSeparateColor")
            links.new(tex.outputs["Color"], separate.inputs["Color"])
            links.new(separate.outputs["Green"], bsdf.inputs["Roughness"])
            links.new(separate.outputs["Blue"], bsdf.inputs["Metallic"])
            normal_pixels = np.ones((size, size, 4), dtype=np.float32)
            dx = np.roll(grain, 1, 1) - np.roll(grain, -1, 1)
            dy = np.roll(grain, 1, 0) - np.roll(grain, -1, 0)
            normals = np.stack((dx * .025, dy * .025, np.ones_like(grain)), axis=-1)
            normals /= np.linalg.norm(normals, axis=-1, keepdims=True)
            normal_pixels[:, :, :3] = normals * .5 + .5
            normal_tex = nodes.new("ShaderNodeTexImage")
            normal_tex.image = self._image(key + "_normal", normal_pixels)
            normal = nodes.new("ShaderNodeNormalMap")
            normal.inputs["Strength"].default_value = .22
            links.new(normal_tex.outputs["Color"], normal.inputs["Color"])
            links.new(normal.outputs["Normal"], bsdf.inputs["Normal"])
        self.materials[key] = mat
        return mat

    def _image(self, name, pixels):
        image = bpy.data.images.new(self.name + "_" + name, self.texture_size, self.texture_size, alpha=True)
        image.colorspace_settings.name = "Non-Color"
        image.pixels.foreach_set(pixels.ravel())
        image.pack()
        return image

    def _link(self, obj):
        self.collection.objects.link(obj)
        obj.parent = self.root
        self.objects.append(obj)
        return obj

    def parent(self, obj, parent):
        transform = world_matrix(obj)
        obj.parent = parent
        obj.matrix_parent_inverse = Matrix.Identity(4)
        obj.matrix_basis = world_matrix(parent).inverted() @ transform
        return obj

    def empty(self, name, loc=(0, 0, 0), parent=None):
        obj = self._link(bpy.data.objects.new(self.name + "_" + name, None))
        obj.location = loc
        obj.empty_display_type = "PLAIN_AXES"
        obj.empty_display_size = .1
        obj["part_role"] = name
        if parent is not None:
            self.parent(obj, parent)
        return obj

    def mesh(self, name, vertices, faces, mat, smooth=False, bevel=0):
        data = bpy.data.meshes.new(self.name + "_" + name)
        data.from_pydata(vertices, [], faces)
        data.update()
        if mat:
            data.materials.append(mat)
        obj = self._link(bpy.data.objects.new(self.name + "_" + name, data))
        for polygon in data.polygons:
            polygon.use_smooth = smooth
        if bevel:
            mod = obj.modifiers.new("Manufactured edge radius", "BEVEL")
            mod.width, mod.segments = bevel, 2
            mod.limit_method = "ANGLE"
            mod.harden_normals = True
            weighted = obj.modifiers.new("Surface normals", "WEIGHTED_NORMAL")
            weighted.keep_sharp = True
        return obj

    def box(self, name, loc, size, mat, bevel=0, rotation=None):
        x, y, z = (v * .5 for v in size)
        vertices = [(-x,-y,-z), (x,-y,-z), (x,y,-z), (-x,y,-z),
                    (-x,-y,z), (x,-y,z), (x,y,z), (-x,y,z)]
        faces = [(3,2,1,0), (0,1,5,4), (1,2,6,5), (2,3,7,6), (3,0,4,7), (4,5,6,7)]
        obj = self.mesh(name, vertices, faces, mat, bevel=bevel)
        obj.location = loc
        if rotation is not None:
            obj.rotation_euler = rotation
        return obj

    def sphere(self, name, loc, scale, mat, segments=24, rings=12):
        vertices = [(0, 0, scale[2])]
        for j in range(1, rings):
            theta = math.pi * j / rings
            for i in range(segments):
                phi = math.tau * i / segments
                vertices.append((scale[0]*math.sin(theta)*math.cos(phi),
                                 scale[1]*math.sin(theta)*math.sin(phi), scale[2]*math.cos(theta)))
        bottom = len(vertices)
        vertices.append((0, 0, -scale[2]))
        faces = [(0, 1+i, 1+(i+1)%segments) for i in range(segments)]
        for j in range(rings-2):
            for i in range(segments):
                a = 1+j*segments+i
                c = 1+j*segments+(i+1)%segments
                faces.append((a, a+segments, c+segments, c))
        faces += [(bottom, 1+(rings-2)*segments+(i+1)%segments,
                   1+(rings-2)*segments+i) for i in range(segments)]
        obj = self.mesh(name, vertices, faces, mat, smooth=True)
        obj.location = loc
        return obj

    def cylinder(self, name, a, b, radius, mat, vertices=24, radius2=None):
        a, b = Vector(a), Vector(b)
        axis = b-a
        if axis.length < 1e-8:
            raise ValueError("Zero-length cylinder: " + name)
        axis.normalize()
        helper = Vector((0,0,1)) if abs(axis.z) < .95 else Vector((1,0,0))
        u = axis.cross(helper).normalized()
        v = axis.cross(u).normalized()
        radius2 = radius if radius2 is None else radius2
        verts = []
        for center, rad in ((a, radius), (b, radius2)):
            verts.extend(tuple(center + rad*(math.cos(i*math.tau/vertices)*u + math.sin(i*math.tau/vertices)*v)) for i in range(vertices))
        faces = [(i,(i+1)%vertices,(i+1)%vertices+vertices,i+vertices) for i in range(vertices)]
        faces.extend([tuple(reversed(range(vertices))), tuple(range(vertices,2*vertices))])
        obj = self.mesh(name, verts, faces, mat, smooth=True)
        obj.data.polygons[-1].use_smooth = False
        obj.data.polygons[-2].use_smooth = False
        return obj

    def torus(self, name, loc, major_radius, minor_radius, mat, axis="Y", major_segments=48, minor_segments=10):
        verts, faces = [], []
        for i in range(major_segments):
            a = math.tau*i/major_segments
            for j in range(minor_segments):
                c = math.tau*j/minor_segments
                r = major_radius + minor_radius*math.cos(c)
                verts.append((r*math.cos(a), r*math.sin(a), minor_radius*math.sin(c)))
        for i in range(major_segments):
            for j in range(minor_segments):
                faces.append((i*minor_segments+j, ((i+1)%major_segments)*minor_segments+j,
                              ((i+1)%major_segments)*minor_segments+(j+1)%minor_segments,
                              i*minor_segments+(j+1)%minor_segments))
        obj = self.mesh(name, verts, faces, mat, smooth=True)
        obj.location = loc
        obj.rotation_mode = "QUATERNION"
        direction = Vector({"X":(1,0,0), "Y":(0,1,0), "Z":(0,0,1)}[axis])
        obj.rotation_quaternion = Vector((0,0,1)).rotation_difference(direction)
        return obj

    def tube(self, name, points, radius, mat, resolution=2):
        data = bpy.data.curves.new(self.name + "_" + name, "CURVE")
        data.dimensions, data.resolution_u = "3D", 8
        data.bevel_depth, data.bevel_resolution = radius, resolution
        data.use_fill_caps = True
        spline = data.splines.new("BEZIER")
        spline.bezier_points.add(len(points)-1)
        for point, position in zip(spline.bezier_points, points):
            point.co = position
            point.handle_left_type = point.handle_right_type = "AUTO"
        data.materials.append(mat)
        return self._link(bpy.data.objects.new(self.name + "_" + name, data))

    def collision_box(self, name, loc, size, rotation=None):
        obj = self.box("UCX_"+name, loc, size, None, rotation=rotation)
        self.collection.objects.unlink(obj)
        self.collision_collection.objects.link(obj)
        self.objects.remove(obj)
        obj.parent = None
        obj.name = "UCX_" + self.name + "_" + str(len(self.collision_collection.objects)-1).zfill(2)
        obj.display_type = "WIRE"
        obj.hide_render = True
        obj["collision_proxy"] = True
        return obj


def prepare_geometry(builder):
    """Resolve construction modifiers and curves, then unwrap editable source meshes."""
    bpy.context.view_layer.update()
    depsgraph = bpy.context.evaluated_depsgraph_get()
    for obj in list(builder.objects):
        if obj.type not in {"MESH", "CURVE"}:
            continue
        name = obj.name
        mesh = bpy.data.meshes.new_from_object(obj.evaluated_get(depsgraph), preserve_all_data_layers=True, depsgraph=depsgraph)
        replacement = bpy.data.objects.new(name + "_resolved", mesh)
        builder.collection.objects.link(replacement)
        replacement.parent = obj.parent
        replacement.matrix_parent_inverse = obj.matrix_parent_inverse.copy()
        replacement.matrix_basis = obj.matrix_basis.copy()
        for key in obj.keys():
            replacement[key] = obj[key]
        bm = bmesh.new()
        bm.from_mesh(mesh)
        bmesh.ops.recalc_face_normals(bm, faces=list(bm.faces))
        bm.to_mesh(mesh)
        bm.free()
        builder.objects[builder.objects.index(obj)] = replacement
        bpy.data.objects.remove(obj, do_unlink=True)
        replacement.name = name
    meshes = [obj for obj in builder.objects if obj.type == "MESH"]
    bpy.ops.object.select_all(action="DESELECT")
    for obj in meshes:
        obj.select_set(True)
    bpy.context.view_layer.objects.active = meshes[0]
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="SELECT")
    bpy.ops.uv.smart_project(angle_limit=math.radians(66), island_margin=.025)
    bpy.ops.object.mode_set(mode="OBJECT")
    bpy.ops.object.select_all(action="DESELECT")


def mesh_triangles(obj):
    obj.data.calc_loop_triangles()
    return len(obj.data.loop_triangles)


def collection_bounds(objects):
    corners = [world_matrix(obj) @ Vector(v) for obj in objects if obj.type == "MESH" for v in obj.bound_box]
    low = [min(v[i] for v in corners) for i in range(3)]
    high = [max(v[i] for v in corners) for i in range(3)]
    return {"min": low, "max": high, "dimensions": [high[i]-low[i] for i in range(3)]}


def export_lod(builder, directory, level, ratio):
    collection = bpy.data.collections.new(builder.name + "_LOD" + str(level))
    bpy.context.scene.collection.children.link(collection)
    originals = [builder.root, *builder.objects]
    copies = {}
    for obj in originals:
        clone = obj.copy()
        if obj.type == "MESH":
            clone.data = obj.data.copy()
        clone.name = obj.name + "_LOD" + str(level)
        collection.objects.link(clone)
        copies[obj] = clone
    for obj, clone in copies.items():
        clone.parent = copies.get(obj.parent)
        clone.matrix_parent_inverse = obj.matrix_parent_inverse.copy()
        clone.matrix_basis = obj.matrix_basis.copy()
    bpy.context.view_layer.update()
    if level:
        for obj in list(collection.objects):
            if obj.type != "MESH" or len(obj.data.polygons) < 24:
                continue
            decimate = obj.modifiers.new("LOD simplification", "DECIMATE")
            decimate.ratio = ratio
            decimate.use_collapse_triangulate = True
            depsgraph = bpy.context.evaluated_depsgraph_get()
            mesh = bpy.data.meshes.new_from_object(obj.evaluated_get(depsgraph), preserve_all_data_layers=True, depsgraph=depsgraph)
            obj.modifiers.clear()
            old_mesh = obj.data
            obj.data = mesh
            if old_mesh.users == 0:
                bpy.data.meshes.remove(old_mesh)
    # Batch draw calls by articulation group/material; editable source remains split.
    groups = {}
    for obj in collection.objects:
        if obj.type == "MESH":
            key = (obj.parent, tuple(obj.data.materials))
            groups.setdefault(key, []).append(obj)
    for (parent, mats), group in groups.items():
        bpy.ops.object.select_all(action="DESELECT")
        for obj in group:
            obj.select_set(True)
        bpy.context.view_layer.objects.active = group[0]
        if len(group) > 1:
            bpy.ops.object.join()
        material_name = mats[0].name if mats else "surface"
        group[0].name = parent.name + "__" + material_name if parent else material_name
    meshes = [obj for obj in collection.objects if obj.type == "MESH"]
    # Explicit triangles make polygon counts match the delivered GLBs.
    for obj in meshes:
        bm = bmesh.new()
        bm.from_mesh(obj.data)
        bmesh.ops.triangulate(bm, faces=list(bm.faces))
        bm.to_mesh(obj.data)
        bm.free()
    path = directory / (builder.name + "_LOD" + str(level) + ".glb")
    export_glb(path, list(collection.objects))
    result = {"level": level, "requested_decimation_ratio": ratio,
              "triangles": sum(mesh_triangles(obj) for obj in meshes),
              "mesh_objects": len(meshes), "file": path.name}
    for obj in collection.objects:
        obj.hide_render = True
        obj.hide_set(True)
    return result


def export_glb(path, objects):
    bpy.ops.object.select_all(action="DESELECT")
    for obj in objects:
        obj.hide_set(False)
        obj.select_set(True)
    bpy.context.view_layer.objects.active = objects[0]
    result = bpy.ops.export_scene.gltf(
        filepath=str(path), export_format="GLB", use_selection=True,
        export_texcoords=True, export_normals=True, export_tangents=True,
        export_materials="EXPORT", export_extras=True, export_yup=True,
        export_animations=False, export_cameras=False, export_lights=False,
    )
    if "FINISHED" not in result or not path.is_file() or path.stat().st_size < 100:
        raise RuntimeError("GLB export failed: " + str(path))
    bpy.ops.object.select_all(action="DESELECT")


def add_studio(builder, resolution, samples):
    scene = bpy.context.scene
    studio = bpy.data.collections.new("STUDIO_RENDER_ONLY")
    scene.collection.children.link(studio)
    bounds = collection_bounds(builder.objects)
    dims = bounds["dimensions"]
    center = Vector([(a+b)*.5 for a,b in zip(bounds["min"], bounds["max"])])
    span = max(dims)
    camera_data = bpy.data.cameras.new("Studio camera")
    camera = bpy.data.objects.new("Studio camera", camera_data)
    studio.objects.link(camera)
    camera.location = center + Vector((span*1.02, -span*1.4, span*.64))
    camera.rotation_euler = (center-camera.location).to_track_quat("-Z", "Y").to_euler()
    camera_data.type = "ORTHO"
    camera_data.ortho_scale = span*(1.05 if builder.name == "airplane" else 1.32)
    camera_data.lens = 50
    scene.camera = camera
    for name, offset, energy, color, size in [
        ("Key", (1,-1.3,2), 90, (1,.91,.82), 1.1),
        ("Fill", (-.5,1,1), 66, (.70,.82,1), 1.0),
        ("Rim", (-1.4,-.2,1.2), 102, (.9,.95,1), .8),
    ]:
        data = bpy.data.lights.new(name, "AREA")
        data.energy = energy*span*span
        data.shape, data.size, data.color = "DISK", span*size, color
        light = bpy.data.objects.new(name, data)
        studio.objects.link(light)
        light.location = center + Vector(offset)*span
        light.rotation_euler = (center-light.location).to_track_quat("-Z", "Y").to_euler()
    floor_mat = bpy.data.materials.new("Studio floor")
    floor_mat.diffuse_color = (.055,.066,.084,1)
    floor = builder.box("studio_floor", (0,0,bounds["min"][2]-.026), (span*200,span*200,.05), floor_mat)
    builder.collection.objects.unlink(floor)
    studio.objects.link(floor)
    floor.parent = None
    builder.objects.remove(floor)
    scene.world = bpy.data.worlds.new("Studio world")
    scene.world.use_nodes = True
    scene.world.node_tree.nodes["Background"].inputs[0].default_value = (.10,.13,.19,1)
    scene.world.node_tree.nodes["Background"].inputs[1].default_value = .22
    scene.render.engine = "CYCLES"
    scene.cycles.device = "CPU"
    scene.cycles.samples = samples
    # CPU-only distribution builds may omit OpenImageDenoise entirely.
    scene.cycles.use_denoising = False
    scene.render.resolution_x = resolution
    scene.render.resolution_y = int(resolution*.75)
    scene.render.resolution_percentage = 100
    scene.render.image_settings.file_format = "PNG"
    scene.render.threads_mode = "FIXED"
    scene.render.threads = min(8, os.cpu_count() or 2)
    scene.view_settings.view_transform = "AgX"
    return studio


def sha256(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def asset_builders():
    return {"enduro": build_enduro, "helicopter": build_helicopter,
            "airplane": build_airplane, "offroader": build_offroader}


def generate_asset(name, options):
    directory = options.output / name
    directory.mkdir(parents=True, exist_ok=True)
    # --force replaces only the generator's fixed filenames, preserving other files.
    # Remove the old receipt first so a failed run cannot appear complete.
    if options.force:
        (directory / "manifest.json").unlink(missing_ok=True)
        for level in range(options.lods, 3):
            (directory / (name + "_LOD" + str(level) + ".glb")).unlink(missing_ok=True)
        if not options.render:
            (directory / (name + "_preview.png")).unlink(missing_ok=True)
    bpy.ops.wm.read_factory_settings(use_empty=True)
    scene = bpy.context.scene
    scene.unit_settings.system, scene.unit_settings.scale_length = "METRIC", 1
    scene.render.fps = 30
    builder = Builder(name, options.texture_size, options.seed)
    started = time.monotonic()
    print("BUILDING", name, flush=True)
    metadata = asset_builders()[name](builder)
    prepare_geometry(builder)
    bpy.context.view_layer.update()
    bounds = collection_bounds(builder.objects)
    lods = [export_lod(builder, directory, level, ratio)
            for level, ratio in enumerate((1., .48, .20)[:options.lods])]
    if len(lods)>1 and not all(a["triangles"] >= b["triangles"] for a,b in zip(lods,lods[1:])):
        raise RuntimeError("LOD triangle counts increased")
    collision_objects = list(builder.collision_collection.objects)
    if collision_objects:
        export_glb(directory/(name+"_collision.glb"), collision_objects)
        for obj in collision_objects:
            obj.hide_set(True)
    add_studio(builder, options.resolution, options.samples)
    scene["asset_readme"] = "Editable source visible. LOD collections hidden. Collision proxies are separate. +X forward, metres."
    bpy.ops.object.select_all(action="DESELECT")
    builder.root.select_set(True)
    bpy.context.view_layer.objects.active = builder.root
    bpy.context.preferences.filepaths.save_version = 0
    blend_path = directory / (name+".blend")
    scene.render.filepath = str(directory/(name+"_preview.png"))
    bpy.ops.wm.save_as_mainfile(filepath=str(blend_path))
    if options.render:
        print("RENDERING", name, flush=True)
        bpy.ops.render.render(write_still=True)
    files = [directory/lod["file"] for lod in lods] + [blend_path]
    if collision_objects:
        files.append(directory/(name+"_collision.glb"))
    if options.render:
        files.append(directory/(name+"_preview.png"))
    manifest = {
        "schema_version": 1, "asset": name, "generator": "generate_vehicles.py",
        "generator_sha256": GENERATOR_SHA256,
        "blender_version": bpy.app.version_string, "seed": options.seed,
        "license": "MIT", "provenance": "Original procedural geometry and generated micro-surface textures; no external model or image sources.",
        "request": "Enduro-Motorrad, Helikopter, Flugzeug und 4x4-Gelaendewagen als detaillierte Python-generierte Grundlage fuer spaetere AAA-Spiele.",
        "design_exclusions": "No logos, third-party meshes, paid generation or claim of engine certification.",
        "units": "metres", "source_axes": {"forward":"+X","left":"+Y","up":"+Z"},
        "glb_axes": {"forward":"+X","left":"-Z","up":"+Y"},
        "source_bounds": bounds, "lods": lods, "texture_size": options.texture_size,
        "source_mesh_objects": sum(obj.type == "MESH" for obj in builder.objects),
        "collision_proxies": len(collision_objects), "design": metadata,
        "limitations": ["Procedural production starting point, not AAA acceptance certification.",
                         "Automatic UVs and decimation require art review; texture detail is generic surface grain.",
                         "No baked high-poly normal/AO set, authored wear, lightmap UV2, skeletal rig, physics or animation clips.",
                         "LOD selection and collision assignment require target-engine setup; GLB has no universal LOD or collision convention.",
                         "No target-engine import or performance test has been performed by the generator."],
        "files": [{"path": path.name, "bytes": path.stat().st_size, "sha256": sha256(path)} for path in files],
        "generation_seconds": round(time.monotonic()-started,2),
    }
    (directory/"manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False)+"\n", encoding="utf-8")
    print("COMPLETE", name, json.dumps(lods), flush=True)
    return manifest


def main():
    builders = asset_builders()
    parser = argparse.ArgumentParser(prog="generate_vehicles.py", description="Blender: Enduro, Helikopter, Flugzeug und 4x4 in Metern generieren, mit PBR, UVs und LODs.")
    parser.add_argument("--asset", choices=("all", "both", *builders), default="all",
                        help="all: alle Modelle (Standard); both: Enduro und Helikopter wie bisher")
    parser.add_argument("--output", type=Path, default=Path("generated"))
    parser.add_argument("--lods", type=int, choices=(1,2,3), default=3, help="Anzahl Detailstufen (LOD0 bis LOD2)")
    parser.add_argument("--texture-size", type=int, choices=(128,256,512,1024), default=256)
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--render", action="store_true", help="Studio-Vorschaubilder mit Cycles CPU rendern")
    parser.add_argument("--resolution", type=int, default=1200)
    parser.add_argument("--samples", type=int, default=64)
    parser.add_argument("--force", action="store_true", help="Bekannte Ausgabedateien in bestehenden Asset-Ordnern ueberschreiben")
    arguments = sys.argv[sys.argv.index("--")+1:] if "--" in sys.argv else []
    options = parser.parse_args(arguments)
    if not bpy.app.background:
        parser.error("Bitte im Hintergrund starten; siehe README. So bleibt eine offene Blender-Sitzung geschuetzt.")
    if bpy.app.version < (4,3,0):
        parser.error("Blender 4.3 oder neuer wird benoetigt.")
    if not 64 <= options.resolution <= 8192 or not 1 <= options.samples <= 1024:
        parser.error("--resolution muss 64..8192 und --samples muss 1..1024 sein.")
    options.output = options.output.resolve()
    if options.asset == "all":
        names = tuple(builders)
    elif options.asset == "both":
        names = ("enduro", "helicopter")
    else:
        names = (options.asset,)
    for name in names:
        directory = options.output/name
        if directory.exists() and any(directory.iterdir()) and not options.force:
            parser.error(str(directory)+" ist nicht leer. Neues Ziel oder --force verwenden.")
    for name in names:
        generate_asset(name, options)



# Enduro geometry: original, unbranded procedural design in meters.
def build_enduro(b):
    """Construct a detailed off-road motorcycle; X forward, Z up."""
    rubber = b.mat('enduro_tire_rubber', (0.018, 0.021, 0.023), roughness=0.83)
    treadmat = b.mat('enduro_tread_rubber', (0.026, 0.029, 0.031), roughness=0.88)
    plastic = b.mat('enduro_orange_polymer', (0.94, 0.115, 0.012), roughness=0.29)
    white = b.mat('enduro_ivory_polymer', (0.82, 0.86, 0.86), roughness=0.32)
    black = b.mat('enduro_black_polymer', (0.018, 0.022, 0.026), roughness=0.39)
    seatmat = b.mat('enduro_seat_grip', (0.025, 0.029, 0.032), roughness=0.89)
    aluminum = b.mat('enduro_satin_alloy', (0.47, 0.52, 0.56), metallic=0.83, roughness=0.31)
    chrome = b.mat('enduro_chrome', (0.72, 0.77, 0.80), metallic=1.0, roughness=0.15)
    darkmetal = b.mat('enduro_graphite_metal', (0.075, 0.085, 0.091), metallic=0.75, roughness=0.36)
    gold = b.mat('enduro_fork_anodized', (0.49, 0.31, 0.10), metallic=0.86, roughness=0.28)
    steel = b.mat('enduro_brake_steel', (0.35, 0.39, 0.41), metallic=0.92, roughness=0.28)
    hotmetal = b.mat('enduro_exhaust_titanium', (0.33, 0.28, 0.24), metallic=0.91, roughness=0.33)
    lens = b.mat('enduro_headlamp_lens', (0.65, 0.81, 0.84), metallic=0.1, roughness=0.16)
    red = b.mat('enduro_tail_lens', (0.51, 0.006, 0.003), roughness=0.24)

    def group(obj, pivot):
        b.parent(obj, pivot)
        return obj

    def slab(name, points, y, thickness, mat, bevel=0.004):
        # Extruded side silhouette; vertices are (x,z).
        n = len(points)
        verts = [(x, y-thickness/2, z) for x,z in points]
        verts += [(x, y+thickness/2, z) for x,z in points]
        faces = [tuple(reversed(range(n))), tuple(range(n,2*n))]
        faces += [(i,(i+1)%n,(i+1)%n+n,i+n) for i in range(n)]
        return b.mesh(name, verts, faces, mat, bevel=bevel)

    def skin(name, stations, mat, bevel=0.004):
        # Eight-sided transverse sections, (x, bottom, top, half-width).
        verts=[]
        for x, low, high, w in stations:
            dz=min(0.012,(high-low)*0.22)
            verts.extend([(x,-w*.78,low),(x,-w,low+dz),(x,-w,high-dz),
                          (x,-w*.80,high),(x,w*.80,high),(x,w,high-dz),
                          (x,w,low+dz),(x,w*.78,low)])
        faces=[tuple(reversed(range(8)))]
        for j in range(len(stations)-1):
            faces.extend([(j*8+i,j*8+(i+1)%8,(j+1)*8+(i+1)%8,(j+1)*8+i) for i in range(8)])
        faces.append(tuple(range((len(stations)-1)*8,len(stations)*8)))
        return b.mesh(name,verts,faces,mat,bevel=bevel)

    def blockmesh(name, blocks, mat):
        verts=[]; faces=[]
        for center,axes,half in blocks:
            start=len(verts)
            for sx,sy,sz in [(-1,-1,-1),(1,-1,-1),(1,1,-1),(-1,1,-1),
                             (-1,-1,1),(1,-1,1),(1,1,1),(-1,1,1)]:
                verts.append(tuple(center[k]+sx*half[0]*axes[0][k]+sy*half[1]*axes[1][k]+sz*half[2]*axes[2][k] for k in range(3)))
            faces.extend([tuple(start+i for i in f) for f in [(0,3,2,1),(4,5,6,7),(0,1,5,4),(1,2,6,5),(2,3,7,6),(3,0,4,7)]])
        return b.mesh(name,verts,faces,mat,bevel=0.0014)

    def rotor(name,cx,cz,y,r,pivot):
        # Closed wave rotor with genuine ventilation slots in the outer band.
        n=96; radii=(r*.55,r*.72,r*.84,r)
        verts=[]
        for yy in (y-.0017,y+.0017):
            for ri,rr in enumerate(radii):
                for i in range(n):
                    a=math.tau*i/n
                    wave=1.0-(.035*(.5+.5*math.cos(a*12)) if ri==3 else 0)
                    verts.append((cx+rr*wave*math.sin(a),yy,cz+rr*wave*math.cos(a)))
        cells=[]
        for j in range(3):
            for i in range(n):
                if j==1 and i%8 in (2,3):
                    continue
                cells.append((j*n+i,j*n+(i+1)%n,(j+1)*n+(i+1)%n,(j+1)*n+i))
        faces=[]; edges={}
        offset=4*n
        for face in cells:
            faces.append(tuple(reversed(face)))
            faces.append(tuple(i+offset for i in face))
            for u,v in zip(face,face[1:]+face[:1]):
                key=tuple(sorted((u,v)))
                edges[key]=edges.get(key,0)+1
        for (u,v),count in edges.items():
            if count==1: faces.append((u,v,v+offset,u+offset))
        group(b.mesh(name,verts,faces,steel),pivot)
        for i in range(6):
            a=math.tau*i/6
            pa=(cx+math.sin(a)*r*.22,y,cz+math.cos(a)*r*.22)
            pb=(cx+math.sin(a+.18)*r*.64,y,cz+math.cos(a+.18)*r*.64)
            group(b.cylinder(name+'_carrier_%02d'%i,pa,pb,.009,darkmetal,vertices=8),pivot)
            bolt=(cx+math.sin(a)*r*.51,y,cz+math.cos(a)*r*.51)
            group(b.cylinder(name+'_rivet_%02d'%i,(bolt[0],y-.004,bolt[2]),(bolt[0],y+.004,bolt[2]),.005,chrome,vertices=8),pivot)

    steering=b.empty('enduro_steering_pivot',(.345,0,1.04))
    steering.rotation_euler[1]=-.49
    wheels={}
    for name,cx,cz,major,minor,rim,width,front in [
            ('front',.79,.357,.299,.045,.263,.093,True),
            ('rear',-.69,.337,.268,.055,.232,.122,False)]:
        pivot=b.empty('enduro_'+name+'_wheel_pivot',(cx,0,cz))
        if front: b.parent(pivot,steering)
        wheels[name]=pivot
        group(b.torus(name+'_tire',(cx,0,cz),major,minor,rubber,major_segments=96,minor_segments=20),pivot)
        # Raised concentric molding ribs and proper separate bead/rim rings.
        for side in (-1,1):
            for rad in (major-.020,major+.005):
                group(b.torus(name+'_sidewall_rib',(cx,side*minor*.84,cz),rad,.0018,rubber,major_segments=80,minor_segments=6),pivot)
            group(b.torus(name+'_rim_lip',(cx,side*width*.30,cz),rim,.011,aluminum,major_segments=80,minor_segments=10),pivot)
        group(b.torus(name+'_rim_well',(cx,0,cz),rim-.004,.019,darkmetal,major_segments=80,minor_segments=10),pivot)
        group(b.cylinder(name+'_hub',(cx,-.047,cz),(cx,.047,cz),.039,aluminum,vertices=32),pivot)
        for sy in (-.041,.041):
            group(b.cylinder(name+'_hub_flange',(cx,sy-.004,cz),(cx,sy+.004,cz),.055,aluminum,vertices=32),pivot)
        group(b.cylinder(name+'_axle',(cx,-.105,cz),(cx,.105,cz),.012,steel,vertices=16),pivot)
        # Cross-laced spokes; alternate hub flanges and angular offset.
        for i in range(36):
            a=math.tau*i/36
            side=1 if i%2 else -1
            for lacing in (-1,1):
                ah=a+lacing*.26
                p0=(cx+.048*math.sin(ah),side*.043,cz+.048*math.cos(ah))
                p1=(cx+(rim-.008)*math.sin(a),side*.020,cz+(rim-.008)*math.cos(a))
                group(b.cylinder(name+'_spoke_%02d_%s'%(i,'a' if lacing<0 else 'b'),p0,p1,.0015,chrome,vertices=6),pivot)
        # Individual knobby tread blocks combined in one mesh per wheel.
        blocks=[]
        for row in (-1,0,1):
            for i in range(40 if front else 42):
                n=40 if front else 42
                a=math.tau*(i+(0.5 if row else 0))/n
                angle=row*.61
                radial=major+minor*math.cos(angle)+.004
                y=minor*math.sin(angle)
                rad=(math.sin(a),0,math.cos(a)); tan=(math.cos(a),0,-math.sin(a)); across=(0,1,0)
                center=(cx+radial*rad[0],y,cz+radial*rad[2])
                blocks.append((center,(tan,across,rad),(.014 if front else .016,.013 if row else .019,.011)))
        group(blockmesh(name+'_knobby_tread',blocks,treadmat),pivot)
        rotor(name+'_brake_disc',cx,cz,-.068,.132 if front else .108,pivot)
        if not front:
            group(b.cylinder('rear_sprocket_core',(cx,.071,cz),(cx,.078,cz),.106,darkmetal,vertices=48),pivot)
            teeth=[]
            for i in range(44):
                a=math.tau*i/44
                teeth.append(((cx+.111*math.sin(a),.076,cz+.111*math.cos(a)),
                              ((math.cos(a),0,-math.sin(a)),(0,1,0),(math.sin(a),0,math.cos(a))),(.005,.004,.009)))
            group(blockmesh('rear_sprocket_teeth',teeth,steel),pivot)
            for i in range(6):
                a=math.tau*i/6
                group(b.cylinder('sprocket_fastener',(cx+.062*math.sin(a),.077,cz+.062*math.cos(a)),(cx+.062*math.sin(a),.085,cz+.062*math.cos(a)),.007,chrome,vertices=6),pivot)

    # Frame: welded double cradle and triangulated subframe.
    for side in (-1,1):
        y=side*.083
        framepaths=[[(.34,y,1.00),(.22,y,.81),(-.18,y,.55),(-.15,y,.39),(.16,y,.37),(.29,y,.60),(.34,y,1.00)],
                    [(.31,y,.91),(-.23,y,.79),(-.18,y,.55)],
                    [(-.18,y,.55),(-.47,y,.86),(-.76,y,.89)],
                    [(-.18,y,.55),(-.65,y,.78),(-.76,y,.89)]]
        for i,pts in enumerate(framepaths): b.tube('frame_rail_%s_%d'%(side,i),pts,.017 if i<2 else .012,plastic)
        # Substantial tapered box-section swingarm.
        swing=slab('swingarm_%s'%side,[(-.12,.51),(-.72,.395),(-.73,.30),(-.51,.31),(-.13,.43)],side*.112,.043,aluminum,.008)
        b.cylinder('swingarm_pivot_%s'%side,(-.12,side*.095,.477),(-.12,side*.147,.477),.029,darkmetal,vertices=24)
        b.box('axle_adjuster_%s'%side,(-.68,side*.143,.342),(.080,.019,.037),darkmetal,bevel=.004)
        b.cylinder('rear_axle_nut_%s'%side,(-.69,side*.145,.337),(-.69,side*.16,.337),.017,chrome,vertices=6)
    b.cylinder('steering_head',(.35,0,.96),(.285,0,1.065),.043,darkmetal,vertices=32)
    # Rear monoshock with a real helical spring.
    shock_a=(-.31,0,.48); shock_b=(-.15,0,.84)
    b.cylinder('rear_shock_damper',shock_a,shock_b,.021,chrome,vertices=24)
    springpts=[]
    axis=Vector(shock_b)-Vector(shock_a)
    tangent=axis.normalized(); u=Vector((0,1,0)); v=tangent.cross(u).normalized()
    for i in range(177):
        t=i/176; a=math.tau*8*t
        p=Vector(shock_a)+axis*t+.041*(math.cos(a)*u+math.sin(a)*v)
        springpts.append(tuple(p))
    b.tube('rear_shock_coil',springpts,.0055,white,resolution=2)
    for point in (shock_a,shock_b): b.sphere('shock_eye',point,(.030,.022,.030),darkmetal,segments=16,rings=8)

    # Compact single-cylinder four-stroke engine and its machined covers.
    b.box('engine_crankcase',(-.015,0,.49),(.32,.22,.22),darkmetal,bevel=.045)
    b.cylinder('clutch_cover',(-.005,-.132,.49),(-.005,-.100,.49),.117,aluminum,vertices=40)
    b.cylinder('ignition_cover',(-.045,.100,.49),(-.045,.139,.49),.096,darkmetal,vertices=40)
    b.cylinder('clutch_center',(-.005,-.139,.49),(-.005,-.133,.49),.074,darkmetal,vertices=32)
    for side in (-1,1):
        for i in range(9):
            a=math.tau*i/9
            x=-.015+.096*math.sin(a); z=.49+.096*math.cos(a)
            b.cylinder('crankcase_bolt',(x,side*.139,z),(x,side*.146,z),.0055,chrome,vertices=6)
    b.box('cylinder_block',(.075,0,.658),(.145,.167,.19),darkmetal,bevel=.012,rotation=(0,-.20,0))
    for i in range(8):
        z=.589+i*.019
        b.box('cooling_fin_%02d'%i,(.085+(z-.66)*.18,0,z),(.168,.189,.007),aluminum,bevel=.003,rotation=(0,-.20,0))
    b.box('cylinder_head',(.096,0,.778),(.185,.178,.063),aluminum,bevel=.013,rotation=(0,-.20,0))
    b.box('valve_cover',(.097,0,.816),(.167,.160,.025),darkmetal,bevel=.008)
    b.tube('spark_plug_cable',[(.10,0,.826),(.05,.026,.875),(-.08,.05,.84)],.007,black)
    b.cylinder('throttle_body',(-.04,0,.72),(-.18,0,.75),.031,aluminum,vertices=24)
    b.box('airbox',(-.27,0,.782),(.18,.17,.16),black,bevel=.025)
    # Skid plate keeps the engine visibly protected.
    b.box('engine_skid_plate',(.035,0,.356),(.36,.25,.018),aluminum,bevel=.009,rotation=(0,-.06,0))
    for side in (-1,1):
        slab('skid_plate_wing',[(-.15,.36),(-.14,.43),(.18,.43),(.22,.37)],side*.127,.012,aluminum)

    # Radiators and separate fine cooling vanes behind the side shrouds.
    for side in (-1,1):
        b.box('radiator_%s'%side,(.25,side*.121,.784),(.108,.046,.24),darkmetal,bevel=.008,rotation=(0,-.19,0))
        for i in range(11):
            b.box('radiator_fin_%s_%02d'%(side,i),(.303,side*.124,.681+i*.019),(.008,.052,.006),aluminum,bevel=.001)
        b.tube('coolant_hose_%s'%side,[(.25,side*.12,.67),(.24,side*.10,.61),(.12,side*.105,.60)],.011,black)

    # Fuel tank, long grippy saddle, angular injection-molded shrouds.
    skin('fuel_tank',[(-.17,.79,.93,.085),(.09,.81,1.015,.113),(.30,.85,1.005,.094),(.36,.88,.94,.057)],black,.01)
    b.cylinder('fuel_filler',(.17,0,1.005),(.17,0,1.036),.035,darkmetal,vertices=24)
    b.box('fuel_cap_grip',(.17,0,1.038),(.050,.016,.011),black,bevel=.003)
    skin('seat',[(-.76,.885,.932,.087),(-.60,.893,.953,.108),(-.28,.899,.973,.105),(.045,.931,.998,.075),(.15,.969,1.011,.056)],seatmat,.008)
    for i in range(15):
        x=-.60+i*.043
        z=.954+(x+.60)*.063
        b.tube('seat_traction_rib_%02d'%i,[(x,-.085,z),(x,.085,z)],.0025,black,resolution=1)
    for side in (-1,1):
        y=side*.128
        slab('radiator_shroud_%s'%side,[(.37,.96),(.20,1.003),(-.01,.95),(-.10,.81),(.12,.665),(.30,.72)],y,.021,plastic,.005)
        slab('shroud_white_inlay_%s'%side,[(.30,.951),(.19,.976),(.005,.934),(-.055,.831),(.024,.804),(.122,.887)],y+side*.013,.003,white,.001)
        slab('shroud_graphite_inlay_%s'%side,[(.295,.826),(.275,.744),(.135,.692),(.047,.767)],y+side*.013,.003,black,.001)
        slab('rear_number_panel_%s'%side,[(-.77,.895),(-.35,.931),(-.22,.828),(-.43,.678),(-.69,.733)],side*.124,.019,white,.005)
        slab('rear_panel_accent_%s'%side,[(-.74,.873),(-.40,.899),(-.31,.838),(-.56,.818)],side*.136,.003,plastic,.001)
        for x,z in [(.25,.937),(.00,.82),(-.65,.853),(-.37,.788)]:
            b.cylinder('panel_fastener',(x,side*.139,z),(x,side*.146,z),.0065,steel,vertices=8)

    skin('rear_fender',[(-1.02,.897,.921,.048),(-.91,.909,.943,.087),(-.65,.885,.927,.108),(-.42,.882,.918,.086)],plastic,.005)
    b.box('tail_lamp',(-.987,0,.891),(.045,.101,.026),red,bevel=.009,rotation=(0,-.14,0))
    b.box('rear_mud_flap',(-1.001,0,.823),(.021,.083,.128),black,bevel=.004,rotation=(0,-.22,0))
    # Header follows the front of the motor, then exits into a tapered silencer.
    b.tube('exhaust_header',[(.173,-.044,.759),(.245,-.07,.74),(.283,-.098,.65),(.246,-.145,.53),(.13,-.16,.455),(-.10,-.17,.50),(-.35,-.17,.69),(-.54,-.165,.80)],.024,hotmetal,resolution=3)
    b.cylinder('exhaust_silencer',(-.53,-.172,.807),(-.89,-.172,.882),.061,aluminum,vertices=32,radius2=.046)
    b.cylinder('silencer_end_cap',(-.895,-.172,.883),(-.93,-.172,.889),.044,darkmetal,vertices=24,radius2=.037)
    b.cylinder('silencer_outlet',(-.932,-.172,.890),(-.940,-.172,.892),.021,black,vertices=24)
    for x,z in [(-.61,.824),(-.81,.866)]:
        b.torus('silencer_clamp',(x,-.172,z),.060,.004,steel,axis='X',major_segments=24,minor_segments=6)
    b.box('exhaust_heatshield',(-.46,-.216,.74),(.24,.016,.084),darkmetal,bevel=.012,rotation=(0,.53,0))

    # Forks: hard-anodized upper tubes, exposed stanchions and molded guards.
    for side in (-1,1):
        y=side*.103
        group(b.cylinder('fork_upper_%s'%side,(.30,y,1.118),(.57,y,.700),.0275,gold,vertices=32),steering)
        group(b.cylinder('fork_stanchion_%s'%side,(.57,y,.70),(.79,y,.36),.021,chrome,vertices=24),steering)
        group(b.cylinder('fork_dust_seal_%s'%side,(.56,y,.716),(.585,y,.677),.030,black,vertices=24),steering)
        group(b.cylinder('fork_axle_lug_%s'%side,(.758,y,.404),(.792,y,.351),.034,darkmetal,vertices=24),steering)
        group(b.cylinder('fork_guard_%s'%side,(.623,y,.602),(.766,y,.384),.027,white,vertices=24),steering)
        group(b.cylinder('fork_top_cap_%s'%side,(.289,y,1.130),(.298,y,1.116),.028,aluminum,vertices=24),steering)
    for x,z in [(.31,1.10),(.37,1.006)]:
        group(b.box('fork_triple_clamp',(x,0,z),(.073,.272,.033),darkmetal,bevel=.009,rotation=(0,-.55,0)),steering)
    group(b.box('front_brake_caliper',(.67,-.104,.422),(.076,.047,.104),darkmetal,bevel=.012,rotation=(0,-.36,0)),steering)
    b.box('rear_brake_caliper',(-.59,-.083,.391),(.072,.037,.066),darkmetal,bevel=.010)
    group(skin('front_fender',[(.34,.805,.834,.057),(.50,.813,.850,.082),(.75,.800,.841,.092),(.98,.762,.793,.075),(1.10,.721,.743,.044)],plastic,.004),steering)
    # Slim front lamp / number mask faces forward, following the steering rake.
    group(skin('headlight_mask',[(.40,.93,1.13,.082),(.458,.943,1.103,.087),(.48,.975,1.065,.062)],white,.006),steering)
    group(b.box('headlight_black_bezel',(.477,0,1.026),(.023,.117,.085),black,bevel=.012,rotation=(0,-.12,0)),steering)
    group(b.box('headlight_lens',(.491,0,1.027),(.012,.095,.060),lens,bevel=.010,rotation=(0,-.12,0)),steering)
    for sy in (-.025,.025):
        group(b.sphere('headlight_optic',(.500,sy,1.026),(.008,.020,.020),chrome,segments=16,rings=8),steering)

    # Tapered aluminum handlebar, controls, rubber grips and hand guards.
    barpts=[(.235,-.37,1.185),(.205,-.245,1.184),(.245,-.16,1.16),(.32,-.07,1.156),(.32,.07,1.156),(.245,.16,1.16),(.205,.245,1.184),(.235,.37,1.185)]
    group(b.tube('handlebar',barpts,.012,aluminum,resolution=3),steering)
    for side in (-1,1):
        group(b.cylinder('grip_%s'%side,(.218,side*.272,1.185),(.235,side*.382,1.185),.019,seatmat,vertices=24),steering)
        for i in range(9):
            y=side*(.279+i*.011)
            group(b.torus('grip_rib_%s_%02d'%(side,i),(.219+i*.0017,y,1.185),.019,.0019,black,axis='Y',major_segments=20,minor_segments=6),steering)
        group(b.box('handlebar_control_%s'%side,(.21,side*.245,1.192),(.039,.035,.036),black,bevel=.007),steering)
        group(b.tube('lever_%s'%side,[(.235,side*.235,1.197),(.285,side*.26,1.195),(.300,side*.342,1.19)],.006,aluminum,resolution=2),steering)
        group(b.sphere('lever_ball_%s'%side,(.30,side*.342,1.190),(.009,.009,.009),aluminum,segments=12,rings=6),steering)
        group(b.tube('handguard_brace_%s'%side,[(.24,side*.384,1.185),(.33,side*.376,1.17),(.36,side*.255,1.168),(.26,side*.205,1.174)],.0075,aluminum,resolution=2),steering)
        group(b.box('handguard_shell_%s'%side,(.354,side*.310,1.181),(.024,.145,.071),plastic,bevel=.014,rotation=(0,0,side*.10)),steering)
        group(b.box('bar_riser_%s'%side,(.32,side*.045,1.131),(.042,.038,.065),darkmetal,bevel=.007),steering)
    group(b.box('handlebar_crosspad',(.32,0,1.177),(.051,.14,.044),black,bevel=.014),steering)
    group(b.box('digital_instrument',(.36,0,1.139),(.066,.093,.024),darkmetal,bevel=.009,rotation=(0,-.23,0)),steering)
    group(b.box('instrument_screen',(.363,0,1.153),(.045,.066,.003),lens,bevel=.003,rotation=(0,-.23,0)),steering)
    group(b.tube('front_brake_hose',[(.21,-.23,1.20),(.39,-.17,1.13),(.49,-.14,.99),(.54,-.13,.76),(.66,-.13,.44)],.0048,black,resolution=3),steering)
    b.tube('throttle_cable',[(.21,-.24,1.18),(.25,-.18,1.10),(.22,-.09,.98),(.03,-.06,.79),(-.12,0,.75)],.004,black,resolution=3)

    # Foot controls, serrated pegs, drive chain and chain guide.
    for side in (-1,1):
        b.cylinder('footpeg_mount_%s'%side,(-.155,side*.10,.438),(-.155,side*.17,.438),.014,steel,vertices=16)
        b.box('footpeg_%s'%side,(-.155,side*.205,.438),(.09,.09,.015),darkmetal,bevel=.005)
        for x in (-.192,-.172,-.152,-.132,-.112):
            for y in (side*.172,side*.238):
                b.box('footpeg_tooth',(x,y,.449),(.009,.009,.011),steel,bevel=.001)
    b.tube('rear_brake_pedal',[(-.15,-.16,.423),(-.06,-.17,.405),(.015,-.18,.411)],.008,aluminum)
    b.box('brake_pedal_tip',(.015,-.194,.413),(.038,.039,.012),steel,bevel=.002)
    b.tube('gear_shift',[(-.13,.153,.46),(-.025,.175,.425),(.02,.18,.427)],.008,aluminum)
    b.cylinder('gear_shift_rubber',(.02,.18,.427),(.02,.217,.427),.010,rubber,vertices=16)
    # Dense articulated links follow a two-sprocket loop, all in a single mesh.
    chainpts=[]
    rear=(-.69,.337); drive=(-.14,.475); rr=.115; fr=.040
    for i in range(34):
        t=i/34
        chainpts.append((rear[0]*(1-t)+drive[0]*t,.084,(rear[1]+rr)*(1-t)+(drive[1]+fr)*t))
    for i in range(12):
        a=math.pi*i/12
        chainpts.append((drive[0]+fr*math.sin(a),.084,drive[1]+fr*math.cos(a)))
    for i in range(34):
        t=i/34
        chainpts.append((drive[0]*(1-t)+rear[0]*t,.084,(drive[1]-fr)*(1-t)+(rear[1]-rr)*t))
    for i in range(25):
        a=math.pi+math.pi*i/25
        chainpts.append((rear[0]+rr*math.sin(a),.084,rear[1]+rr*math.cos(a)))
    linkblocks=[]
    for i,p in enumerate(chainpts):
        prev=Vector(chainpts[(i-1)%len(chainpts)]); nxt=Vector(chainpts[(i+1)%len(chainpts)])
        tan=(nxt-prev).normalized(); across=Vector((0,1,0)); radial=tan.cross(across).normalized()
        for sy in (-.006,.006):
            linkblocks.append(((p[0],p[1]+sy,p[2]),(tuple(tan),tuple(across),tuple(radial)),(.0068,.0021,.0048)))
    blockmesh('drive_chain_links',linkblocks,steel)
    b.box('chain_guide',(-.51,.088,.243),(.135,.037,.047),black,bevel=.006,rotation=(0,-.06,0))
    b.cylinder('front_sprocket',(-.14,.072,.475),(-.14,.093,.475),.046,darkmetal,vertices=24)
    b.tube('rear_brake_hose',[(-.05,-.14,.46),(-.23,-.14,.47),(-.43,-.14,.42),(-.59,-.10,.40)],.004,black)
    # Side stand is retracted, with its actual mounting and foot visible.
    b.cylinder('retracted_side_stand',(-.18,.15,.43),(-.53,.16,.49),.009,steel,vertices=16)
    b.box('side_stand_foot',(-.53,.16,.49),(.049,.038,.013),darkmetal,bevel=.003)

    b.collision_box('main_body',(-.07,0,.71),(1.00,.30,.56))
    b.collision_box('front_wheel',(.79,0,.357),(.64,.16,.69))
    b.collision_box('rear_wheel',(-.69,0,.337),(.61,.18,.65))
    b.collision_box('handlebar',(.28,0,1.16),(.18,.80,.13))
    return {
        'description':'Original modern enduro motorcycle with spoked knobby wheels, drivetrain, detailed engine, suspension and separate steering/wheel pivots.',
        'moving_parts':['enduro_steering_pivot','enduro_front_wheel_pivot','enduro_rear_wheel_pivot'],
        'approximate_dimensions_m':[2.2,.82,1.23],
        'pivot_notes':'Wheel pivots rotate about local Y. Steering pivot local Z is aligned to the head-tube rake. Suspension is modeled but is not an authored kinematic rig.'
    }



def build_helicopter(b):
    """Build an original light utility helicopter in metres, nose along +X.

    The shell is closed and its glazing is a surface material, deliberately
    avoiding transparent views into an unfinished cabin. Rotor pivots are
    independent objects, ready for later engine-side animation.
    """
    ivory = b.mat('heli_ivory_paint', (0.76, 0.80, 0.78), metallic=0.25, roughness=0.28)
    graphite = b.mat('heli_graphite_paint', (0.035, 0.048, 0.052), metallic=0.32, roughness=0.30)
    orange = b.mat('heli_rescue_orange', (0.96, 0.19, 0.025), metallic=0.15, roughness=0.31)
    glass = b.mat('heli_smoked_glazing', (0.025, 0.075, 0.095), metallic=0.50, roughness=0.10)
    rubber = b.mat('heli_window_seal', (0.012, 0.016, 0.018), roughness=0.69)
    alloy = b.mat('heli_machined_alloy', (0.40, 0.45, 0.47), metallic=0.92, roughness=0.27)
    steel = b.mat('heli_dark_steel', (0.10, 0.13, 0.145), metallic=0.87, roughness=0.32)
    black = b.mat('heli_recess_black', (0.008, 0.012, 0.014), roughness=0.78)
    rotor_mat = b.mat('heli_rotor_composite', (0.055, 0.065, 0.070), metallic=0.18, roughness=0.42)
    red = b.mat('heli_port_red', (0.8, 0.015, 0.01), roughness=0.22, emission=(1.0, 0.01, 0.005))
    green = b.mat('heli_starboard_green', (0.01, 0.38, 0.12), roughness=0.22, emission=(0.01, 0.8, 0.10))
    lamp = b.mat('heli_lamp_glass', (0.88, 0.95, 1.0), metallic=0.12, roughness=0.12,
                 emission=(0.50, 0.65, 0.85))

    # Cross sections: x, half-width, vertical centre, half-height. More rings
    # around the cockpit preserve the curved windscreen silhouette at LOD0.
    stations = [
        (-2.30, .13, 1.78, .21), (-2.12, .34, 1.75, .39),
        (-1.85, .54, 1.73, .55), (-1.50, .72, 1.71, .69),
        (-1.15, .86, 1.69, .79), (-.75, .94, 1.68, .85),
        (-.35, .98, 1.67, .89), (.05, 1.00, 1.66, .91),
        (.45, 1.00, 1.65, .91), (.80, .98, 1.64, .89),
        (1.10, .95, 1.62, .86), (1.40, .89, 1.59, .80),
        (1.70, .80, 1.55, .71), (1.95, .67, 1.50, .60),
        (2.16, .53, 1.46, .48), (2.34, .36, 1.41, .34),
        (2.47, .17, 1.38, .18), (2.52, .045, 1.37, .055),
    ]
    ring_count = 64
    verts = []
    for x, width, center, height in stations:
        for j in range(ring_count):
            a = 2.0 * math.pi * j / ring_count
            verts.append((x, width * math.cos(a), center + height * math.sin(a)))
    faces = []
    for i in range(len(stations)-1):
        for j in range(ring_count):
            faces.append((i*ring_count+j, i*ring_count+(j+1)%ring_count,
                          (i+1)*ring_count+(j+1)%ring_count, (i+1)*ring_count+j))
    faces.append(tuple(reversed(range(ring_count))))
    faces.append(tuple((len(stations)-1)*ring_count+j for j in range(ring_count)))
    shell = b.mesh('helicopter_fuselage_shell', verts, faces, ivory, smooth=True)
    for mat in (graphite, orange, glass):
        shell.data.materials.append(mat)
    for i in range(len(stations)-1):
        midpoint_x = (stations[i][0]+stations[i+1][0])/2
        for j in range(ring_count):
            angle = 2*math.pi*(j+.5)/ring_count
            poly = shell.data.polygons[i*ring_count+j]
            # Low graphite belly and a narrow painted belt at the chine.
            if math.sin(angle) < -.40:
                poly.material_index = 1
            elif -.40 <= math.sin(angle) < -.19:
                poly.material_index = 2
            # Front two-part panoramic canopy; solid centre roof strip.
            elif .62 < midpoint_x < 2.36 and 0.03 < math.sin(angle) < .997:
                poly.material_index = 3
            # Side door glazing, with a solid roof and lower opaque door.
            elif -1.15 < midpoint_x < .45 and .02 < math.sin(angle) < .83:
                poly.material_index = 3

    def surface(x, angle, offset=.016):
        """A point just outside the shell for gaskets and panel seams."""
        low, high = stations[0], stations[-1]
        for left, right in zip(stations[:-1], stations[1:]):
            if left[0] <= x <= right[0]:
                low, high = left, right
                break
        t = max(0., min(1., (x-low[0])/(high[0]-low[0])))
        width = low[1]*(1-t)+high[1]*t
        center = low[2]*(1-t)+high[2]*t
        height = low[3]*(1-t)+high[3]*t
        return (x, (width+offset)*math.cos(angle), center+(height+offset)*math.sin(angle))

    # Actual rounded gaskets around the windscreen and cabin glazing.
    for side in (1, -1):
        def side_angle(theta):
            return theta if side == 1 else math.pi-theta
        edge_x = [.62, .80, 1.10, 1.40, 1.70, 1.95, 2.16, 2.34]
        b.tube('windscreen_lower_gasket', [surface(x, side_angle(.025)) for x in edge_x], .018, rubber)
        b.tube('windscreen_centre_mullion', [surface(x, side_angle(math.pi/2-.028)) for x in edge_x], .020, ivory)
        b.tube('windscreen_rear_pillar', [surface(.62, side_angle(j*math.pi/32)) for j in range(17)], .026, ivory)
        b.tube('windscreen_front_gasket', [surface(2.34, side_angle(j*math.pi/32)) for j in range(17)], .015, rubber)
        # Two door windows separated by a visible B pillar.
        for xa, xb in ((-1.15, -.35), (-.35, .45)):
            points = [surface(xa, side_angle(t)) for t in (.025, .25, .50, .75, .98)]
            points += [surface(x, side_angle(.98)) for x in (xa+(xb-xa)/3, xa+2*(xb-xa)/3, xb)]
            points += [surface(xb, side_angle(t)) for t in (.75, .50, .25, .025)]
            points += [surface(x, side_angle(.025)) for x in (xb-((xb-xa)/3), xa+((xb-xa)/3), xa)]
            b.tube('door_window_gasket', points, .018, rubber)
        b.tube('door_centre_pillar', [surface(-.35, side_angle(t)) for t in (0., .25, .5, .75, .98)], .030, ivory)
        for xa, xb in ((-1.15, -.35), (-.35, .50)):
            perimeter = [surface(xa, side_angle(t), .006) for t in (-.43, -.2, .1, .4, .7, 1.0)]
            perimeter += [surface(x, side_angle(1.0), .006) for x in (xa+(xb-xa)/2, xb)]
            perimeter += [surface(xb, side_angle(t), .006) for t in (.7, .4, .1, -.2, -.43)]
            perimeter += [surface(x, side_angle(-.43), .006) for x in (xa+(xb-xa)/2, xa)]
            b.tube('door_shut_line', perimeter, .006, black)
            handle_x = xb-.16
            p = surface(handle_x, side_angle(-.13), .04)
            b.box('recessed_door_handle', p, (.15, .025, .033), steel, bevel=.012)
            b.box('handle_recess', (p[0],p[1]-side*.016,p[2]), (.21,.027,.07), rubber, bevel=.025)
        # Fine hinges, body fasteners and a step under each rear door.
        for z in (1.31, 1.63):
            p = surface(-1.12, side_angle((z-1.69)/.79), .025)
            b.box('door_hinge', p, (.065,.035,.12), alloy, bevel=.01)
        b.tube('cabin_access_step', [(-.9,side*.88,.90),(-.9,side*1.17,.63),(.05,side*1.17,.63),(.05,side*.96,.90)], .025, steel)
        b.box('anti_slip_step_pad', (-.42,side*1.17,.655), (.62,.10,.018), rubber, bevel=.007)
        # Thin rub strip and service panel seam aft of the cabin.
        b.tube('aft_service_panel', [surface(x,side_angle(t),.010) for x,t in
               [(-1.85,-.2),(-1.85,.5),(-1.50,.85),(-1.20,.70),(-1.20,-.2),(-1.85,-.2)]], .007, steel)
        for x in (-1.75,-1.45,-1.22):
            point = surface(x, side_angle(-.12), .025)
            b.sphere('service_panel_fastener', point, (.014,.009,.014), alloy, segments=8, rings=4)
        # Window wipers follow the front glazing curvature.
        b.tube('windscreen_wiper_arm', [surface(x,side_angle(t),.055) for x,t in [(2.12,.08),(1.93,.35),(1.58,.60)]], .012, steel)
        b.tube('windscreen_wiper_blade', [surface(x,side_angle(.60),.060) for x in (1.35,1.58,1.85)], .017, rubber)

    # Rounded engine cowling and open-looking slatted intakes.
    b.sphere('transmission_cowling', (-.72,0,2.55), (.94,.56,.47), ivory, segments=40, rings=20)
    b.box('engine_upper_spine', (-.97,0,2.83), (1.18,.48,.21), graphite, bevel=.10)
    for side in (-1,1):
        b.box('engine_intake_recess', (-1.15,side*.48,2.60), (.65,.055,.22), black, bevel=.055)
        for i in range(9):
            b.box('engine_intake_louvre', (-1.42+i*.068,side*.518,2.60), (.023,.020,.19), steel, bevel=.006,
                  rotation=(0,.22,0))
        # Exhausts point aft; inner dark discs sit behind metal lips.
        b.cylinder('turbine_exhaust_outer', (-1.45,side*.36,2.70), (-1.90,side*.42,2.60), .135, steel, vertices=32)
        b.cylinder('turbine_exhaust_throat', (-1.903,side*.42,2.60), (-1.913,side*.422,2.598), .105, black, vertices=32)
        b.torus('exhaust_rim',(-1.905,side*.42,2.60),.120,.012,alloy,axis='X',major_segments=32,minor_segments=8)
        for x in (-1.60,-1.25,-.90,-.55):
            b.sphere('cowling_quick_release', (x,side*.38,2.89), (.018,.018,.009), alloy, segments=10,rings=5)

    # Tapered structural tail boom with an integrated drive shaft cover.
    boom_stations = [(-2.00,.37,1.79),(-2.65,.28,1.92),(-3.45,.215,2.10),
                     (-4.30,.17,2.31),(-5.10,.135,2.50),(-5.90,.105,2.69),(-6.12,.09,2.73)]
    bv, bf = [], []
    for x,r,z in boom_stations:
        for j in range(32):
            t=2*math.pi*j/32
            bv.append((x,r*math.cos(t),z+r*.87*math.sin(t)))
    for i in range(len(boom_stations)-1):
        for j in range(32):
            bf.append((i*32+j,i*32+(j+1)%32,(i+1)*32+(j+1)%32,(i+1)*32+j))
    bf += [tuple(reversed(range(32))),tuple((len(boom_stations)-1)*32+j for j in range(32))]
    boom=b.mesh('tail_boom',bv,bf,ivory,smooth=True)
    boom.data.materials.append(orange)
    for poly in boom.data.polygons:
        cx=sum(boom.data.vertices[index].co.x for index in poly.vertices)/len(poly.vertices)
        if -5.1 < cx < -4.3:
            poly.material_index=1
    b.tube('tail_drive_shaft_cover',[(x,0,z+r*.88+.027) for x,r,z in boom_stations],.035,graphite)
    for x,r,z in boom_stations[1:-1]:
        # A fine ring marks removable tail section joints.
        points=[(x,r*1.012*math.cos(j*2*math.pi/40),z+r*.88*math.sin(j*2*math.pi/40)) for j in range(41)]
        b.tube('tail_boom_joint',points,.006,steel)

    # A symmetric aerofoil extrusion helper creates real closed solids.
    def fin(name,outline,thickness,material):
        # Outline points are X,Z; thickness extends on Y.
        vertices=[(x,y,z) for y in (-thickness/2,thickness/2) for x,z in outline]
        n=len(outline)
        polys=[tuple(reversed(range(n))),tuple(n+i for i in range(n))]
        polys += [(i,(i+1)%n,n+(i+1)%n,n+i) for i in range(n)]
        return b.mesh(name,vertices,polys,material,bevel=.018)
    fin('vertical_stabilizer',[(-6.22,2.25),(-5.62,2.48),(-5.55,3.53),(-5.97,3.98),(-6.28,3.94)],.12,ivory)
    fin('vertical_fin_orange_tip',[(-6.281,3.64),(-5.65,3.64),(-5.97,3.98),(-6.28,3.94)],.125,orange)
    fin('lower_tail_fin',[(-6.20,2.66),(-5.63,2.48),(-5.78,1.92),(-6.12,1.83)],.09,graphite)
    # Swept horizontal stabilizer with slightly tapered chord.
    hverts=[(-4.25,-1.18,2.22),(-3.62,-1.18,2.22),(-3.45,0,2.26),(-4.20,0,2.26),
            (-4.25,1.18,2.22),(-3.62,1.18,2.22),(-3.45,0,2.30),(-4.20,0,2.30)]
    # Separate closed halves prevent a non-manifold centre seam.
    for side in (-1,1):
        vertices=[(-4.2,0,2.25),(-3.45,0,2.25),(-3.62,side*1.18,2.21),(-4.25,side*1.18,2.21),
                  (-4.2,0,2.32),(-3.45,0,2.32),(-3.62,side*1.18,2.27),(-4.25,side*1.18,2.27)]
        b.mesh('horizontal_stabilizer',vertices,[(0,3,2,1),(4,5,6,7),(0,1,5,4),(1,2,6,5),(2,3,7,6),(3,0,4,7)],ivory,bevel=.015)
        b.box('stabilizer_end_cap',(-3.95,side*1.18,2.25),(.54,.035,.09),orange,bevel=.015)
    b.sphere('tail_beacon',(-6.06,0,3.99),(.085,.065,.05),red,segments=20,rings=10)

    # Skids and their swept tubular attachments carry the cabin above ground.
    for side in (-1,1):
        y=side*1.08
        points=[(-2.12,y,.26),(-1.95,y,.14),(-1.60,y,.105),(1.72,y,.105),(1.98,y,.18),(2.12,y,.35)]
        b.tube('landing_skid',points,.066,graphite,resolution=3)
        for x in (-1.25,1.0):
            b.tube('landing_gear_cross_strut',[(x,0,.93),(x,side*.65,.82),(x,side*.99,.38),(x,y,.15)],.053,alloy,resolution=3)
            b.box('skid_saddle', (x,y,.15),(.15,.17,.12),steel,bevel=.025)
            b.box('landing_gear_fairing',(x,side*.49,.91),(.25,.34,.15),graphite,bevel=.065)
        for x in (-1.8,-.8,.4,1.6):
            b.box('skid_wear_plate',(x,y,.045),(.25,.095,.025),alloy,bevel=.012)

    # Main transmission, swashplate and rotor head with separate spin pivot.
    pivot=(-.30,0,3.35)
    main_rotor=b.empty('main_rotor_pivot',pivot)
    b.cylinder('main_rotor_mast',(-.30,0,2.74),(-.30,0,3.39),.066,alloy,vertices=32)
    for z,r in ((2.94,.20),(3.02,.17),(3.10,.105)):
        b.torus('swashplate',(-.30,0,z),r,.030,steel,axis='Z',major_segments=40,minor_segments=10)
    for a in (0,2*math.pi/3,4*math.pi/3):
        b.cylinder('fixed_pitch_actuator',(-.30+.25*math.cos(a),.25*math.sin(a),2.77),
                   (-.30+.17*math.cos(a),.17*math.sin(a),3.02),.018,alloy,vertices=12)
    hub=b.sphere('main_rotor_hub',pivot,(.23,.23,.105),steel,segments=32,rings=16)
    b.parent(hub,main_rotor)
    cap=b.cylinder('rotor_hub_cap',(-.30,0,3.38),(-.30,0,3.47),.10,alloy,vertices=24)
    b.parent(cap,main_rotor)

    def rotor_blade(name,azimuth,parent,center,radii,chords,vertical=False):
        # Closed NACA-like 0012 aerofoil, finite trailing-edge thickness.
        # The final outer stations sweep backwards for a modern tip profile.
        profile=[]
        samples=16
        for j in range(samples+1):
            u=.5*(1-math.cos(math.pi*j/samples))
            thickness=5*.115*(.2969*math.sqrt(u)-.1260*u-.3516*u*u+.2843*u**3-.1015*u**4)
            profile.append((u,thickness))
        for j in range(samples-1,0,-1):
            u=.5*(1-math.cos(math.pi*j/samples))
            thickness=5*.115*(.2969*math.sqrt(u)-.1260*u-.3516*u*u+.2843*u**3-.1015*u**4)
            profile.append((u,-thickness))
        vv=[]
        for i,(radius,chord) in enumerate(zip(radii,chords)):
            fraction=(radius-radii[0])/(radii[-1]-radii[0])
            pitch=math.radians(10-8*fraction)
            sweep=max(0,fraction-.89)*chord*2.2
            for u,t in profile:
                chordwise=(u-.28)*chord+sweep
                thickness=t*chord
                yy=chordwise*math.cos(pitch)-thickness*math.sin(pitch)
                zz=chordwise*math.sin(pitch)+thickness*math.cos(pitch)
                xx=radius*math.cos(azimuth)-yy*math.sin(azimuth)
                yy2=radius*math.sin(azimuth)+yy*math.cos(azimuth)
                if vertical:
                    vv.append((center[0]+xx,center[1]+zz,center[2]+yy2))
                else:
                    vv.append((center[0]+xx,center[1]+yy2,center[2]+zz))
        ff=[]
        n=len(profile)
        for i in range(len(radii)-1):
            for j in range(n):
                ff.append((i*n+j,i*n+(j+1)%n,(i+1)*n+(j+1)%n,(i+1)*n+j))
        ff.extend([tuple(reversed(range(n))),tuple((len(radii)-1)*n+j for j in range(n))])
        obj=b.mesh(name,vv,ff,rotor_mat,smooth=True)
        obj.data.materials.append(ivory)
        obj.data.materials.append(orange)
        for i in range(len(radii)-1):
            if i>=len(radii)-3:
                for j in range(n):
                    obj.data.polygons[i*n+j].material_index=2 if i==len(radii)-2 else 1
        b.parent(obj,parent)
        return obj

    for i in range(4):
        angle=math.radians(22.5+90*i)
        direction=(math.cos(angle),math.sin(angle))
        pa=(-.30+.13*direction[0],.13*direction[1],3.35)
        pb=(-.30+.67*direction[0],.67*direction[1],3.35)
        arm=b.cylinder('main_blade_grip',pa,pb,.048,alloy,vertices=16)
        b.parent(arm,main_rotor)
        pinloc=(-.30+.39*direction[0],.39*direction[1])
        pin=b.cylinder('blade_flap_hinge',(pinloc[0],pinloc[1],3.28),(pinloc[0],pinloc[1],3.44),.047,steel,vertices=20)
        b.parent(pin,main_rotor)
        pitch_link=b.cylinder('rotating_pitch_link',(-.30+.18*direction[0],.18*direction[1],3.03),
                              (-.30+.48*direction[0]-.07*direction[1],.48*direction[1]+.07*direction[0],3.33),.013,alloy,vertices=10)
        b.parent(pitch_link,main_rotor)
        rotor_blade('main_rotor_blade',angle,main_rotor,pivot,
                    [.55,.72,1.1,2.0,3.0,4.1,4.65,4.85,5.0],
                    [.18,.30,.31,.305,.295,.28,.27,.25,.17])

    # Tail rotor is a separate Y-axis pivot offset from the fin.
    tail_center=(-5.96,.32,2.69)
    tail_rotor=b.empty('tail_rotor_pivot',tail_center)
    b.cylinder('tail_gearbox',(-5.96,-.03,2.69),(-5.96,.33,2.69),.105,steel,vertices=24)
    hub=b.sphere('tail_rotor_hub',tail_center,(.14,.065,.14),alloy,segments=24,rings=12)
    b.parent(hub,tail_rotor)
    for i in range(4):
        angle=math.radians(15+90*i)
        rotor_blade('tail_rotor_blade',angle,tail_rotor,tail_center,[.12,.25,.55,.75,.83], [.09,.12,.12,.11,.08],vertical=True)
        point=(tail_center[0]+.16*math.cos(angle),tail_center[1]+.03,tail_center[2]+.16*math.sin(angle))
        bolt=b.sphere('tail_rotor_grip_bolt',point,(.022,.015,.022),steel,segments=10,rings=5)
        b.parent(bolt,tail_rotor)
    # Tail rotor guard below the boom, a physical tube rather than a disc.
    b.tube('tail_strike_guard',[(-5.45,0,2.25),(-5.85,0,1.46),(-6.30,0,1.38)],.025,steel)

    # Small functional details establish scale without using brand markings.
    for side in (-1,1):
        b.box('navigation_light_mount',(-.85,side*.93,1.91),(.18,.08,.09),graphite,bevel=.035)
        b.sphere('navigation_light',(-.85,side*.985,1.92),(.065,.035,.035),red if side==1 else green,segments=20,rings=10)
    b.sphere('chin_searchlight_housing',(1.73,0,.98),(.17,.17,.13),graphite,segments=24,rings=12)
    b.cylinder('chin_searchlight_lens',(1.82,0,.94),(1.865,0,.925),.105,lamp,vertices=32)
    b.cylinder('belly_antenna',(-.66,0,.79),(-.96,0,.43),.014,steel,vertices=10,radius2=.005)
    b.cylinder('roof_whip_antenna',(-1.38,.18,2.93),(-1.72,.18,3.31),.009,steel,vertices=10,radius2=.003)
    b.box('gps_patch_antenna',(.25,0,2.60),(.18,.12,.035),ivory,bevel=.025)
    b.tube('pitot_probe',[(1.25,-.67,1.12),(1.43,-.76,1.00),(1.85,-.76,1.00)],.012,alloy)
    for side in (-1,1):
        b.cylinder('tow_point',(2.21,side*.20,1.12),(2.25,side*.20,1.06),.022,steel,vertices=12)

    # Engine collision uses conservative primitive hulls, exported separately.
    b.collision_box('cabin',(.05,0,1.58),(3.8,1.75,1.50))
    b.collision_box('engine',(-.80,0,2.57),(1.6,.95,.60))
    b.collision_box('tail',(-4.05,0,2.26),(4.1,.42,.46),rotation=(0,.22,0))
    b.collision_box('left_skid',(.0,1.08,.16),(4.1,.17,.25))
    b.collision_box('right_skid',(.0,-1.08,.16),(4.1,.17,.25))
    b.collision_box('vertical_fin',(-5.95,0,3.1),(.65,.16,1.75))
    return {
        'description': 'Original four-blade light utility helicopter, sealed tinted cockpit, turbine housing and skid undercarriage',
        'moving_parts': ['main_rotor_pivot: local Z rotation', 'tail_rotor_pivot: local Y rotation'],
        'dimensions_design_m': {'fuselage_and_tail_length': 8.8, 'rotor_diameter': 10.0, 'body_width': 2.0},
        'notes': ['Glazing is opaque smoked material; no finished interior.',
                  'Rotor blades must receive custom engine collision or damage handling.',
                  'Panel lines are geometry and simplified automatically in lower detail levels.']
    }


def build_airplane(b):
    """Original low-wing touring airplane, SI metres, +X forward and +Z up.

    Closed airfoil sections leave real gaps at the separately hinged controls.
    Glazing is opaque smoked material; the model has no finished interior.
    """
    ivory = b.mat('plane_ivory_paint', (.80, .83, .80), metallic=.16, roughness=.29)
    orange = b.mat('plane_signal_orange', (.96, .135, .018), metallic=.16, roughness=.30)
    graphite = b.mat('plane_graphite_paint', (.025, .040, .052), metallic=.28, roughness=.32)
    glass = b.mat('plane_smoked_glass', (.019, .055, .074), metallic=.47, roughness=.115)
    rubber = b.mat('plane_tire_rubber', (.013, .017, .019), roughness=.83)
    seal = b.mat('plane_black_seal', (.010, .014, .016), roughness=.60)
    alloy = b.mat('plane_satin_alloy', (.48, .53, .55), metallic=.92, roughness=.27)
    steel = b.mat('plane_dark_steel', (.09, .12, .14), metallic=.86, roughness=.32)
    chrome = b.mat('plane_polished_steel', (.65, .70, .72), metallic=1., roughness=.16)
    composite = b.mat('plane_propeller_composite', (.035, .042, .046), metallic=.10, roughness=.42)
    red = b.mat('plane_port_red', (.7, .01, .005), roughness=.2, emission=(1., .01, .004))
    green = b.mat('plane_starboard_green', (.005, .42, .09), roughness=.2, emission=(.01, .8, .12))
    lamp = b.mat('plane_lamp', (.80, .90, 1.), roughness=.16, emission=(.65, .82, 1.))

    # x, half-width, vertical centre, half-height. The crown rises into the
    # cabin and falls gradually into the tail cone and separate engine cowling.
    stations = [
        (-3.85, .045, 1.55, .07), (-3.65, .11, 1.52, .15),
        (-3.20, .18, 1.48, .23), (-2.60, .27, 1.43, .31),
        (-2.00, .39, 1.39, .39), (-1.50, .47, 1.40, .45),
        (-1.15, .54, 1.42, .52), (-.80, .60, 1.46, .61),
        (-.35, .65, 1.51, .72), (.10, .66, 1.54, .76),
        (.50, .65, 1.55, .75), (.85, .62, 1.52, .71),
        (1.10, .58, 1.48, .64), (1.35, .54, 1.42, .53),
        (1.60, .50, 1.36, .44), (2.00, .46, 1.33, .38),
        (2.40, .41, 1.33, .35), (2.75, .36, 1.34, .31),
        (3.05, .29, 1.34, .26), (3.28, .22, 1.34, .20),
    ]
    ring_size = 80
    vertices, faces = [], []
    for x, width, center, height in stations:
        for j in range(ring_size):
            a = math.tau * j / ring_size
            vertices.append((x, width*math.cos(a), center+height*math.sin(a)))
    for i in range(len(stations)-1):
        for j in range(ring_size):
            faces.append((i*ring_size+j, i*ring_size+(j+1)%ring_size,
                          (i+1)*ring_size+(j+1)%ring_size, (i+1)*ring_size+j))
    faces += [tuple(reversed(range(ring_size))),
              tuple((len(stations)-1)*ring_size+j for j in range(ring_size))]
    shell = b.mesh('fuselage_shell', vertices, faces, ivory, smooth=True)
    for material in (graphite, orange, glass):
        shell.data.materials.append(material)
    for i in range(len(stations)-1):
        x = .5*(stations[i][0]+stations[i+1][0])
        for j in range(ring_size):
            a = math.tau*(j+.5)/ring_size
            sine = math.sin(a)
            poly = shell.data.polygons[i*ring_size+j]
            if sine < -.39:
                poly.material_index = 1
            elif -.39 <= sine < -.16:
                poly.material_index = 2
            elif -.80 < x < .85 and .035 < sine < .94:
                poly.material_index = 3
            elif .85 < x < 1.60 and .035 < sine < .996:
                poly.material_index = 3
            elif -1.15 < x < -.80 and .12 < sine < .80:
                poly.material_index = 3
            elif 1.60 < x < 2.90 and sine > .95:
                # Anti-glare paint follows the cowling rather than floating above it.
                poly.material_index = 1

    def surface(x, angle, offset=.012):
        low, high = stations[0], stations[-1]
        for left, right in zip(stations[:-1], stations[1:]):
            if left[0] <= x <= right[0]:
                low, high = left, right
                break
        t = max(0., min(1., (x-low[0])/(high[0]-low[0])))
        width = low[1]*(1-t)+high[1]*t
        center = low[2]*(1-t)+high[2]*t
        height = low[3]*(1-t)+high[3]*t
        return (x, (width+offset)*math.cos(angle), center+(height+offset)*math.sin(angle))

    # Glazing seals lie directly on the loft, without an overlapping glass shell.
    for side, side_name in ((1, 'left'), (-1, 'right')):
        def theta(angle):
            return angle if side == 1 else math.pi-angle
        for xa, xb in ((-.80, -.35), (-.35, .85)):
            points = [surface(xa, theta(a)) for a in (.04,.30,.60,.90,1.20)]
            points += [surface(x, theta(1.20)) for x in (xa+(xb-xa)/3, xa+2*(xb-xa)/3, xb)]
            points += [surface(xb, theta(a)) for a in (.90,.60,.30,.04)]
            points += [surface(x, theta(.04)) for x in (xb-(xb-xa)/3, xa+(xb-xa)/3, xa)]
            b.tube(side_name+'_window_seal', points, .012, seal)
        b.tube(side_name+'_cabin_pillar', [surface(-.35,theta(a),.018) for a in (.02,.25,.5,.75,1.0,1.23)], .025, ivory)
        wind_x = (.85,1.10,1.35,1.60)
        b.tube(side_name+'_windscreen_lower_seal', [surface(x,theta(.04)) for x in wind_x], .014, seal)
        b.tube(side_name+'_windscreen_rear_frame', [surface(.85,theta(a)) for a in (.02,.3,.6,.9,1.2,1.55)], .021, ivory)
        b.tube(side_name+'_windscreen_front_seal', [surface(1.60,theta(a)) for a in (.02,.3,.6,.9,1.2,1.55)], .012, seal)
        b.tube(side_name+'_windscreen_center_frame', [surface(x,theta(1.54),.015) for x in wind_x], .016, ivory)
        door = [surface(-.38,theta(a),.006) for a in (-.34,-.12,.2,.5,.8,1.22)]
        door += [surface(x,theta(1.22),.006) for x in (.05,.48,.84)]
        door += [surface(.84,theta(a),.006) for a in (.9,.6,.3,0.,-.34)]
        door += [surface(x,theta(-.34),.006) for x in (.45,.05,-.38)]
        b.tube(side_name+'_door_shut_line', door, .0045, seal)
        handle = surface(-.16,theta(-.04),.032)
        b.box(side_name+'_door_handle_recess', handle, (.18,.022,.057), graphite, bevel=.016)
        b.box(side_name+'_door_handle', (handle[0],handle[1]+side*.016,handle[2]), (.12,.024,.018), alloy, bevel=.006)
        for x in (.47,.72):
            p = surface(x,theta(.92),.024)
            b.box(side_name+'_door_hinge', p, (.07,.033,.045), alloy, bevel=.008)
        b.tube(side_name+'_cabin_step', [(-.55,side*.55,.88),(-.55,side*.85,.60),(-.92,side*.85,.60)], .018, steel)
        # Cowling seam and captive fasteners suggest removable engine panels.
        b.tube(side_name+'_cowling_split', [surface(x,theta(.03),.007) for x in (1.67,2.,2.4,2.75,3.08)], .004, seal)
        b.tube(side_name+'_firewall_joint', [surface(1.68,theta(a),.005) for a in (-1.4,-1.0,-.5,0.,.5,1.,1.5)], .0045, steel)
        for x in (1.76,2.02,2.30,2.60,2.88):
            p = surface(x,theta(.12),.014)
            b.sphere(side_name+'_cowling_fastener', p, (.011,.008,.011), alloy, segments=10,rings=5)
        # Short rearward exhaust stubs sit below the engine cowling.
        b.tube(side_name+'_exhaust_pipe', [(2.05,side*.32,1.06),(1.95,side*.46,.94),(1.61,side*.50,.91)], .037, steel)
        b.cylinder(side_name+'_exhaust_dark_opening', (1.608,side*.50,.91),(1.595,side*.50,.909), .030, seal,vertices=24)
        for z in (1.32,1.39,1.46):
            b.box(side_name+'_cooling_louvre', (2.10,side*.452,z), (.30,.014,.018), graphite,bevel=.006,rotation=(0,-.12,0))

    # Chin cooling inlet: nested lip and dark throat, separated from the spinner.
    b.sphere('chin_air_intake_lip',(3.12,0,1.14),(.13,.19,.095),ivory,segments=32,rings=16)
    b.sphere('chin_air_intake_throat',(3.226,0,1.14),(.020,.143,.058),seal,segments=32,rings=16)

    def thickness(u, ratio):
        return max(0.,5*ratio*(.2969*math.sqrt(max(0.,u))-.1260*u-.3516*u*u+.2843*u**3-.1036*u**4))

    def airfoil(name, sections, start, end, material, parent=None, vertical=False, camber=.016, thickness_ratio=.12):
        # sections: span coordinate, leading-edge x, chord, section centre.
        # For horizontal surfaces centre is Z; for a fin it is centre Y.
        samples=36
        upper=[start+(end-start)*(.5-.5*math.cos(math.pi*i/samples)) for i in range(samples+1)]
        profile=[(u,1) for u in upper]
        # Omit duplicated zero-thickness leading/trailing vertices.
        lower=list(reversed(upper))
        if end == 1.: lower=lower[1:]
        if start == 0.: lower=lower[:-1]
        profile += [(u,-1) for u in lower]
        vv=[]
        for span,leading,chord,center in sections:
            for u,side in profile:
                offset=chord*(4*camber*u*(1-u)+side*thickness(u,thickness_ratio))
                vv.append((leading-u*chord,center+offset,span) if vertical else (leading-u*chord,span,center+offset))
        n=len(profile); ff=[]
        for i in range(len(sections)-1):
            ff.extend((i*n+j,i*n+(j+1)%n,(i+1)*n+(j+1)%n,(i+1)*n+j) for j in range(n))
        ff += [tuple(reversed(range(n))),tuple((len(sections)-1)*n+j for j in range(n))]
        obj=b.mesh(name,vv,ff,material,smooth=True)
        obj.data.materials.append(graphite)
        obj.data.materials.append(orange)
        for i in range(len(sections)-1):
            span=.5*(sections[i][0]+sections[i+1][0])
            for j,(_u,sign) in enumerate(profile):
                polygon=obj.data.polygons[i*n+j]
                if vertical:
                    if span>2.72: polygon.material_index=2
                elif abs(span)>4.72:
                    polygon.material_index=2
                elif sign<0 and material != orange:
                    polygon.material_index=1
        if parent is not None: b.parent(obj,parent)
        return obj

    def wing_section(y):
        fraction=(abs(y)-.52)/(5.40-.52)
        return (y,.77-.38*fraction,1.84-.76*fraction,.99+math.tan(math.radians(4.))*max(0.,abs(y)-.52))

    def hinge_pivot(name, a, z_axis=False):
        # a holds two WORLD points on the actual hinge line.
        first,last=Vector(a[0]),Vector(a[1])
        pivot=b.empty(name,tuple((first+last)*.5))
        local_axis=Vector((0,0,1) if z_axis else (0,1,0))
        pivot.rotation_mode='QUATERNION'
        pivot.rotation_quaternion=local_axis.rotation_difference((last-first).normalized())
        return pivot

    for side,side_name in ((1,'left'),(-1,'right')):
        station_y=[.52,.78,1.15,1.65,2.15,2.63,2.74,3.2,3.7,4.2,4.72,5.10,5.25,5.40]
        sections=[wing_section(side*y) for y in station_y]
        airfoil(side_name+'_wing_forward',sections,0.,.715,ivory)
        for ya,yb,label in ((.52,.80,'root'),(2.63,2.72,'control_separator'),(5.12,5.40,'tip')):
            airfoil(side_name+'_wing_fixed_trailing_'+label,[wing_section(side*y) for y in (ya,(ya+yb)*.5,yb)],.715,1.,ivory)
        for label,ya,yb in (('flap',.82,2.61),('aileron',2.74,5.10)):
            control_sections=[wing_section(side*(ya+(yb-ya)*i/6)) for i in range(7)]
            hinge=[]
            for y in (ya,yb):
                yy,leading,chord,center=wing_section(side*y)
                hinge.append((leading-.725*chord,yy,center))
            pivot=hinge_pivot(side_name+'_'+label+'_pivot',hinge)
            control=airfoil(side_name+'_'+label,control_sections,.735,1.,ivory,parent=pivot)
            for t in (.16,.50,.84):
                point=Vector(hinge[0]).lerp(Vector(hinge[1]),t)
                axis=(Vector(hinge[1])-Vector(hinge[0])).normalized()
                part=b.cylinder(side_name+'_'+label+'_hinge_pin',tuple(point-axis*.035),tuple(point+axis*.035),.014,alloy,vertices=12)
                b.parent(part,pivot)
            # Recessed access covers and wing fasteners stay on the fixed surface.
        for y in (1.35,2.20,3.15,4.10):
            yy,leading,chord,center=wing_section(side*y)
            u=.42; top=center+chord*(4*.016*u*(1-u)+thickness(u,.12))
            b.cylinder(side_name+'_wing_inspection_cover',(leading-u*chord,yy,top+.001),(leading-u*chord,yy,top+.006),.075,ivory,vertices=32)
            for i in range(4):
                angle=math.tau*i/4
                p=(leading-u*chord+.056*math.cos(angle),yy+.056*math.sin(angle),top+.009)
                b.sphere(side_name+'_inspection_screw',p,(.006,.006,.003),steel,segments=8,rings=4)
        # Walkway next to the cabin, and flush filler caps further outboard.
        yy,leading,chord,center=wing_section(side*.91)
        u=.55; z=center+chord*(4*.016*u*(1-u)+thickness(u,.12))
        b.box(side_name+'_wing_walk',(-.12,side*.85,z+.008),(.68,.24,.012),graphite,bevel=.018,rotation=(side*math.radians(4),0,0))
        yy,leading,chord,center=wing_section(side*1.58)
        u=.33; z=center+chord*(4*.016*u*(1-u)+thickness(u,.12))
        b.cylinder(side_name+'_fuel_cap',(leading-u*chord,yy,z),(leading-u*chord,yy,z+.009),.055,alloy,vertices=32)
        b.box(side_name+'_fuel_cap_handle',(leading-u*chord,yy,z+.015),(.055,.012,.008),steel,bevel=.003)
        yy,leading,chord,center=wing_section(side*5.37)
        b.sphere(side_name+'_wingtip_light_base',(leading-.30*chord,side*5.38,center+.045),(.20,.05,.057),graphite,segments=24,rings=12)
        b.sphere(side_name+'_navigation_light',(leading-.23*chord,side*5.419,center+.05),(.11,.035,.042),red if side==1 else green,segments=20,rings=10)
        b.sphere(side_name+'_wingtip_strobe',(leading-.41*chord,side*5.416,center+.045),(.042,.024,.030),lamp,segments=16,rings=8)
        # Fine rows of flush skin fasteners, placed from the same airfoil function.
        for y in (.85,1.30,1.80,2.30,2.85,3.40,3.95,4.50,4.95):
            yy,leading,chord,center=wing_section(side*y)
            for u in (.10,.67):
                top=center+chord*(4*.016*u*(1-u)+thickness(u,.12))
                b.sphere(side_name+'_wing_skin_rivet',(leading-u*chord,yy,top+.002),(.0045,.0045,.002),alloy,segments=8,rings=4)

    # Tailplane uses a straight Y hinge at X=-3.25, with tapered leading edge.
    elevator=b.empty('elevator_pivot',(-3.25,0,1.63))
    for side,side_name in ((1,'left'),(-1,'right')):
        tail_sections=[]
        for y in (.12,.40,.75,1.10,1.45,1.72,1.82):
            chord=1.0-.36*(y-.12)/(1.82-.12)
            tail_sections.append((side*y,-3.25+.725*chord,chord,1.63))
        airfoil(side_name+'_horizontal_stabilizer',tail_sections,0.,.715,ivory,camber=0.,thickness_ratio=.095)
        airfoil(side_name+'_elevator',tail_sections,.735,1.,orange,parent=elevator,camber=0.,thickness_ratio=.095)
        for y in (.40,1.15,1.65):
            b.parent(b.cylinder(side_name+'_elevator_hinge',(-3.25,side*y-.035,1.63),(-3.25,side*y+.035,1.63),.012,alloy,vertices=12),elevator)
    fin_sections=[]
    for z,chord in ((1.57,1.40),(1.80,1.27),(2.10,1.06),(2.42,.84),(2.74,.68),(2.96,.58),(3.02,.49)):
        fin_sections.append((z,-3.52+.725*chord,chord,0.))
    airfoil('vertical_stabilizer',fin_sections,0.,.715,ivory,vertical=True,camber=0.,thickness_ratio=.11)
    rudder=b.empty('rudder_pivot',(-3.52,0,2.27))
    airfoil('rudder',fin_sections,.735,1.,ivory,parent=rudder,vertical=True,camber=0.,thickness_ratio=.11)
    for z in (1.86,2.35,2.78):
        b.parent(b.cylinder('rudder_hinge',(-3.52,0,z-.035),(-3.52,0,z+.035),.012,alloy,vertices=12),rudder)
    b.sphere('tail_white_light',(-3.86,0,1.58),(.065,.043,.043),lamp,segments=16,rings=8)
    b.sphere('fin_anti_collision_beacon',(-3.28,0,3.031),(.060,.039,.031),red,segments=16,rings=8)

    # Fixed tricycle gear with exposed tyres, separate wheel pivots and fairings.
    wheel_specs=[('nose',(2.10,0,.205),.150,.055,.095),
                 ('left',(-.52,1.22,.270),.195,.075,.125),
                 ('right',(-.52,-1.22,.270),.195,.075,.125)]
    for wheel_name,center,major,minor,rim in wheel_specs:
        x,y,z=center
        pivot=b.empty(wheel_name+'_wheel_pivot',center)
        def wheel_part(obj):
            b.parent(obj,pivot)
            return obj
        wheel_part(b.torus(wheel_name+'_tire',center,major,minor,rubber,axis='Y',major_segments=80,minor_segments=20))
        for sy in (-1,1):
            wheel_part(b.torus(wheel_name+'_rim_lip',(x,y+sy*minor*.78,z),rim,.011,alloy,axis='Y',major_segments=48,minor_segments=10))
            wheel_part(b.torus(wheel_name+'_sidewall_rib',(x,y+sy*minor*.80,z),major-.010,.0025,rubber,axis='Y',major_segments=64,minor_segments=6))
            wheel_part(b.cylinder(wheel_name+'_hub',(x,y+sy*minor*.53,z),(x,y+sy*minor*.90,z),rim*.86,alloy,vertices=40))
            wheel_part(b.cylinder(wheel_name+'_axle_cap',(x,y+sy*minor*.91,z),(x,y+sy*minor*1.04,z),rim*.27,chrome,vertices=16))
            for i in range(6):
                a=math.tau*i/6
                xx=x+rim*.61*math.sin(a); zz=z+rim*.61*math.cos(a)
                wheel_part(b.cylinder(wheel_name+'_hub_bolt',(xx,y+sy*minor*.91,zz),(xx,y+sy*minor*.98,zz),.007,steel,vertices=6))
        # Shallow moulding ribs run circumferentially on the tread.
        for lateral in (-.018,0.,.018):
            radius=major+math.sqrt(max(0.,minor*minor-lateral*lateral))
            wheel_part(b.torus(wheel_name+'_tread_rib',(x,y+lateral,z),radius-.001,.0015,rubber,axis='Y',major_segments=80,minor_segments=6))
        if wheel_name=='nose':
            b.cylinder('nose_gear_strut',(2.17,0,1.05),(2.12,0,.53),.044,ivory,vertices=24)
            b.cylinder('nose_gear_oleo',(2.12,0,.63),(2.10,0,.33),.025,chrome,vertices=24)
            for sy in (-1,1):
                b.tube('nose_gear_fork',[(2.12,sy*.02,.42),(2.08,sy*.075,.33),(2.10,sy*.075,.205)],.018,alloy)
            b.sphere('nose_gear_upper_fairing',(2.10,0,.397),(.29,.092,.105),ivory,segments=32,rings=16)
        else:
            side=1 if y>0 else -1
            b.tube(wheel_name+'_main_gear_leg',[(-.20,side*.66,.99),(-.35,side*.98,.65),(-.52,side*1.15,.30)],.040,ivory)
            b.cylinder(wheel_name+'_wheel_axle',(-.52,side*1.12,.27),(-.52,side*1.32,.27),.022,steel,vertices=24)
            wheel_part(b.cylinder(wheel_name+'_brake_disc',(-.52,side*1.135,.27),(-.52,side*1.148,.27),.105,steel,vertices=40))
            b.box(wheel_name+'_brake_caliper',(-.45,side*1.135,.28),(.06,.04,.10),steel,bevel=.012)
            b.tube(wheel_name+'_brake_line',[(-.20,side*.68,.98),(-.36,side*.99,.65),(-.46,side*1.12,.31)],.006,seal)
            b.sphere(wheel_name+'_gear_upper_fairing',(-.55,side*1.22,.50),(.36,.13,.12),ivory,segments=32,rings=16)

    # Constant-speed three-blade propeller. Closed twisted airfoil sections
    # rotate in YZ around the X-axis pivot, independent of the cowling.
    prop_center=(3.40,0,1.34)
    propeller=b.empty('propeller_pivot',prop_center)
    b.parent(b.cylinder('propeller_hub',(3.25,0,1.34),(3.46,0,1.34),.17,alloy,vertices=40),propeller)
    radial_stations=[(.16,.13),(.26,.19),(.40,.235),(.57,.25),(.74,.22),(.89,.175),(.99,.12),(1.035,.065)]
    profile_u=[.5-.5*math.cos(math.pi*j/30) for j in range(31)]
    profile=[(u,1) for u in profile_u]+[(u,-1) for u in reversed(profile_u[1:-1])]
    for blade in range(3):
        angle=math.radians(20+blade*120)
        vv=[]
        for radius,chord in radial_stations:
            fraction=(radius-.16)/(1.035-.16)
            pitch=math.radians(35-23*fraction)
            for u,sign in profile:
                chordwise=(u-.28)*chord
                thick=sign*thickness(u,.11)*chord
                x=prop_center[0]+chordwise*math.sin(pitch)+thick*math.cos(pitch)
                tangent=chordwise*math.cos(pitch)-thick*math.sin(pitch)
                y=radius*math.sin(angle)+tangent*math.cos(angle)
                z=prop_center[2]+radius*math.cos(angle)-tangent*math.sin(angle)
                vv.append((x,y,z))
        n=len(profile); ff=[]
        for i in range(len(radial_stations)-1):
            ff.extend((i*n+j,i*n+(j+1)%n,(i+1)*n+(j+1)%n,(i+1)*n+j) for j in range(n))
        ff += [tuple(reversed(range(n))),tuple((len(radial_stations)-1)*n+j for j in range(n))]
        obj=b.mesh('propeller_blade',vv,ff,composite,smooth=True)
        obj.data.materials.append(orange)
        obj.data.materials.append(ivory)
        for i in range(len(radial_stations)-1):
            if i>=len(radial_stations)-3:
                for j in range(n): obj.data.polygons[i*n+j].material_index=1 if i==len(radial_stations)-2 else 2
        b.parent(obj,propeller)
    spinner_sections=[(3.40,.235),(3.49,.220),(3.60,.185),(3.71,.140),(3.82,.087),(3.90,.038),(3.94,.008)]
    vv=[]
    for x,radius in spinner_sections:
        for j in range(64):
            a=math.tau*j/64
            vv.append((x,radius*math.cos(a),1.34+radius*math.sin(a)))
    ff=[]
    for i in range(len(spinner_sections)-1):
        ff.extend((i*64+j,i*64+(j+1)%64,(i+1)*64+(j+1)%64,(i+1)*64+j) for j in range(64))
    ff += [tuple(reversed(range(64))),tuple((len(spinner_sections)-1)*64+j for j in range(64))]
    b.parent(b.mesh('spinner',vv,ff,ivory,smooth=True),propeller)
    b.parent(b.torus('spinner_backplate',(3.40,0,1.34),.235,.007,steel,axis='X',major_segments=64,minor_segments=8),propeller)

    # Small operational details convey scale without branding.
    b.tube('left_pitot_probe',[(.16,2.36,.94),(.33,2.36,.77),(.71,2.36,.77)],.012,steel)
    b.sphere('belly_drain',(-1.7,0,1.0),(.04,.04,.035),steel,segments=12,rings=6)
    b.box('gps_antenna',(-.28,0,2.245),(.18,.12,.045),ivory,bevel=.035)
    b.cylinder('vhf_whip_antenna',(-.75,0,2.09),(-1.02,0,2.55),.010,steel,vertices=12,radius2=.003)
    for side in (-1,1):
        b.sphere('landing_light_housing',(.77,side*1.05,1.02),(.045,.14,.07),graphite,segments=24,rings=12)
        b.sphere('landing_light_lens',(.805,side*1.05,1.02),(.022,.10,.047),lamp,segments=24,rings=12)

    # Conservative static boxes. Moving surfaces and the propeller sweep need
    # engine-specific dynamic collision/flight logic rather than broad boxes.
    b.collision_box('fuselage',(.15,0,1.46),(6.7,1.20,1.30))
    b.collision_box('left_wing',(-.16,2.95,1.17),(1.57,4.85,.28),rotation=(math.radians(4.),0,0))
    b.collision_box('right_wing',(-.16,-2.95,1.17),(1.57,4.85,.28),rotation=(-math.radians(4.),0,0))
    b.collision_box('tailplane',(-3.04,0,1.63),(1.0,3.60,.14))
    b.collision_box('fin',(-3.12,0,2.29),(1.03,.16,1.42))
    b.collision_box('nose_gear',(2.10,0,.53),(.48,.20,1.04))
    b.collision_box('left_main_gear',(-.45,1.18,.47),(.57,.24,.92))
    b.collision_box('right_main_gear',(-.45,-1.18,.47),(.57,.24,.92))
    return {
        'description':'Original low-wing single-engine touring airplane with tapered cambered wings, separate control surfaces, tricycle landing gear and a three-blade propeller.',
        'dimensions_design_m':{'fuselage_with_spinner_length':7.79,'wingspan':10.8,'fin_height':3.02},
        'moving_parts':['propeller_pivot','nose_wheel_pivot','left_wheel_pivot','right_wheel_pivot',
                        'left_aileron_pivot','right_aileron_pivot','left_flap_pivot','right_flap_pivot',
                        'elevator_pivot','rudder_pivot'],
        'pivot_notes':'Propeller: local X. Wheels: local Y. Flaps/ailerons: local Y aligned outboard along their swept, dihedral hinge line (left/right signs differ). Elevator: local Y. Rudder: local Z. No animation clips or flight rig are provided.',
        'notes':['Cockpit glazing is opaque smoked material; no finished interior.',
                 'Airfoils are visual NACA-inspired approximations, not aerodynamic certification.',
                 'Eight static collision boxes exclude the propeller sweep and require target-engine tuning.',
                 'Control surfaces are independent closed meshes with geometric hinge gaps; low LODs need visual review.']
    }


def build_offroader(b):
    """Original expedition 4x4 with a hollow cabin and articulated road wheels."""
    m = {
        'paint': b.mat('offroader_sage_paint', (.135,.205,.155), metallic=.33, roughness=.33),
        'roof': b.mat('offroader_sand_roof', (.65,.61,.48), metallic=.18, roughness=.36),
        'trim': b.mat('offroader_graphite_trim', (.018,.024,.027), roughness=.46),
        'rubber': b.mat('offroader_tire_rubber', (.012,.016,.018), roughness=.85),
        'tread': b.mat('offroader_tread_rubber', (.022,.027,.029), roughness=.90),
        'alloy': b.mat('offroader_satin_alloy', (.43,.48,.51), metallic=.90, roughness=.31),
        'steel': b.mat('offroader_dark_steel', (.065,.083,.090), metallic=.82, roughness=.38),
        'chrome': b.mat('offroader_polished_metal', (.62,.68,.71), metallic=1., roughness=.19),
        'bronze': b.mat('offroader_bronze_wheels', (.30,.23,.13), metallic=.86, roughness=.30),
        'glass': b.mat('offroader_smoked_glass', (.78,.86,.90), roughness=.035, transmission=1.),
        'red': b.mat('offroader_red_lens', (.50,.008,.005), roughness=.23, emission=(.55,.008,.004)),
        'amber': b.mat('offroader_amber_lens', (.90,.20,.012), roughness=.23, emission=(.70,.10,.003)),
        'lamp': b.mat('offroader_led_lens', (.70,.82,.90), roughness=.16, emission=(.48,.61,.74)),
        'fabric': b.mat('offroader_woven_seat', (.055,.063,.058), roughness=.89),
        'interior': b.mat('offroader_cabin_polymer', (.030,.038,.039), roughness=.63),
        'display': b.mat('offroader_instrument_display', (.009,.026,.037), roughness=.25),
    }
    build_offroader_body(b, m)
    build_offroader_chassis(b, m)
    build_offroader_interior(b, m)

    # Broad, separate collision proxies are deliberately independent of the rig.
    b.collision_box('body', (0,0,1.00), (3.90,1.62,.62))
    b.collision_box('cabin', (-.77,0,1.70), (2.30,1.48,.65))
    b.collision_box('front_bumper', (2.20,0,.775), (.35,1.98,.47))
    b.collision_box('rear_bumper', (-2.15,0,.69), (.35,1.98,.28))
    for axle, x in (('front',1.32),('rear',-1.32)):
        for side, y in (('left',.92),('right',-.92)):
            b.collision_box(axle+'_'+side+'_wheel', (x,y,.43), (.86,.28,.86))
    b.collision_box('rear_spare', (-2.16,0,1.22), (.30,.86,.86))
    b.collision_box('roof_rack', (-.80,0,2.17), (2.15,1.65,.22))
    return {
        'description': 'Original four-door expedition 4x4 with open wheel arches, terrain tires, rear spare, ladder frame, axles, driveline, cabin, winch and roof rack.',
        'dimensions_design_m': {'wheelbase': 2.64, 'track': 1.84, 'tire_diameter': .86},
        'moving_parts': ['front_left_steering_pivot','front_right_steering_pivot',
                         'front_left_wheel_pivot','front_right_wheel_pivot',
                         'rear_left_wheel_pivot','rear_right_wheel_pivot'],
        'pivot_notes': 'Road wheels rotate about local Y. Each front wheel is parented to its own local-Z steering pivot. Front brake calipers follow steering; discs follow wheel rotation. No steering angles or axle physics are computed.',
        'notes': ['The cabin contains modeled seats, dashboard, steering wheel, console and belts; it is not a finished first-person cockpit.',
                  'Window transmission uses KHR_materials_transmission; verify support in the target-engine importer.',
                  'Four-wheel drive is represented by mechanical geometry, not a vehicle-physics simulation.',
                  'Doors, cabin steering wheel and suspension are static; no animation clips or suspension rig are provided.',
                  'Ten static collision boxes need engine tuning and do not follow the wheel pivots.'],
    }

def build_offroader_body(b, m):
    """Hollow, original expedition-wagon body with real wheel openings."""
    paint, trim, steel = m['paint'], m['trim'], m['steel']

    def trim_path(name, points, radius, material):
        # Polygonal panel edges must not inherit AUTO handles that overshoot
        # window corners and can push the rubber seals through the roof.
        obj = b.tube(name, points, radius, material)
        for spline in obj.data.splines:
            for point in spline.bezier_points:
                point.handle_left_type = point.handle_right_type = 'VECTOR'
        return obj

    def panel(name, outline, offset, material, bevel=0):
        # Closed thin panel, including concave silhouettes such as wheel arches.
        n = len(outline)
        delta = Vector(offset) * .5
        vertices = [tuple(Vector(p)-delta) for p in outline]
        vertices += [tuple(Vector(p)+delta) for p in outline]
        faces = [tuple(reversed(range(n))), tuple(range(n,2*n))]
        faces += [(i,(i+1)%n,(i+1)%n+n,i+n) for i in range(n)]
        return b.mesh(name, vertices, faces, material, bevel=bevel)

    def side_y(z, side, offset=0):
        return side*(.84-.09*max(0.,min(1.,(z-1.43)/.60))+offset)

    def side_outline(points, side, offset=0):
        return [(x,side_y(z,side,offset),z) for x,z in points]

    # A concave sheet at each side rises over both wheel wells. Above the
    # floor there is no cabin-sized solid block to obscure the interior.
    lower=.68
    radius=.53
    start=math.asin((lower-.43)/radius)
    lower_edge=[(-1.98,lower)]
    for axle in (-1.32,1.32):
        lower_edge.extend((axle+radius*math.cos(a),.43+radius*math.sin(a))
                          for a in [math.pi-start-(math.pi-2*start)*i/24 for i in range(25)])
    lower_edge.append((2.04,lower))
    side_shape=[(2.04,1.39),(.62,1.43),(-1.94,1.43),(-1.98,1.38)]+lower_edge
    for side, side_name in ((1,'left'),(-1,'right')):
        panel(side_name+'_body_side',[(x,side*.84,z) for x,z in side_shape],(0,.032,0),paint,bevel=.007)
        # Sculpted six-sided flare cross section sweeps above each tyre.
        for axle,label in ((1.32,'front'),(-1.32,'rear')):
            profile=[(.534,.842),(.538,1.015),(.551,1.063),(.586,1.063),(.603,.98),(.600,.842)]
            verts=[]
            steps=32
            for i in range(steps+1):
                a=math.pi*i/steps
                verts.extend((axle+r*math.cos(a),side*y,.43+r*math.sin(a)) for r,y in profile)
            count=len(profile)
            faces=[(i*count+j,i*count+(j+1)%count,(i+1)*count+(j+1)%count,(i+1)*count+j)
                   for i in range(steps) for j in range(count)]
            faces += [tuple(reversed(range(count))),tuple(steps*count+j for j in range(count))]
            b.mesh(side_name+'_'+label+'_fender_flare',verts,faces,trim,bevel=.004)
            for i in range(7):
                a=math.pi*(i+.5)/7
                pos=(axle+.578*math.cos(a),side*1.070,.43+.578*math.sin(a))
                b.cylinder(side_name+'_'+label+'_flare_fastener',
                           (pos[0],pos[1]-side*.004,pos[2]),
                           (pos[0],pos[1]+side*.004,pos[2]),.006,m['alloy'],vertices=8)
            # The flap is aft of the tyre envelope and leaves the tread visible.
            b.box(side_name+'_'+label+'_mud_flap',(axle-.50,side*.94,.37),(.025,.27,.29),m['rubber'],bevel=.007)

        # Actual sill covers and open step rails between the wheel openings.
        b.box(side_name+'_rock_slider',(-.02,side*.97,.64),(1.49,.12,.10),steel,bevel=.025)
        b.box(side_name+'_running_board',(-.02,side*1.015,.685),(1.39,.22,.035),trim,bevel=.012)
        for x in (-.55,.0,.51):
            b.cylinder(side_name+'_step_mount',(x,side*.70,.64),(x,side*.99,.64),.026,steel,vertices=12)
        for x in (-.58,-.37,-.16,.05,.26,.47):
            b.box(side_name+'_step_grip',(x,side*1.03,.706),(.065,.16,.006),m['rubber'],bevel=.002)

        # Side windows: thin closed glass panes within the A/B/C/D pillars.
        front_window=[(.54,1.49),(.285,1.985),(-.525,1.985),(-.525,1.49)]
        rear_window=[(-.635,1.49),(-.635,1.985),(-1.475,1.985),(-1.475,1.49)]
        quarter_window=[(-1.585,1.49),(-1.585,1.985),(-1.875,1.985),(-1.905,1.49)]
        for label,shape in (('front_door',front_window),('rear_door',rear_window),('quarter',quarter_window)):
            panel(side_name+'_'+label+'_glass',side_outline(shape,side),(0,.007,0),m['glass'])
            seal_points=side_outline(shape+[shape[0]],side,.006)
            trim_path(side_name+'_'+label+'_window_seal',seal_points,.012,m['rubber'])
        pillars=[((.62,1.43),(.32,2.03),.036),
                 ((-.58,1.43),(-.58,2.03),.033),
                 ((-1.53,1.43),(-1.53,2.03),.035),
                 ((-1.96,1.43),(-1.94,2.03),.038)]
        for index,(lower_point,upper_point,width) in enumerate(pillars):
            a=side_outline([lower_point],side)[0]
            c=side_outline([upper_point],side)[0]
            b.cylinder(side_name+'_cabin_pillar_'+str(index),a,c,width,paint,vertices=12)
        b.tube(side_name+'_belt_moulding',[(x,side*.86,1.435) for x in (-1.96,-1.53,-.58,.62)],.016,trim)
        b.cylinder(side_name+'_window_lower_rail',(.60,side*.837,1.465),(-1.94,side*.837,1.465),.025,paint,vertices=12)
        b.cylinder(side_name+'_window_header',(.32,side*.755,2.025),(-1.94,side*.755,2.025),.034,paint,vertices=12)
        b.tube(side_name+'_roof_gutter',[(.40,side*.785,2.065),(-.60,side*.785,2.065),(-1.99,side*.785,2.065)],.015,trim)

        # Dark recess lines follow the front door and the rear wheel intrusion.
        front_door=[(.58,1.42),(.58,.76),(.51,.705),(-.51,.705),(-.57,.76),(-.57,1.42)]
        rear_door=[(-.61,1.42),(-.61,.73),(-.83,.73),(-.90,.86),(-1.07,.96),(-1.33,.995),(-1.55,.94),(-1.57,1.42)]
        for label,points,handle_x in (('front',front_door,-.39),('rear',rear_door,-1.37)):
            trim_path(side_name+'_'+label+'_door_seam',[(x,side*.861,z) for x,z in points],.0035,trim)
            b.box(side_name+'_'+label+'_handle_recess',(handle_x,side*.866,1.32),(.20,.025,.07),trim,bevel=.022)
            b.box(side_name+'_'+label+'_door_handle',(handle_x,side*.895,1.333),(.14,.028,.025),m['alloy'],bevel=.009)
        for x in (.51,-.67):
            for z in (.91,1.23):
                b.box(side_name+'_door_hinge',(x,side*.875,z),(.06,.024,.07),paint,bevel=.007)
                b.cylinder(side_name+'_hinge_pin',(x-.022,side*.89,z-.035),(x-.022,side*.89,z+.035),.009,steel,vertices=10)

        # Mirrors face aft toward the driver, with an independent dark housing.
        b.tube(side_name+'_mirror_arm',[(.48,side*.84,1.58),(.42,side*.96,1.60),(.37,side*1.04,1.65)],.023,trim)
        b.box(side_name+'_mirror_housing',(.36,side*1.075,1.655),(.14,.15,.19),trim,bevel=.035)
        b.box(side_name+'_mirror_glass',(.282,side*1.075,1.655),(.010,.119,.15),m['chrome'],bevel=.015)
        b.box(side_name+'_side_indicator',(.77,side*.865,1.23),(.12,.018,.038),m['amber'],bevel=.009)

    # Hood and the upper cowl are separate from the open passenger cell.
    b.box('hood',(1.325,0,1.404),(1.39,1.63,.078),paint,bevel=.030)
    b.box('hood_center_raise',(1.30,0,1.452),(.96,.84,.025),paint,bevel=.035)
    for side in (-1,1):
        b.tube('hood_shut_line',[(.64,side*.824,1.42),(1.28,side*.824,1.42),(2.02,side*.802,1.42)],.004,trim)
        b.box('hood_latch',(1.88,side*.84,1.315),(.065,.035,.12),trim,bevel=.013)
        b.cylinder('hood_latch_pin',(1.88,side*.85,1.28),(1.88,side*.89,1.28),.013,m['alloy'],vertices=10)
    b.box('front_cowl',(.59,0,1.435),(.11,1.63,.07),paint,bevel=.015)
    b.box('cowl_vent',(.57,0,1.48),(.075,.83,.012),trim,bevel=.006)
    for y in (-.32,-.24,-.16,-.08,0,.08,.16,.24,.32):
        b.box('cowl_vent_slit',(.57,y,1.488),(.052,.025,.005),steel,bevel=.001)

    windshield=[(.601,-.775,1.485),(.601,.775,1.485),(.343,.707,1.992),(.343,-.707,1.992)]
    panel('windshield',windshield,(-.007,0,0),m['glass'])
    trim_path('windshield_seal',windshield+[windshield[0]],.014,m['rubber'])
    b.cylinder('windshield_lower_frame',(.62,-.82,1.44),(.62,.82,1.44),.028,paint,vertices=16)
    b.cylinder('windshield_upper_frame',(.32,-.755,2.03),(.32,.755,2.03),.030,paint,vertices=16)
    for y in (-.38,.36):
        b.tube('windscreen_wiper_arm',[(.622,y,1.48),(.53,y-.10,1.65),(.455,y-.15,1.79)],.009,steel)
        b.tube('windscreen_wiper_blade',[(.448,y-.35,1.80),(.448,y-.15,1.80),(.448,y+.07,1.80)],.012,trim)

    b.box('roof',(-.80,0,2.075),(2.48,1.59,.085),m['roof'],bevel=.045)
    rear_outline=[(-1.981,-.84,.75),(-1.981,.84,.75),(-1.965,.84,1.44),(-1.965,-.84,1.44)]
    panel('tailgate_lower',rear_outline,(.035,0,0),paint,bevel=.01)
    rear_window=[(-1.97,-.733,1.50),(-1.97,.733,1.50),(-1.95,.733,1.988),(-1.95,-.733,1.988)]
    panel('tailgate_glass',rear_window,(.007,0,0),m['glass'])
    trim_path('tailgate_window_seal',rear_window+[rear_window[0]],.014,m['rubber'])
    for y in (-.78,.78):
        b.cylinder('rear_corner_pillar',(-1.97,y,1.43),(-1.95,y*.96,2.03),.042,paint,vertices=12)
    b.cylinder('rear_window_header',(-1.95,-.755,2.03),(-1.95,.755,2.03),.035,paint,vertices=12)
    b.cylinder('rear_window_sill',(-1.97,-.82,1.445),(-1.97,.82,1.445),.040,paint,vertices=12)
    b.tube('rear_window_wiper',[(-1.994,.48,1.51),(-1.998,.15,1.70),(-1.998,-.06,1.78)],.010,steel)
    b.box('rear_window_wiper_blade',(-2.008,-.05,1.79),(.018,.48,.018),trim,bevel=.005)
    b.box('tailgate_handle',(-2.012,-.61,1.35),(.030,.18,.065),trim,bevel=.017)
    for y in (-.60,.60):
        for z in (.92,1.36):
            b.box('tailgate_hinge',(-2.01,y,z),(.04,.10,.055),paint,bevel=.008)

    # Original wide horizontal grille rather than a brand-specific pattern.
    b.box('front_fascia',(2.042,0,1.105),(.045,1.66,.60),paint,bevel=.025)
    b.box('grille_recess',(2.074,0,1.18),(.038,.98,.40),trim,bevel=.030)
    for z in (1.035,1.108,1.181,1.254,1.327):
        b.box('grille_horizontal_bar',(2.102,0,z),(.030,.90,.026),steel,bevel=.008)
    b.box('grille_center_bridge',(2.123,0,1.182),(.024,.063,.38),paint,bevel=.008)
    for side in (-1,1):
        b.box('headlight_housing',(2.084,side*.638,1.205),(.09,.315,.245),trim,bevel=.025)
        for y in (side*.565,side*.710):
            b.box('headlight_lens',(2.137,y,1.22),(.022,.111,.125),m['lamp'],bevel=.019)
            b.box('headlight_projector_inner',(2.151,y,1.22),(.012,.065,.073),m['alloy'],bevel=.014)
        b.box('daytime_running_light',(2.144,side*.638,1.113),(.014,.245,.015),m['lamp'],bevel=.005)
        b.box('front_signal',(2.100,side*.67,.995),(.025,.19,.042),m['amber'],bevel=.008)
        b.box('rear_light_housing',(-2.015,side*.704,1.191),(.065,.177,.36),trim,bevel=.020)
        for z,material in ((1.30,m['red']),(1.205,m['red']),(1.115,m['amber']),(1.055,m['lamp'])):
            b.box('rear_light_lens',(-2.056,side*.704,z),(.016,.137,.055),material,bevel=.012)

    b.box('front_bumper',(2.142,0,.735),(.27,1.91,.205),trim,bevel=.045)
    b.box('front_bumper_skid',(2.17,0,.595),(.17,1.15,.12),m['alloy'],bevel=.018,rotation=(0,-.22,0))
    b.box('rear_bumper',(-2.075,0,.705),(.24,1.90,.20),trim,bevel=.040)
    for side in (-1,1):
        b.box('bumper_fog_recess',(2.286,side*.76,.75),(.025,.23,.10),steel,bevel=.020)
        b.box('bumper_fog_lens',(2.305,side*.76,.75),(.018,.13,.057),m['lamp'],bevel=.011)
        b.box('recovery_mount',(2.25,side*.53,.62),(.11,.07,.10),steel,bevel=.012)
        b.torus('front_recovery_shackle',(2.31,side*.53,.603),.045,.012,m['bronze'],axis='X',major_segments=24,minor_segments=8)
        b.cylinder('rear_recovery_eye',(-2.20,side*.57,.62),(-2.25,side*.57,.62),.035,steel,vertices=16)
    # Winch is visibly mechanical, with roller fairlead and a wound cable drum.
    b.box('winch_base',(2.10,0,.835),(.30,.59,.055),steel,bevel=.014)
    b.cylinder('winch_drum',(2.11,-.18,.897),(2.11,.18,.897),.072,steel,vertices=24)
    for y in (-.15,-.105,-.06,-.015,.03,.075,.12,.165):
        b.torus('winch_cable_winding',(2.11,y,.897),.072,.009,m['alloy'],axis='Y',major_segments=24,minor_segments=6)
    b.cylinder('winch_motor',(2.11,.19,.897),(2.11,.30,.897),.065,trim,vertices=24)
    for z in (.795,.855):
        b.cylinder('fairlead_horizontal_roller',(2.304,-.16,z),(2.304,.16,z),.015,m['alloy'],vertices=16)
    for y in (-.165,.165):
        b.cylinder('fairlead_vertical_roller',(2.304,y,.795),(2.304,y,.855),.018,m['alloy'],vertices=16)
    b.tube('winch_lead',[(2.12,0,.90),(2.28,0,.825),(2.33,0,.79)],.008,steel)
    b.torus('winch_hook',(2.34,0,.77),.025,.008,m['bronze'],axis='Y',major_segments=20,minor_segments=8)
    b.box('rear_tow_receiver',(-2.15,0,.53),(.24,.095,.10),steel,bevel=.012)
    b.cylinder('tow_ball_stem',(-2.29,0,.50),(-2.29,0,.57),.023,steel,vertices=16)
    b.sphere('tow_ball',(-2.29,0,.60),(.035,.035,.035),m['chrome'],segments=16,rings=8)

    # Passenger-side raised air intake and a restrained expedition roof basket.
    b.tube('snorkel_riser',[(1.12,-.90,1.27),(.88,-.92,1.40),(.64,-.91,1.52),(.37,-.87,2.08)],.052,trim)
    b.box('snorkel_head',(.39,-.87,2.135),(.21,.13,.12),trim,bevel=.025)
    b.box('snorkel_intake',(.505,-.87,2.142),(.016,.103,.066),steel,bevel=.010)
    for z in (2.125,2.146,2.167):
        b.box('snorkel_intake_slit',(.516,-.87,z),(.008,.085,.007),m['rubber'],bevel=.002)
    for side in (-1,1):
        for x in (-1.61,-.78,.06):
            b.box('roof_rack_foot',(x,side*.66,2.143),(.13,.10,.09),trim,bevel=.015)
            b.cylinder('roof_rack_stanchion',(x,side*.66,2.16),(x,side*.66,2.25),.017,steel,vertices=12)
        b.tube('roof_rack_side_rail',[(.18,side*.67,2.25),(-.70,side*.67,2.25),(-1.76,side*.67,2.25)],.020,steel)
    for x in (.18,-1.76):
        b.cylinder('roof_rack_end_rail',(x,-.67,2.25),(x,.67,2.25),.020,steel,vertices=12)
    for x in (.08,-.28,-.64,-1.00,-1.36,-1.69):
        b.box('roof_rack_crossbar',(x,0,2.198),(.045,1.32,.025),steel,bevel=.008)
    b.box('roof_led_bar',(.21,0,2.205),(.09,.85,.075),trim,bevel=.015)
    for y in (-.33,-.22,-.11,0,.11,.22,.33):
        b.box('roof_led_lens',(.263,y,2.205),(.014,.073,.042),m['lamp'],bevel=.008)
    b.cylinder('short_roof_antenna',(-1.84,.25,2.12),(-1.88,.25,2.36),.008,trim,vertices=10,radius2=.003)

    return {'body': 'Hollow four-door expedition wagon with wheel-arch cutouts, transmitting glazing, rack, winch and snorkel.',
            'note': 'Mirrors reach approximately 2.30 m width; antenna reaches 2.36 m. Glass relies on the transmission material supplied by the caller.'}


def build_offroader_chassis(b, m):
    """Detailed visual 4WD running gear; SI metres and no physics solver."""

    def blocks(name, items, material, bevel=.0015):
        vertices, faces = [], []
        corners = ((-1,-1,-1),(1,-1,-1),(1,1,-1),(-1,1,-1),
                   (-1,-1,1),(1,-1,1),(1,1,1),(-1,1,1))
        cube_faces = ((0,3,2,1),(4,5,6,7),(0,1,5,4),(1,2,6,5),(2,3,7,6),(3,0,4,7))
        for center, axes, half in items:
            start = len(vertices)
            for signs in corners:
                vertices.append(tuple(center[k]+sum(signs[j]*half[j]*axes[j][k] for j in range(3)) for k in range(3)))
            faces.extend(tuple(start+i for i in face) for face in cube_faces)
        obj = b.mesh(name, vertices, faces, material, bevel=bevel)
        if bevel:
            # One bevel segment gives lugs a manufactured edge without multiplying
            # every block into a high-poly component.
            obj.modifiers[0].segments = 1
        return obj

    def wheel(name, center, outward=1, spare=False, pivot=None, steering=None):
        cx, cy, cz = center

        def point(u, axial, w):
            return (cx-axial, cy+u, cz+w) if spare else (cx+u, cy+outward*axial, cz+w)

        def direction(u, axial, w):
            return (-axial,u,w) if spare else (u,outward*axial,w)

        def attach(obj):
            if pivot is not None:
                b.parent(obj, pivot)
            return obj

        def ring(label, radius, thickness, axial, material, segments=64, minor=8):
            obj = b.torus(name+'_'+label, point(0,axial,0), radius, thickness, material,
                          axis='X' if spare else 'Y', major_segments=segments, minor_segments=minor)
            return attach(obj)

        def cylinder(label, start, end, radius, material, vertices=24):
            return attach(b.cylinder(name+'_'+label,point(*start),point(*end),radius,material,vertices=vertices))

        def profile_surface(label, profile, material, segments=72):
            # A closed section gives a real tyre cavity and dished rim, rather
            # than a solid disc. Profile points are (radius, axial position).
            vertices=[]
            for radius, axial in profile:
                for i in range(segments):
                    angle=math.tau*i/segments
                    vertices.append(point(radius*math.sin(angle),axial,radius*math.cos(angle)))
            faces=[]
            for j in range(len(profile)):
                for i in range(segments):
                    faces.append((j*segments+i,j*segments+(i+1)%segments,
                                  ((j+1)%len(profile))*segments+(i+1)%segments,
                                  ((j+1)%len(profile))*segments+i))
            return attach(b.mesh(name+'_'+label,vertices,faces,material,smooth=True))

        profile_surface('tire_carcass',[(.234,-.102),(.295,-.137),(.357,-.139),
                        (.393,-.112),(.410,-.065),(.414,0),(.410,.065),
                        (.393,.112),(.357,.139),(.295,.137),(.234,.102),
                        (.226,.075),(.226,-.075)],m['rubber'])
        # Alternating chevron lugs are one mesh, with visible channels between
        # adjacent rows and successive blocks. Maximum radial envelope is .43m.
        lugs=[]
        for row in (-1,0,1):
            for i in range(32):
                angle=math.tau*(i+(.5 if row else 0))/32
                radial=Vector(direction(math.sin(angle),0,math.cos(angle)))
                tangent=Vector(direction(math.cos(angle),0,-math.sin(angle)))
                across=Vector(direction(0,1,0))
                skew=(.25 if i%2 else -.25) if row==0 else row*.28
                axes=(tuple(tangent*math.cos(skew)+across*math.sin(skew)),
                      tuple(-tangent*math.sin(skew)+across*math.cos(skew)),tuple(radial))
                radius=.4205 if row==0 else .405
                center_block=point(radius*math.sin(angle),row*.089,radius*math.cos(angle))
                lugs.append((center_block,axes,(.025,.034,.0095)))
        attach(blocks(name+'_terrain_tread',lugs,m['tread']))
        for side in (-1,1):
            ring('sidewall_moulding',.312,.0024,side*.139,m['rubber'],minor=6)
            ring('sidewall_bead',.242,.004,side*.107,m['rubber'],minor=6)

        # Deep barrel and separate beadlock ring, with open paired spokes.
        profile_surface('rim_barrel',[(.218,-.098),(.228,-.098),(.228,.105),
                        (.217,.105),(.205,.072),(.205,-.069)],m['steel'],segments=48)
        ring('outer_rim_lip',.224,.011,.107,m['bronze'],segments=64)
        ring('inner_rim_lip',.224,.009,-.098,m['alloy'],segments=48)
        ring('beadlock_ring',.207,.009,.119,m['bronze'],segments=64)
        spokes=[]
        for i in range(6):
            for split in (-1,1):
                angle=math.tau*i/6+split*.073
                radial=Vector(direction(math.sin(angle),0,math.cos(angle)))
                tangent=Vector(direction(math.cos(angle),0,-math.sin(angle)))
                axes=(tuple(radial),tuple(direction(0,1,0)),tuple(tangent))
                spokes.append((point(.139*math.sin(angle),.082,.139*math.cos(angle)),axes,(.071,.016,.014)))
        attach(blocks(name+'_paired_alloy_spokes',spokes,m['bronze'],bevel=.003))
        cylinder('hub_body',(0,-.052,0),(0,.114,0),.068,m['steel'],vertices=32)
        cylinder('hub_cap',(0,.115,0),(0,.133,0),.047,m['bronze'],vertices=32)
        for i in range(6):
            angle=math.tau*i/6
            u,w=.055*math.sin(angle),.055*math.cos(angle)
            cylinder('wheel_nut',(u,.115,w),(u,.137,w),.009,m['chrome'],vertices=6)
        for i in range(20):
            angle=math.tau*i/20
            u,w=.207*math.sin(angle),.207*math.cos(angle)
            cylinder('beadlock_bolt',(u,.120,w),(u,.133,w),.0057,m['alloy'],vertices=6)
        cylinder('valve_stem',(.162,.108,.104),(.162,.137,.104),.004,m['rubber'],vertices=8)

        if not spare:
            profile_surface('brake_rotor',[(.065,-.082),(.172,-.082),(.172,-.069),(.065,-.069)],m['alloy'],segments=64)
            cylinder('brake_hat',(0,-.085,0),(0,-.053,0),.075,m['steel'],vertices=32)
            # Calipers follow steering, but never the rolling wheel rotation.
            caliper=b.box(name+'_brake_caliper',point(.126,-.086,.020),(.065,.052,.125),m['steel'],bevel=.014)
            if steering is not None:
                b.parent(caliper,steering)

    # Ladder frame rails, raised ends and bolted crossmembers.
    for side in (-1,1):
        b.box('ladder_frame_rail',(.0,side*.48,.535),(3.52,.090,.155),m['steel'],bevel=.018)
        for x in (-1.65,1.65):
            b.box('frame_end_riser',(x,side*.48,.600),(.52,.096,.135),m['steel'],bevel=.015)
        for x in (-1.45,-.62,.56,1.46):
            b.cylinder('body_mount',(x,side*.48,.59),(x,side*.48,.68),.047,m['rubber'],vertices=20)
            b.cylinder('body_mount_bolt',(x,side*.48,.665),(x,side*.48,.693),.012,m['alloy'],vertices=6)
    for x in (-1.76,-.76,.18,.91,1.76):
        b.box('frame_crossmember',(x,0,.54),(.095,1.05,.105),m['steel'],bevel=.012)

    # Transfer case and separate driveshafts visibly establish 4WD running gear.
    b.box('gearbox_housing',(.56,0,.59),(.55,.30,.22),m['alloy'],bevel=.065)
    b.box('transfer_case',(.03,0,.53),(.37,.35,.20),m['steel'],bevel=.055)
    b.box('transfer_case_skid',(.12,0,.385),(.84,.63,.055),m['alloy'],bevel=.018)
    for x in (-.18,.39):
        for y in (-.255,.255):
            b.cylinder('skid_plate_bolt',(x,y,.35),(x,y,.365),.010,m['steel'],vertices=6)
    b.cylinder('front_prop_shaft',(.20,0,.53),(1.32,0,.43),.028,m['steel'],vertices=20)
    b.cylinder('rear_prop_shaft',(-.13,0,.52),(-1.32,0,.43),.032,m['steel'],vertices=20)
    for x in (-1.20,1.18):
        b.cylinder('prop_shaft_universal_joint',(x-.055,0,.445),(x+.055,0,.445),.045,m['alloy'],vertices=16)

    def coil(name, a, end, radius=.058, wire=.0075, turns=5):
        a,end=Vector(a),Vector(end)
        direction=end-a
        axis=direction.normalized()
        helper=Vector((0,1,0)) if abs(axis.y)<.9 else Vector((1,0,0))
        u=axis.cross(helper).normalized()
        v=axis.cross(u).normalized()
        vertices,faces=[],[]
        steps,sides=81,6
        for i in range(steps):
            t=i/(steps-1); angle=math.tau*turns*t
            radial=math.cos(angle)*u+math.sin(angle)*v
            tangent=(direction+math.tau*turns*radius*(-math.sin(angle)*u+math.cos(angle)*v)).normalized()
            normal=tangent.cross(radial).normalized()
            center=a+direction*t+radial*radius
            for j in range(sides):
                phi=math.tau*j/sides
                vertices.append(tuple(center+wire*(math.cos(phi)*radial+math.sin(phi)*normal)))
        for i in range(steps-1):
            faces.extend((i*sides+j,i*sides+(j+1)%sides,(i+1)*sides+(j+1)%sides,(i+1)*sides+j) for j in range(sides))
        faces += [tuple(reversed(range(sides))),tuple((steps-1)*sides+j for j in range(sides))]
        return b.mesh(name,vertices,faces,m['steel'],smooth=True)

    pivots={}
    for axle_name,cx in (('front',1.32),('rear',-1.32)):
        b.cylinder(axle_name+'_axle_tube',(cx,-.83,.43),(cx,.83,.43),.053,m['steel'],vertices=24)
        b.sphere(axle_name+'_differential',(cx,0,.415),(.175,.19,.155),m['steel'],segments=32,rings=16)
        b.cylinder(axle_name+'_diff_cover',(cx-.18,0,.415),(cx-.192,0,.415),.126,m['alloy'],vertices=24)
        for i in range(10):
            angle=math.tau*i/10
            b.cylinder(axle_name+'_diff_cover_bolt',(cx-.193,.11*math.sin(angle),.415+.11*math.cos(angle)),
                       (cx-.206,.11*math.sin(angle),.415+.11*math.cos(angle)),.009,m['steel'],vertices=6)
        for side,side_name in ((1,'left'),(-1,'right')):
            center=(cx,side*.92,.43)
            steering=None
            if axle_name=='front':
                steering=b.empty('front_'+side_name+'_steering_pivot',center)
                pivots['front_'+side_name+'_steering_pivot']=steering
            pivot=b.empty(axle_name+'_'+side_name+'_wheel_pivot',center)
            if steering is not None:
                b.parent(pivot,steering)
            pivots[axle_name+'_'+side_name+'_wheel_pivot']=pivot
            wheel(axle_name+'_'+side_name,center,outward=side,pivot=pivot,steering=steering)
            # Radius arms locate the rigid axles; shocks and springs are static
            # visual parts pending the target engine's suspension simulation.
            anchor_x=cx-.70 if axle_name=='front' else cx+.72
            b.cylinder(axle_name+'_'+side_name+'_radius_arm',(anchor_x,side*.46,.49),(cx,side*.68,.39),.025,m['steel'],vertices=16)
            b.cylinder(axle_name+'_'+side_name+'_upper_link',(anchor_x,side*.34,.61),(cx,side*.43,.49),.018,m['steel'],vertices=12)
            for x,y,z in ((anchor_x,side*.46,.49),(cx,side*.68,.39)):
                b.cylinder('suspension_bush',(x,y-.025,z),(x,y+.025,z),.037,m['rubber'],vertices=16)
                b.cylinder('suspension_link_bolt',(x,y-.035,z),(x,y+.035,z),.011,m['alloy'],vertices=6)
            coil(axle_name+'_'+side_name+'_suspension_spring',(cx,side*.60,.49),(cx,side*.60,.72))
            for z in (.475,.73):
                b.cylinder('spring_seat',(cx,side*.60,z-.009),(cx,side*.60,z+.009),.073,m['steel'],vertices=24)
            a=(cx-.15,side*.70,.44); end=(cx+.16,side*.61,.75)
            b.cylinder('shock_absorber_body',a,tuple(Vector(a).lerp(Vector(end),.65)),.028,m['paint'],vertices=20)
            b.cylinder('shock_absorber_piston',tuple(Vector(a).lerp(Vector(end),.56)),end,.014,m['chrome'],vertices=16)
            if steering is not None:
                knuckle=b.cylinder(side_name+'_steering_knuckle',(cx,side*.77,.43),(cx,side*.87,.43),.072,m['steel'],vertices=20)
                b.parent(knuckle,steering)
                arm=b.cylinder(side_name+'_steering_arm',(cx,side*.79,.41),(cx-.16,side*.77,.40),.024,m['steel'],vertices=12)
                b.parent(arm,steering)
    b.cylinder('front_tie_rod',(1.16,-.77,.40),(1.16,.77,.40),.018,m['steel'],vertices=16)
    b.cylinder('steering_drag_link',(.96,-.38,.64),(1.16,.61,.40),.018,m['steel'],vertices=16)
    b.cylinder('rear_panhard_rod',(-1.32,-.62,.45),(-1.08,.56,.62),.019,m['steel'],vertices=16)

    # Exhaust avoids the cabin floor and axle travel envelope; the outlet sits
    # behind the rear wheel and points toward the vehicle side.
    b.tube('underbody_exhaust',[(1.22,-.26,.63),(.76,-.36,.47),(.16,-.35,.46),
                              (-.55,-.35,.46),(-.98,-.39,.62),(-1.43,-.41,.63),(-1.83,-.68,.51)],.026,m['steel'],resolution=1)
    b.box('exhaust_silencer',(-.32,-.34,.49),(.65,.22,.15),m['alloy'],bevel=.055)
    b.cylinder('exhaust_outlet',(-1.83,-.68,.51),(-1.86,-.84,.50),.034,m['alloy'],vertices=24)
    b.cylinder('exhaust_outlet_shadow',(-1.86,-.842,.50),(-1.862,-.850,.499),.026,m['trim'],vertices=24)
    # The fifth matching wheel is a static carrier-mounted spare, not a driving
    # wheel; no extra animation pivot is exported for it.
    b.box('spare_carrier_backplate',(-2.005,0,1.20),(.08,.38,.44),m['steel'],bevel=.018)
    for y in (-.16,.16):
        b.cylinder('spare_carrier_support',(-1.99,y,1.12),(-2.15,y*.45,1.22),.023,m['steel'],vertices=16)
    wheel('rear_spare',(-2.16,0,1.22),spare=True)
    return {'moving_parts':list(pivots), 'wheel_radius_m':.43, 'wheelbase_m':2.64,
            'track_m':1.84, 'tire_width_m':.28,
            'notes':['Front wheel pivots are children of Z-axis steering pivots.',
                     'Brake discs roll with the wheels; front calipers follow steering only.',
                     'Axle links, springs and dampers are visual static geometry until engine rigging.']}

def build_offroader_interior(b, m):
    """Basic left-hand-drive five-seat cabin; static geometry in SI metres.

    Seats, belts and controls are modelled for exterior/interior inspection,
    without occupants, working instruments or an authored first-person rig.
    """
    fabric, plastic = m['fabric'], m['interior']
    rubber, alloy, steel = m['rubber'], m['alloy'], m['steel']

    # A thin cabin liner remains inside the actual sill and wheel envelope.
    b.box('cabin_floor_liner', (-.64, 0, .768), (2.48, 1.48, .022), rubber, bevel=.008)
    for side in (-1, 1):
        b.box('front_footwell_mat', (.25, side*.41, .785), (.60, .46, .014), rubber, bevel=.016)
        for i in range(7):
            b.box('footwell_mat_rib', (.04+i*.068, side*.41, .794), (.012, .37, .005), m['trim'], bevel=.002)
        b.box('rear_footwell_mat', (-.73, side*.41, .785), (.36, .46, .014), rubber, bevel=.014)
    b.box('cargo_floor_mat', (-1.73, 0, .79), (.30, 1.41, .030), rubber, bevel=.012)
    for side in (-1, 1):
        b.torus('cargo_tie_down', (-1.73, side*.60, .811), .025, .004, steel,
                axis='Z', major_segments=16, minor_segments=6)

    def ribbon(name, points, width, material):
        # Flat closed belt strips, not round cables. Width lies mostly in X.
        vertices=[]
        for point in points:
            x,y,z=point
            vertices.extend([(x-width*.5,y-.0015,z),(x+width*.5,y-.0015,z),
                             (x+width*.5,y+.0015,z),(x-width*.5,y+.0015,z)])
        faces=[(3,2,1,0)]
        for i in range(len(points)-1):
            faces.extend((i*4+j,i*4+(j+1)%4,(i+1)*4+(j+1)%4,(i+1)*4+j) for j in range(4))
        faces.append(tuple((len(points)-1)*4+j for j in range(4)))
        return b.mesh(name,vertices,faces,material)

    # Two individually shaped front seats: bolsters, inserts and headrests.
    for side, label in ((1,'driver'),(-1,'passenger')):
        y=side*.40
        for rail_y in (y-.16,y+.16):
            b.box(label+'_seat_rail',(-.18,rail_y,.82),(.46,.032,.035),steel,bevel=.007)
            b.box(label+'_seat_pedestal',(-.20,rail_y,.90),(.25,.045,.14),plastic,bevel=.018)
        b.box(label+'_seat_cushion',(-.15,y,1.015),(.49,.47,.145),fabric,bevel=.050)
        b.box(label+'_cushion_insert',(-.12,y,1.091),(.35,.30,.018),fabric,bevel=.025)
        for offset in (-.21,.21):
            b.box(label+'_cushion_bolster',(-.16,y+offset,1.085),(.43,.070,.10),fabric,bevel=.031)
        b.box(label+'_seat_back',(-.43,y,1.34),(.14,.47,.60),fabric,bevel=.050,rotation=(0,-.14,0))
        b.box(label+'_back_insert',(-.345,y,1.34),(.023,.30,.45),fabric,bevel=.025,rotation=(0,-.14,0))
        for offset in (-.205,.205):
            b.box(label+'_back_bolster',(-.385,y+offset,1.35),(.12,.075,.52),fabric,bevel=.034,rotation=(0,-.14,0))
        # Subtle piping defines upholstery seams without dense fake text.
        for offset in (-.145,.145):
            b.tube(label+'_seat_stitch',[(-.32,y+offset,1.103),(-.16,y+offset,1.107),(.015,y+offset,1.103)],.0018,m['roof'],resolution=1)
            b.tube(label+'_back_stitch',[(-.307,y+offset,1.15),(-.331,y+offset,1.34),(-.359,y+offset,1.53)],.0018,m['roof'],resolution=1)
        for offset in (-.09,.09):
            b.cylinder(label+'_headrest_post',(-.475,y+offset,1.60),(-.495,y+offset,1.72),.008,alloy,vertices=12)
        b.box(label+'_headrest',(-.50,y,1.745),(.15,.29,.20),fabric,bevel=.047)
        # Retracted unoccupied belts hang at the B pillar, outside the seats.
        ribbon(label+'_shoulder_belt',[(-.47,side*.744,1.79),(-.43,side*.735,1.50),
                                      (-.30,side*.67,1.08),(-.30,side*.66,.83)],.040,rubber)
        b.box(label+'_upper_belt_anchor',(-.47,side*.748,1.79),(.078,.025,.055),plastic,bevel=.012)
        b.box(label+'_belt_retractor',(-.30,side*.685,.89),(.11,.075,.16),plastic,bevel=.018)
        b.cylinder(label+'_buckle_stalk',(-.22,side*.14,.91),(-.16,side*.14,1.10),.009,steel,vertices=10)
        b.box(label+'_belt_buckle',(-.16,side*.14,1.12),(.045,.035,.065),plastic,bevel=.010)
        b.box(label+'_buckle_release',(-.143,side*.14,1.14),(.014,.025,.022),m['red'],bevel=.004)

    # Rear bench has three seating positions and restrained segmentation.
    b.box('rear_bench_base',(-1.22,0,.90),(.43,1.35,.17),plastic,bevel=.035)
    for index,y in enumerate((-.46,0,.46)):
        prefix='rear_seat_'+str(index)
        b.box(prefix+'_cushion',(-1.18,y,1.025),(.48,.435,.15),fabric,bevel=.040)
        b.box(prefix+'_back',(-1.49,y,1.345),(.14,.435,.59),fabric,bevel=.043,rotation=(0,-.10,0))
        b.box(prefix+'_back_insert',(-1.412,y,1.35),(.016,.30,.43),fabric,bevel=.018,rotation=(0,-.10,0))
        for offset in (-.13,.13):
            b.tube(prefix+'_cushion_stitch',[(-1.35,y+offset,1.107),(-1.18,y+offset,1.109),(-1.00,y+offset,1.106)],.0018,m['roof'],resolution=1)
        for offset in (-.08,.08):
            b.cylinder(prefix+'_headrest_post',(-1.52,y+offset,1.60),(-1.53,y+offset,1.71),.0075,alloy,vertices=10)
        b.box(prefix+'_headrest',(-1.54,y,1.74),(.14,.28,.19),fabric,bevel=.040)
        b.box(prefix+'_buckle',(-1.08,y+.16,1.105),(.045,.029,.055),plastic,bevel=.008)
        b.box(prefix+'_buckle_release',(-1.056,y+.16,1.119),(.009,.020,.020),m['red'],bevel=.003)
    for side in (-1,1):
        ribbon('rear_shoulder_belt',[(-1.57,side*.736,1.78),(-1.50,side*.724,1.42),
                                    (-1.23,side*.70,1.07),(-1.30,side*.70,.86)],.038,rubber)
    ribbon('rear_center_belt',[(-1.57,.10,1.64),(-1.414,.10,1.46),(-1.38,-.08,1.12)],.037,rubber)

    # Dashboard is a shaped shallow assembly below the windshield sill.
    b.box('dashboard_upper',(.47,0,1.335),(.31,1.43,.18),plastic,bevel=.048)
    b.box('dashboard_lower',(.48,0,1.18),(.24,1.41,.15),plastic,bevel=.030)
    b.box('instrument_binnacle',(.31,.42,1.415),(.085,.37,.18),m['trim'],bevel=.035)
    for index,gy in enumerate((.335,.505)):
        b.cylinder('instrument_gauge_face',(.258,gy,1.421),(.266,gy,1.421),.064,m['display'],vertices=40)
        b.torus('instrument_gauge_bezel',(.255,gy,1.421),.064,.004,alloy,axis='X',major_segments=40,minor_segments=8)
        for tick in range(11):
            angle=math.radians(-130+tick*26)
            a=(.250,gy+.047*math.sin(angle),1.421+.047*math.cos(angle))
            c=(.250,gy+.054*math.sin(angle),1.421+.054*math.cos(angle))
            b.cylinder('instrument_tick',a,c,.0010,m['roof'],vertices=6)
        angle=math.radians(-42+index*27)
        b.cylinder('instrument_needle',(.245,gy,1.421),(.245,gy+.043*math.sin(angle),1.421+.043*math.cos(angle)),.0016,m['red'],vertices=6)
        b.cylinder('instrument_needle_hub',(.242,gy,1.421),(.247,gy,1.421),.005,alloy,vertices=12)

    b.box('center_display_bezel',(.300,-.02,1.36),(.026,.27,.18),m['trim'],bevel=.014)
    b.box('center_display',(.284,-.02,1.363),(.005,.23,.14),m['display'],bevel=.008)
    # Generic geometric interface details: no fabricated brand or readable claims.
    for index,width in enumerate((.14,.10,.16)):
        b.box('display_information_bar',(.280,-.04,1.398-index*.031),(.001,width,.005),m['roof'])
    b.box('display_indicator',(.279,.070,1.37),(.001,.012,.048),m['amber'],bevel=.002)
    for gy,gz in ((-.64,1.32),(.65,1.32),(-.09,1.223),(.10,1.223)):
        b.box('dashboard_air_vent',(.309,gy,gz),(.018,.145,.062),m['trim'],bevel=.010)
        for slat in range(4):
            b.box('air_vent_louvre',(.297,gy,gz-.021+slat*.014),(.012,.121,.004),alloy,bevel=.001)
    b.box('passenger_grab_handle',(.29,-.43,1.205),(.040,.32,.035),m['trim'],bevel=.015)
    for gy in (-.57,-.29):
        b.cylinder('grab_handle_mount',(.33,gy,1.205),(.29,gy,1.205),.012,steel,vertices=10)
    b.box('glovebox_lid',(.345,-.46,1.10),(.020,.43,.13),plastic,bevel=.018)
    b.box('glovebox_latch',(.332,-.46,1.145),(.014,.060,.019),alloy,bevel=.005)

    # Fixed steering wheel and stalks, angled toward the driver's seat.
    center=Vector((.145,.42,1.345))
    normal=Vector((-.89,0,.455)).normalized()
    b.cylinder('steering_column',(.375,.42,1.23),tuple(center),.025,steel,vertices=20)
    wheel=b.torus('steering_wheel_rim',tuple(center),.163,.016,m['trim'],axis='Z',major_segments=56,minor_segments=10)
    wheel.rotation_mode='QUATERNION'
    wheel.rotation_quaternion=Vector((0,0,1)).rotation_difference(normal)
    u=Vector((0,1,0)); v=normal.cross(u).normalized()
    b.cylinder('steering_wheel_hub',tuple(center-normal*.021),tuple(center+normal*.021),.052,plastic,vertices=28)
    for angle in (0,math.pi,math.pi/2):
        direction=math.cos(angle)*u+math.sin(angle)*v
        b.cylinder('steering_wheel_spoke',tuple(center+direction*.035),tuple(center+direction*.149),.014,alloy,vertices=10)
    for side in (-1,1):
        b.cylinder('steering_column_stalk',(.25,.42+side*.035,1.29),(.22,.42+side*.155,1.30),.007,plastic,vertices=10)
        b.sphere('steering_stalk_end',(.22,.42+side*.155,1.30),(.016,.026,.012),m['trim'],segments=12,rings=6)

    b.box('center_console',(-.09,0,.976),(.57,.23,.30),plastic,bevel=.035)
    b.box('center_console_top',(-.09,0,1.13),(.52,.215,.025),m['trim'],bevel=.018)
    for gx,gy,length in ((.065,-.035,.17),(-.14,.055,.10)):
        b.sphere('gear_lever_boot',(gx,gy,1.153),(.052,.040,.030),rubber,segments=20,rings=10)
        b.cylinder('gear_lever_shaft',(gx,gy,1.16),(gx-.025,gy,1.16+length),.008,alloy,vertices=12)
        b.sphere('gear_lever_knob',(gx-.025,gy,1.17+length),(.028,.025,.030),m['trim'],segments=20,rings=10)
    for gy in (-.055,.055):
        b.cylinder('console_cup_well',(-.29,gy,1.143),(-.29,gy,1.149),.034,rubber,vertices=24)
        b.torus('console_cup_rim',(-.29,gy,1.153),.034,.004,m['trim'],axis='Z',major_segments=24,minor_segments=6)
    b.cylinder('parking_brake_lever',(-.27,.135,1.01),(-.03,.135,1.085),.012,steel,vertices=12)
    b.cylinder('parking_brake_grip',(-.12,.135,1.057),(-.02,.135,1.088),.018,rubber,vertices=16)
    for gy,width in ((.30,.040),(.43,.055),(.56,.055)):
        b.cylinder('pedal_arm',(.51,gy,1.04),(.42,gy,.89),.009,steel,vertices=10)
        b.box('pedal_pad',(.405,gy,.885),(.030,width,.087),rubber,bevel=.009,rotation=(0,-.18,0))

    # Door cards stay beneath the glazing line; armrests remain inside the cabin.
    for side in (-1,1):
        for x in (-.04,-1.11):
            b.box('inner_door_card',(x,side*.785,1.12),(.69,.023,.37),plastic,bevel=.025)
            b.box('door_armrest',(x-.04,side*.753,1.24),(.40,.070,.065),fabric,bevel=.022)
            b.box('door_pull_recess',(x+.17,side*.758,1.315),(.11,.027,.050),m['trim'],bevel=.012)
            b.box('interior_door_handle',(x+.17,side*.739,1.315),(.075,.015,.017),alloy,bevel=.006)
            b.box('door_storage_pocket',(x-.02,side*.752,.965),(.45,.070,.12),plastic,bevel=.024)
    b.box('roof_console',(-.12,0,1.975),(.25,.17,.033),plastic,bevel=.015)
    b.box('cabin_dome_light',(-.12,0,1.953),(.13,.10,.008),m['roof'],bevel=.010)
    for side in (-1,1):
        b.box('sun_visor',(.27,side*.39,1.947),(.12,.40,.030),fabric,bevel=.012,rotation=(0,-.07,0))

    return {
        'seating': 'Two front seats and three-place rear bench with headrests and retracted belts.',
        'controls': 'Static steering wheel, geometric gauges, generic screen, gear/transfer selectors and pedals.',
        'limitations': 'Basic modeled cabin, no occupants, functioning instruments, animations or first-person interior certification.'
    }

if __name__ == "__main__":
    main()
