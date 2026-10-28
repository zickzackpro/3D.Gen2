#!/usr/bin/env python3
"""Original procedural vehicles and motorboat for Blender 4.3+.

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
        if obj.type not in {"MESH", "CURVE", "FONT"}:
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
    meshes = [obj for obj in builder.objects if obj.type == "MESH" and not obj.get('preserve_uv')]
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


def prepare_offroader_export_tangents(objects):
    """Use each face's normal-map UV for glTF's single tangent attribute.

    Blender's glTF exporter calculates tangents from the active render UV layer,
    even when a normal texture explicitly uses another UV set. Mixed leather /
    cloth meshes therefore need one per-corner export layer assembled from their
    material-specific coordinates. Original seam/lettering UVs are kept intact.
    Only call on triangulated export copies; source meshes and shaders stay editable.
    """
    material_copies = {}
    normal_uvs = {}
    for obj in objects:
        if obj.type != 'MESH':
            continue
        mesh = obj.data
        if not mesh.uv_layers.get('UVMap'):
            raise RuntimeError('Offroader export mesh has no authored UVMap: ' + obj.name)
        for index, original in enumerate(mesh.materials):
            if original is None:
                continue
            if original not in material_copies:
                material = original.copy()
                material_copies[original] = material
                normal_uv = 'UVMap'
                if material.use_nodes:
                    nodes, links = material.node_tree.nodes, material.node_tree.links
                    authored_uv = None
                    # Explicit bindings prevent changing active_render from
                    # redirecting implicit atlas textures to ExportTangentUV.
                    for node in list(nodes):
                        if node.type == 'TEX_IMAGE' and not node.inputs['Vector'].is_linked:
                            if authored_uv is None:
                                authored_uv = nodes.new('ShaderNodeUVMap')
                                authored_uv.uv_map = 'UVMap'
                            links.new(authored_uv.outputs['UV'], node.inputs['Vector'])
                        elif node.type == 'NORMAL_MAP' and not node.uv_map:
                            node.uv_map = 'UVMap'
                    shader = next((node for node in nodes if node.type == 'BSDF_PRINCIPLED'), None)
                    if shader and shader.inputs['Normal'].is_linked:
                        normal = shader.inputs['Normal'].links[0].from_node
                        if normal.type != 'NORMAL_MAP' or normal.space != 'TANGENT':
                            raise RuntimeError('Unsupported offroader normal shader: ' + material.name)
                        normal_uv = normal.uv_map or 'UVMap'
                normal_uvs[material] = normal_uv
            mesh.materials[index] = material_copies[original]
        target = mesh.uv_layers.get('ExportTangentUV') or mesh.uv_layers.new(name='ExportTangentUV')
        for polygon in mesh.polygons:
            material = mesh.materials[polygon.material_index] if mesh.materials else None
            name = normal_uvs.get(material, 'UVMap')
            source = mesh.uv_layers.get(name)
            if source is None:
                raise RuntimeError('Missing normal-map UV ' + name + ' on ' + obj.name)
            for index in polygon.loop_indices:
                target.data[index].uv = source.data[index].uv
        mesh.uv_layers.active = target
        target.active_render = True


def export_lod(builder, directory, level, ratio):
    collection = bpy.data.collections.new(builder.name + "_LOD" + str(level))
    bpy.context.scene.collection.children.link(collection)
    originals = [builder.root, *(obj for obj in builder.objects if level <= obj.get("max_lod", 2))]
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
    if builder.name == 'offroader':
        prepare_offroader_export_tangents(meshes)
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
    elevation = .85 if builder.name == "motorboat" else .64
    camera.location = center + Vector((span*1.02, -span*1.4, span*elevation))
    camera.rotation_euler = (center-camera.location).to_track_quat("-Z", "Y").to_euler()
    camera_data.type = "ORTHO"
    frame_scale = {"airplane": 1.05, "motorboat": 1.22}.get(builder.name, 1.32)
    camera_data.ortho_scale = span*frame_scale
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
    if builder.name == 'offroader':
        # Preview-only bounce fill makes the cabin readable through real glass.
        data=bpy.data.lights.new('Cabin studio bounce','AREA')
        data.energy=4.; data.shape='RECTANGLE'; data.size=.9; data.size_y=.5
        light=bpy.data.objects.new('Cabin studio bounce',data)
        studio.objects.link(light); light.location=(-.65,0,1.96)
        light.rotation_euler=(Vector((-.40,0,.95))-light.location).to_track_quat('-Z','Y').to_euler()
    floor_mat = bpy.data.materials.new("Studio floor")
    floor_mat.diffuse_color = (.055,.066,.084,1)
    floor_surface = bounds["min"][2]-.001
    if builder.name == "motorboat":
        # Preview-only water at the nominal design waterline, excluded from GLB.
        floor_surface = 0.
        floor_mat.name = "Studio water (render only)"
        floor_mat.use_nodes = True
        nodes, links = floor_mat.node_tree.nodes, floor_mat.node_tree.links
        shader = nodes.get("Principled BSDF")
        shader.inputs["Base Color"].default_value = (.018,.060,.080,1)
        shader.inputs["Roughness"].default_value = .22
        shader.inputs["Metallic"].default_value = .22
        shader.inputs["IOR"].default_value = 1.333
        coordinates = nodes.new("ShaderNodeTexCoord")
        noise = nodes.new("ShaderNodeTexNoise")
        noise.inputs["Scale"].default_value = 5.
        noise.inputs["Detail"].default_value = 2.
        bump = nodes.new("ShaderNodeBump")
        bump.inputs["Strength"].default_value = .24
        bump.inputs["Distance"].default_value = .075
        links.new(coordinates.outputs["Object"], noise.inputs["Vector"])
        links.new(noise.outputs["Fac"], bump.inputs["Height"])
        links.new(bump.outputs["Normal"], shader.inputs["Normal"])
    floor = builder.box("studio_water" if builder.name == "motorboat" else "studio_floor",
                        (0,0,floor_surface-.025), (span*200,span*200,.05), floor_mat)
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
    if builder.name == 'offroader':
        scene.cycles.max_bounces = 12
        scene.cycles.transmission_bounces = 8
        scene.cycles.caustics_reflective = False
        scene.cycles.caustics_refractive = False
        scene.cycles.sample_clamp_indirect = 3.
        scene.cycles.adaptive_threshold = .015
        scene.cycles.adaptive_min_samples = min(64,samples)
    # CPU-only distribution builds may omit OpenImageDenoise entirely.
    import _cycles
    scene.cycles.use_denoising = bool(getattr(_cycles,'with_openimagedenoise',False))
    if scene.cycles.use_denoising:
        scene.cycles.denoiser = 'OPENIMAGEDENOISE'
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
            "airplane": build_airplane, "offroader": build_offroader,
            "motorboat": build_motorboat}


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
    if name == 'offroader':
        prepare_offroader_body_surfacing(builder)
        prepare_offroader_insert_uvs(builder)
        prepare_offroader_surface_tints(builder)
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
        "request": "Enduro, Helikopter, Flugzeug, 4x4-Gelaendewagen und Motorboot als detaillierte Python-generierte Grundlage fuer spaetere AAA-Spiele.",
        "design_exclusions": "No logos, third-party meshes, paid generation or claim of engine certification.",
        "units": "metres", "source_axes": {"forward":"+X","left":"+Y","up":"+Z"},
        "glb_axes": {"forward":"+X","left":"-Z","up":"+Y"},
        "source_bounds": bounds, "lods": lods, "texture_size": options.texture_size,
        "source_mesh_objects": sum(obj.type == "MESH" for obj in builder.objects),
        "collision_proxies": len(collision_objects), "design": metadata,
        "limitations": ["Procedural production starting point, not AAA acceptance certification.",
                         "Automatic UVs and decimation require art review; texture detail is generic surface grain.",
                         "No baked high-poly normal/AO set, lightmap UV2, skeletal rig, physics or animation clips.",
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
    parser = argparse.ArgumentParser(prog="generate_vehicles.py", description="Blender: Enduro, Helikopter, Flugzeug, 4x4 und Motorboot mit PBR, UVs und LODs generieren.")
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


def offroader3_cockpit_round_outline(width, height, radius, cy=0., cz=0., steps=5):
    """Rounded rectangle in the YZ plane, with true circular corner samples."""
    result=[]
    for sy,sz,start in ((1,1,0),(-1,1,90),(-1,-1,180),(1,-1,270)):
        for i in range(steps+1):
            a=math.radians(start+90*i/steps)
            result.append((cy+sy*(width*.5-radius)+radius*math.cos(a),
                           cz+sz*(height*.5-radius)+radius*math.sin(a)))
    return result


def offroader3_cockpit_loft(b, name, rings, material, closed=False, caps=False):
    """Join explicit perimeter rings; closed joins an annular section at its back."""
    n=len(rings[0])
    vertices=[tuple(p) for ring in rings for p in ring]
    faces=[]
    for i in range(len(rings) if closed else len(rings)-1):
        k=(i+1)%len(rings)
        faces.extend((i*n+j,i*n+(j+1)%n,k*n+(j+1)%n,k*n+j) for j in range(n))
    if caps:
        faces.extend((tuple(reversed(range(n))),tuple((len(rings)-1)*n+j for j in range(n))))
    obj=b.mesh(name,vertices,faces,material,smooth=True)
    if caps:
        obj.data.polygons[-1].use_smooth=False
        obj.data.polygons[-2].use_smooth=False
    return obj


def _offroader3_upholstered_shell(b, name, location, size, leather, insert, *, back=False, bench=False, rotation=None):
    """Continuous sewn cover: the cloth shares vertices with the sculpted bolsters.

    The second grid closes the moulded underside/rear. Four narrow perimeter
    bands round the cover edge without stacking separate upholstery pillows.
    UVs on each cloth panel retain the existing baked seams and textile normals.
    """
    along=[-1,-.98,-.94,-.88,-.80,-.72,-.64,-.56,-.48,-.40,-.32,-.24,-.16,-.08,
           0,.08,.16,.24,.32,.40,.48,.56,.64,.72,.80,.88,.94,.98,1]
    across=[-1,-.96,-.90,-.84,-.78,-.72,-.66,-.60,-.50,-.40,-.30,-.20,-.10,
            0,.10,.20,.30,.40,.50,.60,.66,.72,.78,.84,.90,.96,1]
    panels=3 if bench else 1
    across=sorted(set(round((2*panel+v+1)/panels-1,8) for panel in range(panels) for v in across))
    na,nb=len(along),len(across)
    def local_lateral(v):
        panel=min(panels-1,max(0,int((v+1)*panels*.5)))
        return (v+1)*panels-2*panel-1,panel
    def smoothstep(a,c,x):
        t=max(0.,min(1.,(x-a)/(c-a)))
        return t*t*(3-2*t)
    def surface(a,v,front):
        q,_=local_lateral(v)
        rim=max(abs(a),abs(v))
        # A modest radius at the four outline corners, keeping seat centres broad.
        # Ease the material-region corners into the cover without adding a
        # raised insert lip. The warp vanishes at the outside silhouette.
        panel_round=math.exp(-((abs(q)-.66)/.23)**4)
        longitudinal_round=math.exp(-((abs(a)-.72)/.22)**4)
        shaped_a=a*(1-.045*longitudinal_round*smoothstep(.35,.66,abs(q)))
        shaped_v=v-.075*q*panel_round*smoothstep(.42,.72,abs(a))/panels
        longitudinal=shaped_a*math.sqrt(1-.18*abs(v)**8)
        lateral=shaped_v*math.sqrt(1-.16*abs(a)**8)
        side_lift=math.exp(-((abs(q)-.81)/.21)**2)
        centre=math.exp(-(q/.70)**6)
        seam=.0012*math.exp(-((abs(q)-.66)/.035)**2)*(1-smoothstep(.70,.82,abs(a)))
        seam+=.0010*math.exp(-((abs(a)-.72)/.035)**2)*(1-smoothstep(.64,.74,abs(q)))
        if back:
            depth,width,height=size
            lateral*=(1-.080*(a+.10)-.035*smoothstep(.35,1,a)) if not bench else 1-.035*(a+.10)
            y=lateral*width*.5; z=longitudinal*height*.5
            if front:
                lumbar=(.020 if not bench else .013)*math.exp(-((a+.40)/.40)**2)*centre
                shoulder=(.036 if not bench else .016)*side_lift*(.68+.32*math.exp(-((a-.16)/.68)**2))
                chest=-.005*math.exp(-((a-.25)/.48)**2)*centre
                join=.003*math.exp(-((a+.80)/.12)**2)*centre
                x=depth*.19+lumbar+shoulder+chest-join-seam-.023*smoothstep(.88,1,rim)
            else:
                x=-depth*.45-.006*(1-a*a)*(1-v*v)
            return (x,y,z)
        length,width,height=size
        lateral*=1-.035*max(0.,-a)
        x=longitudinal*length*.5; y=lateral*width*.5
        if front:
            thigh=(.011 if not bench else .008)*math.exp(-((a-.43)/.43)**2)*math.exp(-(q/.82)**6)
            dish=(.022 if not bench else .016)*math.exp(-((a+.20)/.59)**2)*centre
            shoulder=(.027 if not bench else .013)*side_lift*(1-.42*max(0.,a))
            join=.004*math.exp(-((a+.80)/.13)**2)*centre
            waterfall=(.017 if not bench else .014)*smoothstep(.60,1,a)
            z=height*.36+thigh-dish+shoulder-join-seam-.021*smoothstep(.84,1,rim)-waterfall
        else:
            z=-height*.43-.005*(1-a*a)*(1-v*v)
        return (x,y,z)
    vertices=[surface(a,v,True) for a in along for v in across]
    vertices += [surface(a,v,False) for a in along for v in across]
    count=na*nb
    faces=[]; cloth=[]
    for i in range(na-1):
        for j in range(nb-1):
            start=i*nb+j
            face=(start,start+nb,start+nb+1,start+1)
            faces.append(face)
            am=(along[i]+along[i+1])*.5
            vm=(across[j]+across[j+1])*.5
            q,panel=local_lateral(vm)
            cloth.append(panel if abs(am)<.72 and abs(q)<.66 else -1)
    for i in range(na-1):
        for j in range(nb-1):
            start=count+i*nb+j
            faces.append((start+1,start+nb+1,start+nb,start)); cloth.append(-1)
    boundary=list(range(nb))
    boundary += [i*nb+nb-1 for i in range(1,na)]
    boundary += [(na-1)*nb+j for j in range(nb-2,-1,-1)]
    boundary += [i*nb for i in range(na-2,0,-1)]
    previous=boundary
    for t in (.20,.50,.80,1.):
        current=[]
        for idx in boundary:
            a=along[idx//nb]; v=across[idx%nb]
            front=Vector(vertices[idx]); rear=Vector(vertices[count+idx])
            p=front.lerp(rear,t)
            # A 2 mm cover roll is enough; larger rolls look inflated.
            swell=.0015*math.sin(math.pi*t)
            if back:
                p.y+=math.copysign(swell*abs(v)**8,v); p.z+=math.copysign(swell*abs(a)**8,a)
            else:
                p.x+=math.copysign(swell*abs(a)**8,a); p.y+=math.copysign(swell*abs(v)**8,v)
            if t==1.:
                current.append(count+idx)
            else:
                current.append(len(vertices)); vertices.append(tuple(p))
        for j in range(len(boundary)):
            k=(j+1)%len(boundary)
            faces.append((previous[j],previous[k],current[k],current[j])); cloth.append(-1)
        previous=current
    obj=b.mesh(name,vertices,faces,leather,smooth=True)
    obj.data.materials.append(insert)
    uv=obj.data.uv_layers.new(name='UVMap')
    for polygon,panel in zip(obj.data.polygons,cloth):
        polygon.material_index=1 if panel>=0 else 0
        for loop_index in polygon.loop_indices:
            idx=obj.data.loops[loop_index].vertex_index
            if panel>=0:
                a=along[idx//nb]; v=across[idx%nb]
                local_v=(v+1)*panels-2*panel-1
                coord=((local_v+.66)/1.32,(a+.72)/1.44)
            else:
                p=obj.data.vertices[idx].co
                coord=(p.y/size[1]+.5,(p.z/size[2] if back else p.x/size[0])+.5)
            uv.data[loop_index].uv=coord
    obj.location=location
    if rotation is not None:
        obj.rotation_euler=rotation
    obj['preserve_uv']=True
    obj['seat_surface_uv']=True
    obj['seat_construction']='Continuous closed cover; inset cloth and integrated bolster share vertices'
    obj['seat_anatomy']='Tapered shoulders, lumbar support and a shallow chest cradle' if back else 'Soft hip dish, broad side support and a rolled thigh edge'
    return obj


def offroader_finish_materials(b, m):
    """Author portable, metric-scale finish textures without changing seam atlases.

    Call after the baseline upholstery/sidewall textures and before building parts.
    FinishUV is populated after construction by prepare_offroader_surface_tints.
    """
    def image(material, suffix, pixels):
        name=material.name+'_'+suffix
        im=bpy.data.images.new(name,pixels.shape[1],pixels.shape[0],alpha=True)
        im.colorspace_settings.name='Non-Color'
        im.pixels.foreach_set(np.asarray(pixels,dtype=np.float32).ravel())
        im.pack()
        return im

    def finish(material, kind, roughness, metallic, tile, amplitude, variation):
        n=b.texture_size
        seed=int(hashlib.sha256((material.name+'_finish_'+str(b.seed)).encode()).hexdigest()[:8],16)
        rng=np.random.default_rng(seed)
        yy,xx=np.mgrid[:n,:n]; u=(xx+.5)/n; v=(yy+.5)/n
        freq=np.fft.fftfreq(n)
        radial=freq[:,None]**2+freq[None,:]**2
        def grain(cells):
            noise=rng.standard_normal((n,n))
            sigma=n/(cells*3.)
            field=np.fft.ifft2(np.fft.fft2(noise)*np.exp(-2*math.pi**2*sigma*sigma*radial)).real
            return field/max(float(field.std()),1.e-8)
        fine=grain(70); medium=grain(24); broad=grain(7)
        if kind=='paint':
            height=amplitude*(.72*medium+.28*fine)
            rough=roughness+variation*(.7*medium+.3*broad)
        elif kind=='leather':
            # Irregular connected grain and shallow pores, without baked shading.
            pebbles=np.tanh(medium*.9)-.30*np.maximum(fine-.7,0)
            height=amplitude*(pebbles+.18*fine)
            rough=roughness+variation*(.52*medium+.28*fine+.20*broad)
        elif kind=='cloth':
            warp=np.sin(math.tau*32*u+.12*np.sin(math.tau*4*v))
            weft=np.sin(math.tau*32*v+.12*np.sin(math.tau*4*u))
            alternating=np.cos(math.tau*16*u)*np.cos(math.tau*16*v)
            height=amplitude*(.42*warp+.42*weft+.16*alternating+.10*fine)
            rough=roughness+variation*(.42*warp+.42*weft+.16*medium)
        elif kind=='brushed':
            height=amplitude*(.6*grain(90)+.4*np.sin(math.tau*86*u+.4*medium))
            rough=roughness+variation*(.55*medium+.25*fine+.20*broad)
        else:
            height=amplitude*(.72*medium+.28*fine)
            rough=roughness+variation*(.58*medium+.22*fine+.20*broad)
        # Periodic central differences preserve the normal at texture tile edges.
        du=(np.roll(height,-1,1)-np.roll(height,1,1))/(2*tile/n)
        dv=(np.roll(height,-1,0)-np.roll(height,1,0))/(2*tile/n)
        normals=np.stack((-du,-dv,np.ones_like(du)),axis=-1)
        normals/=np.linalg.norm(normals,axis=-1,keepdims=True)
        normal=np.ones((n,n,4),dtype=np.float32); normal[:,:,:3]=normals*.5+.5
        orm=np.ones_like(normal); orm[:,:,1]=np.clip(rough,.025,1.); orm[:,:,2]=metallic
        nodes,links=material.node_tree.nodes,material.node_tree.links
        shader=nodes.get('Principled BSDF')
        shader.inputs['Roughness'].default_value=roughness
        shader.inputs['Metallic'].default_value=metallic
        uv=nodes.new('ShaderNodeUVMap'); uv.uv_map='FinishUV'
        for node in nodes:
            if node.type=='TEX_IMAGE' and node.image:
                if node.image.name.endswith('_metallic_roughness'):
                    node.image=image(material,'finish_metallic_roughness',orm)
                    links.new(uv.outputs['UV'],node.inputs['Vector'])
                elif node.image.name.endswith('_normal'):
                    node.image=image(material,'finish_normal',normal)
                    links.new(uv.outputs['UV'],node.inputs['Vector'])
            elif node.type=='NORMAL_MAP':
                node.uv_map='FinishUV'; node.inputs['Strength'].default_value=1.
        material['finish_tile_m']=tile
        material['finish_kind']=kind
        material['finish_uv']='FinishUV'

    # Distinct dielectric finishes. No fake lighting or all-over scratch pattern.
    recipes={
        'paint':('paint',.255,0.,.070,.0000028,.006),
        'roof':('paint',.325,0.,.070,.0000045,.012),
        'trim':('polymer',.640,0.,.065,.000014,.020),
        'interior':('polymer',.690,0.,.070,.000018,.025),
        'leather':('leather',.575,0.,.080,.000025,.035),
        'rubber':('rubber',.855,0.,.085,.000013,.017),
        'tread':('rubber',.895,0.,.080,.000018,.020),
        'fabric':('cloth',.790,0.,.040,.000018,.033),
        'alloy':('brushed',.320,.90,.090,.0000025,.016),
        'steel':('metal',.415,.82,.095,.000004,.020),
        'bronze':('metal',.325,.86,.085,.000003,.014),
        'cast':('cast',.515,.85,.075,.000025,.029),
    }
    for key,recipe in recipes.items():
        finish(m[key],*recipe)
    # The existing seat insert normal encodes actual seams in UVMap, and its
    # original roughness texture is retained along with the sidewall lettering.
    # Contact and exposed mechanical surfaces get only four shared extra shaders.
    m['brake_contact']=b.mat('offroader_brake_contact',(.36,.39,.40),metallic=.94,roughness=.34)
    m['exhaust_finish']=b.mat('offroader_heat_tinted_stainless',(.27,.265,.245),metallic=.90,roughness=.40)
    m['step_contact']=b.mat('offroader_step_contact_polymer',(.022,.027,.029),roughness=.49)
    m['handle_contact']=b.mat('offroader_handpolished_alloy',(.43,.48,.51),metallic=.90,roughness=.245)
    finish(m['exhaust_finish'],'metal',.405,.90,.10,.000004,.020)
    finish(m['step_contact'],'polymer',.495,0.,.065,.000010,.025)
    finish(m['handle_contact'],'brushed',.245,.90,.060,.0000015,.012)
    brake=m['brake_contact']
    n=max(1024,b.texture_size)
    yy,xx=np.mgrid[:n,:n]; x=((xx+.5)/n-.5)*.36; y=((yy+.5)/n-.5)*.36
    radius=np.hypot(x,y)
    contact=np.clip((radius-.078)/.008,0,1)*np.clip((.171-radius)/.006,0,1)
    # Micron-scale scoring only changes the highlight; no floating ring geometry.
    grooves=np.sin(radius*math.tau/.00165)+.34*np.sin(radius*math.tau/.00073+.7)
    height=contact*grooves*.0000018
    dv,du=np.gradient(height,.36/n,.36/n)
    normals=np.stack((-du,-dv,np.ones_like(du)),axis=-1)
    normals/=np.linalg.norm(normals,axis=-1,keepdims=True)
    normal=np.ones((n,n,4),dtype=np.float32); normal[:,:,:3]=normals*.5+.5
    orm=np.ones_like(normal)
    orm[:,:,1]=np.clip(.46-contact*.13+contact*grooves*.027,.22,.50)
    orm[:,:,2]=.94
    nodes,links=brake.node_tree.nodes,brake.node_tree.links
    uv=nodes.new('ShaderNodeUVMap'); uv.uv_map='BrakeUV'
    for node in nodes:
        if node.type=='TEX_IMAGE' and node.image:
            pixels=normal if node.image.name.endswith('_normal') else orm
            node.image=image(brake,'contact_normal' if pixels is normal else 'contact_metallic_roughness',pixels)
            links.new(uv.outputs['UV'],node.inputs['Vector'])
        elif node.type=='NORMAL_MAP':
            node.uv_map='BrakeUV'; node.inputs['Strength'].default_value=1.
    brake['finish_uv']='BrakeUV'
    brake['finish_kind']='circumferential brake contact'


def offroader_mechanical_wear(b, m):
    """Assign existing parts to localized contact finishes; add no geometry."""
    for obj in b.objects:
        if obj.type not in {'MESH','CURVE'} or not obj.data.materials:
            continue
        name=obj.name.split('.')[0]
        target=None
        if name.endswith('_brake_rotor'):
            target=m['brake_contact']; obj['mechanical_finish']='circumferential disc wear'
        elif any(token in name for token in ('exhaust_header','exhaust_downpipe','underbody_exhaust','exhaust_silencer','exhaust_outlet')) and 'shadow' not in name:
            target=m['exhaust_finish']; obj['mechanical_finish']='restrained heat tint'
        elif name.endswith('_door_handle') or name.endswith('_interior_door_handle'):
            target=m['handle_contact']; obj['mechanical_finish']='hand contact polish'
        elif any(token in name for token in ('running_board','step_grip')):
            # Only upward-facing original faces receive the slightly smoother
            # contact finish. The construction bevel inherits the adjacent slot.
            if obj.type=='MESH':
                obj.data.materials.append(m['step_contact'])
                slot=len(obj.data.materials)-1
                for polygon in obj.data.polygons:
                    if polygon.normal.z>.7:
                        polygon.material_index=slot
                obj['mechanical_finish']='light step contact polish'
        if target is not None:
            for index in range(len(obj.data.materials)):
                obj.data.materials[index]=target
    b.root['material_finish_notes']='Original image PBR: metric paint, polymer, leather, rubber and cloth grain; shared brake contact atlas; slight contact polish and exhaust heat tint. No baked lighting.'


def offroader_access_path(b, name, points, radius, material, role=None):
    """Straight stamped edges and seals without Bezier corner overshoot."""
    obj = b.tube(name, points, radius, material, resolution=1)
    for spline in obj.data.splines:
        for point in spline.bezier_points:
            point.handle_left_type = point.handle_right_type = 'VECTOR'
    if role:
        obj['access_role'] = role
    return obj


def offroader_access_panel(b, name, outline, offset, material, bevel=0, role=None):
    """A consistently outward-facing closed sheet, including concave outlines."""
    n = len(outline)
    delta = Vector(offset) * .5
    vertices = [tuple(Vector(p)-delta) for p in outline]
    vertices += [tuple(Vector(p)+delta) for p in outline]
    faces = [tuple(reversed(range(n))), tuple(range(n,2*n))]
    faces += [(i,(i+1)%n,(i+1)%n+n,i+n) for i in range(n)]
    obj = b.mesh(name, vertices, faces, material, bevel=bevel)
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    bmesh.ops.recalc_face_normals(bm, faces=list(bm.faces))
    bm.to_mesh(obj.data)
    bm.free()
    if role:
        obj['access_role'] = role
    return obj


def offroader_rounded_outline(points, radius, steps=5):
    """Trim polygon corners with tangent quadratic arcs in the original plane."""
    points=[Vector(p) for p in points]
    result=[]
    for i,p in enumerate(points):
        before=points[i-1]-p
        after=points[(i+1)%len(points)]-p
        distance=min(radius,before.length*.24,after.length*.24)
        entry=p+before.normalized()*distance
        leave=p+after.normalized()*distance
        for j in range(steps+1):
            t=j/steps
            result.append(tuple(entry*(1-t)**2+p*(2*t*(1-t))+leave*t*t))
    return result

def offroader_crowned_door_skin(b, name, outline, side, material, role):
    """Closed stamped skin with a small compound crown and unchanged shut line."""
    obj=offroader_access_panel(b,name,[(x,side*.842,z) for x,z in outline],
                               (0,.024,0),material,role=role)
    bm=bmesh.new(); bm.from_mesh(obj.data)
    # Resolve the rolled waist directly in the skin, including its inner face.
    for z in (1.405,1.425,1.445,1.465,1.480):
        bmesh.ops.bisect_plane(bm,geom=list(bm.verts)+list(bm.edges)+list(bm.faces),
                              plane_co=(0,0,z),plane_no=(0,0,1),dist=1.e-7)
    bmesh.ops.triangulate(bm,faces=list(bm.faces))
    bmesh.ops.subdivide_edges(bm,edges=list(bm.edges),cuts=4,use_grid_fill=True)
    x0=min(p[0] for p in outline); x1=max(p[0] for p in outline)
    z0=min(p[1] for p in outline); z1=max(p[1] for p in outline)
    polygon=[Vector(p) for p in outline]
    for v in bm.verts:
        point=Vector((v.co.x,v.co.z))
        distance=min((point-(a+(c-a)*max(0,min(1,(point-a).dot(c-a)/max((c-a).length_squared,1e-12))))).length
                     for a,c in zip(polygon,polygon[1:]+polygon[:1]))
        u=max(0,min(1,(v.co.x-x0)/(x1-x0)))
        t=max(0,min(1,(v.co.z-z0)/(z1-z0)))
        # Edge fade is zero on the exact outline and softly enters the press.
        fade=min(distance/.060,1.)
        fade=fade*fade*(3-2*fade)
        crown=.019*(max(0,math.sin(math.pi*u)*math.sin(math.pi*t))**.75)*fade
        waist=max(0.,min(1.,(v.co.z-1.405)/.087))
        waist=waist*waist*(3-2*waist)
        v.co.y+=side*(crown-.020*waist)
    bmesh.ops.recalc_face_normals(bm,faces=list(bm.faces));bm.to_mesh(obj.data);bm.free()
    for face in obj.data.polygons:
        face.use_smooth=abs(face.normal.y)>.7
    bevel=obj.modifiers.new('Rolled door skin edge','BEVEL')
    bevel.width=.0015;bevel.segments=2;bevel.limit_method='ANGLE'
    obj['integrated_window_waist']=True
    obj['panel_crown_m']=.019
    obj['component']='opening_door_outer_skin'
    return obj


def offroader_form_fixed_side(obj, side):
    """Gently press the fixed fender skins; cabin shut-line borders stay put."""
    bm=bmesh.new();bm.from_mesh(obj.data)
    # Resolve the fixed quarter's matching rolled waist before cutting the
    # wing at its shoulder transition.
    for z in (1.405,1.425,1.445,1.465,1.480):
        bmesh.ops.bisect_plane(bm,geom=list(bm.verts)+list(bm.edges)+list(bm.faces),
                              plane_co=(0,0,z),plane_no=(0,0,1),dist=1.e-7)
    bmesh.ops.bisect_plane(bm,geom=list(bm.verts)+list(bm.edges)+list(bm.faces),
                          plane_co=(.62,0,0),plane_no=(1,0,0),dist=1.e-6)
    bmesh.ops.triangulate(bm,faces=list(bm.faces))
    front_edges=[edge for edge in bm.edges
                 if any(face.calc_center_median().x>.62001 for face in edge.link_faces)]
    bmesh.ops.subdivide_edges(bm,edges=front_edges,cuts=3,use_grid_fill=True)
    for v in bm.verts:
        x,z=v.co.x,v.co.z
        if x<=.62:
            waist=max(0.,min(1.,(z-1.405)/.087))
            v.co.y-=side*.022*waist*waist*(3-2*waist)
        if x>.62:
            blend=max(0,min(1,(x-.62)/.16))
            blend=blend*blend*(3-2*blend)
            height=max(0,min(1,(z-.68)/.74))
            shoulder=-.020*max(0,min(1,(z-1.15)/.24))**2
            arch_crown=.012*math.sin(math.pi*height)*max(0,math.sin(math.pi*(x-.62)/1.43))
            v.co.y+=side*blend*(shoulder+arch_crown)
    bmesh.ops.recalc_face_normals(bm,faces=list(bm.faces));bm.to_mesh(obj.data);bm.free()
    for face in obj.data.polygons:
        face.use_smooth=abs(face.normal.y)>.7
    obj['formed_front_shoulder_inset_m']=.020


def offroader_cabin_taper_y(y,z):
    t=max(0.,min(1.,(z-1.43)/.60))
    return y-math.copysign(.035*t*min(abs(y)/.65,1.),y)

def prepare_offroader_body_surfacing(b):
    """Resolve the extra cabin tumblehome coherently, without moving hinge axes.

    Side panes remain planar: the offset is linear in height and identical on
    their inner/outer surfaces. Existing seat shells, wheel geometry, new roof
    equipment and hinge pins are excluded. Rebuild shading normals afterwards.
    """
    tokens=('pressed_cabin_pillar','quarter_glass','quarter_window_seal',
            'quarter_lower_rail','quarter_belt_moulding','window_header','roof_gutter',
            'door_glass','door_window_frame','door_window_seal','door_window_lower_rail',
            'door_belt_moulding','door_inner_glass_scraper','painted_jamb_return',
            'body_weather_seal','mirror_arm','mirror_mount','mirror_housing','mirror_glass',
            'windshield','windscreen_wiper','rear_corner_pillar','tailgate_glass',
            'tailgate_window_frame','tailgate_window_seal','tailgate_mobile_window',
            'rear_glass_defroster','rear_defroster_terminal','rear_window_wiper',
            'snorkel','shoulder_belt','upper_belt_anchor','headliner_side',
            'ceiling_grab_handle','sun_visor','roof_console','cabin_dome_light',
            'rearview_mirror')
    for obj in b.objects:
        if obj.type!='MESH' or not any(token in obj.name for token in tokens):
            continue
        matrix=world_matrix(obj)
        inverse=matrix.inverted()
        for vertex in obj.data.vertices:
            point=matrix@vertex.co
            point.y=offroader_cabin_taper_y(point.y,point.z)
            vertex.co=inverse@point
        obj.data.update()
        # Evaluated weighted normals refer to the former positions. Zero custom
        # normals request Blender's fresh geometric corner normals instead.
        if obj.data.has_custom_normals:
            obj.data.normals_split_custom_set([(0.,0.,0.)]*len(obj.data.loops))
        obj['extra_upper_cabin_taper_m']=.035

def offroader4_round_plan(length, width, radius, steps=8):
    """Counterclockwise rounded rectangle in the XY plane."""
    result=[]
    for sx,sy,start in ((1,1,0),(-1,1,90),(-1,-1,180),(1,-1,270)):
        for i in range(steps+1):
            angle=math.radians(start+90*i/steps)
            result.append((sx*(length*.5-radius)+radius*math.cos(angle),
                           sy*(width*.5-radius)+radius*math.sin(angle)))
    return result

def offroader4_closed_rings(b, name, rings, material, smooth=True):
    """Closed manufactured shell from equal-sized explicit section rings."""
    count=len(rings[0])
    vertices=[tuple(p) for ring in rings for p in ring]
    faces=[tuple(reversed(range(count)))]
    for ring in range(len(rings)-1):
        faces.extend((ring*count+i,ring*count+(i+1)%count,
                      (ring+1)*count+(i+1)%count,(ring+1)*count+i)
                     for i in range(count))
    faces.append(tuple((len(rings)-1)*count+i for i in range(count)))
    obj=b.mesh(name,vertices,faces,material,smooth=smooth)
    bm=bmesh.new(); bm.from_mesh(obj.data)
    bmesh.ops.recalc_face_normals(bm,faces=list(bm.faces))
    bm.to_mesh(obj.data);bm.free()
    obj.data.polygons[0].use_smooth=False
    obj.data.polygons[-1].use_smooth=False
    return obj

def build_offroader_roof_equipment(b, m):
    """Thin rolled roof edge, broad compound crown and a restrained low rack."""
    outline=offroader4_round_plan(2.40,1.49,.105,steps=10)
    profile=[(.06,2.075),(.35,2.072),(.65,2.059),(.90,2.044),
             (.985,2.042),(1.000,2.048),(1.002,2.061),(.995,2.073),
             (.96,2.078),(.80,2.090),(.60,2.101),(.30,2.109),(.06,2.111)]
    rings=[[(x*scale-.80,y*scale,z) for x,y in outline] for scale,z in profile]
    roof=offroader4_closed_rings(b,'roof',rings,m['roof'])
    roof['component']='crowned_thin_perimeter_roof'
    roof['perimeter_thickness_m']=.032
    roof['plan_corner_radius_m']=.105
    roof['roof_crown_m']=.037
    steel,trim=m['steel'],m['trim']
    for side in (-1,1):
        for x in (-1.60,-.78,.045):
            b.box('roof_rack_gasket',(x,side*.62,2.087),(.101,.080,.014),m['rubber'],bevel=.006)
            b.box('roof_rack_foot',(x,side*.62,2.103),(.091,.069,.028),trim,bevel=.009)
            b.cylinder('roof_rack_stanchion',(x,side*.62,2.108),
                       (x,side*.62,2.193),.011,steel,vertices=12)
    rail_outline=offroader4_round_plan(1.94,1.26,.062,steps=6)
    rail=b.tube('roof_rack_perimeter_rail',[(x-.81,y,2.193) for x,y in rail_outline],.014,steel)
    rail.data.splines[0].use_cyclic_u=True
    for point in rail.data.splines[0].bezier_points:
        point.handle_left_type=point.handle_right_type='VECTOR'
    for side in (-1,1):
        b.cylinder('roof_rack_floor_rail',(-1.76,side*.62,2.151),
                   (.14,side*.62,2.151),.012,steel,vertices=12)
    for x in (.08,-.28,-.64,-1.00,-1.36,-1.69):
        b.box('roof_rack_crossbar',(x,0,2.151),(.036,1.24,.018),steel,bevel=.006)
    for side in (-1,1):
        b.box('roof_led_mount',(.128,side*.27,2.154),(.065,.022,.018),steel,bevel=.004)
    b.box('roof_led_bar',(.175,0,2.157),(.070,.80,.044),trim,bevel=.012)
    for y in (-.30,-.20,-.10,0,.10,.20,.30):
        b.box('roof_led_lens',(.216,y,2.157),(.012,.068,.025),m['lamp'],bevel=.005)
    b.box('short_roof_antenna_base',(-1.84,.25,2.09),(.037,.037,.026),trim,bevel=.009)
    b.cylinder('short_roof_antenna',(-1.84,.25,2.098),(-1.88,.25,2.30),.0065,trim,vertices=10,radius2=.0025)

def offroader4_bumper_section(y, front=True):
    """Center, radii and roll-up of the smoothly swept bumper wing."""
    t=abs(y)/.97
    if front:
        center_x=2.142-.135*t**3
        center_z=.735+.033*t*t
        depth=.145-.040*t**4
        half_height=.103-.040*t**5
    else:
        center_x=-2.075+.105*t**3
        center_z=.680+.022*t*t
        depth=.125-.034*t**4
        half_height=.082-.030*t**5
    return center_x,center_z,depth,half_height

def offroader4_bumper_face(y, front=True):
    cx,cz,depth,height=offroader4_bumper_section(y,front)
    return cx+(depth if front else -depth)

def offroader4_bumper_shell(b,name,material,front=True):
    rings=[]
    # End sections contract in the final few centimeters to make rounded caps.
    ys=[-.978,-.975,-.968,-.952,-.928]+[-.90+i*.05 for i in range(37)]+[.928,.952,.968,.975,.978]
    for y in ys:
        cx,cz,depth,height=offroader4_bumper_section(y,front)
        end_scale=1.
        if abs(y)>.928:
            a=min(1.,(abs(y)-.928)/.050)
            end_scale=max(.045,math.sqrt(max(0.,1.-a*a)))
        depth*=end_scale;height*=end_scale
        radius=min(.027,height*.40,depth*.35)
        # Existing rectangle helper is independent of the actual plane:
        # length/depth corresponds to X and width/height corresponds to Z.
        section=offroader4_round_plan(depth*2,height*2,radius,steps=6)
        rings.append([(cx+x,y,cz+z) for x,z in section])
    obj=offroader4_closed_rings(b,name,rings,material)
    obj['component']='swept_bumper_with_rolled_wings'
    obj['wing_sweep_m']=.135 if front else .105
    return obj

def offroader4_bumper_cut(b,bumper,name,location,size,rotation=None,bevel=.02):
    cutter=b.box(name,location,size,None,bevel=bevel,rotation=rotation)
    bpy.context.view_layer.update()
    boolean=bumper.modifiers.new('Integrated bumper opening','BOOLEAN')
    boolean.operation='DIFFERENCE';boolean.solver='EXACT';boolean.object=cutter
    bpy.context.view_layer.objects.active=bumper
    bpy.ops.object.modifier_apply(modifier=boolean.name)
    b.objects.remove(cutter);bpy.data.objects.remove(cutter,do_unlink=True)

def offroader4_fog_well(b,m,y):
    face=offroader4_bumper_face(y)
    # The outer wing slopes toward the wheel; the lens shares its tangent plane.
    derivative=(offroader4_bumper_face(y+.0001)-offroader4_bumper_face(y-.0001))/.0002
    angle=math.atan(-derivative)
    z=.75+.033*(abs(y)/.97)**2
    def point(x,v,w):
        return (face+x*math.cos(angle)-v*math.sin(angle),
                y+x*math.sin(angle)+v*math.cos(angle),z+w)
    rings=[]
    for x,width,height,radius in ((.002,.220,.100,.022),(-.027,.220,.100,.022),
                                  (-.027,.170,.062,.014),(.005,.170,.062,.014)):
        rings.append([point(x,v,w) for v,w in offroader4_round_plan(width,height,radius,steps=6)])
    count=len(rings[0]);vertices=[p for ring in rings for p in ring]
    faces=[(j*count+i,j*count+(i+1)%count,((j+1)%4)*count+(i+1)%count,((j+1)%4)*count+i)
           for j in range(4) for i in range(count)]
    well=b.mesh('bumper_fog_recess',vertices,faces,m['steel'],smooth=True)
    bm=bmesh.new();bm.from_mesh(well.data);bmesh.ops.recalc_face_normals(bm,faces=list(bm.faces));bm.to_mesh(well.data);bm.free()
    center=point(-.023,0,0)
    b.box('bumper_fog_lens',center,(.006,.162,.054),m['lamp'],bevel=.009,rotation=(0,0,angle))
    return face,z,angle

def build_offroader_bumpers(b,m):
    """Swept wings, integrated fog wells and a recessed mechanical winch mouth."""
    steel,trim=m['steel'],m['trim']
    front=offroader4_bumper_shell(b,'front_bumper',trim,True)
    rear=offroader4_bumper_shell(b,'rear_bumper',trim,False)
    # The fairlead occupies a real opening instead of a solid bumper face.
    offroader4_bumper_cut(b,front,'fairlead_aperture_tool',(2.18,0,.824),(.50,.382,.108),bevel=.012)
    for side in (-1,1):
        y=side*.755
        face,z,angle=offroader4_fog_well(b,m,y)
        offroader4_bumper_cut(b,front,'fog_aperture_tool',(face-.045*math.cos(angle),y-.045*math.sin(angle),z),
                              (.26,.226,.106),rotation=(0,0,angle),bevel=.023)
        b.box('fairlead_mount',(2.287,side*.207,.826),(.028,.036,.088),steel,bevel=.007)
        b.cylinder('fairlead_mount_bolt',(2.295,side*.207,.816),(2.310,side*.207,.816),.006,m['alloy'],vertices=6)
        b.box('recovery_mount',(2.25,side*.53,.62),(.11,.07,.10),steel,bevel=.012)
        b.torus('front_recovery_shackle',(2.31,side*.53,.603),.045,.012,m['bronze'],axis='X',major_segments=24,minor_segments=8)
        b.cylinder('rear_recovery_eye',(-2.20,side*.57,.62),(-2.25,side*.57,.62),.035,steel,vertices=16)
    b.box('front_bumper_skid',(2.16,0,.600),(.16,1.10,.115),m['alloy'],bevel=.025,rotation=(0,-.22,0))
    b.box('rear_bumper_step_pad',(-2.075,0,.764),(.12,1.10,.008),m['rubber'],bevel=.005)
    for x in (-2.106,-2.075,-2.044):
        b.box('rear_bumper_step_rib',(x,0,.769),(.007,1.04,.002),trim,bevel=.001)
    b.box('winch_base',(2.10,0,.835),(.30,.59,.055),steel,bevel=.014)
    b.cylinder('winch_drum',(2.11,-.18,.897),(2.11,.18,.897),.072,steel,vertices=24)
    for y in (-.15,-.105,-.06,-.015,.03,.075,.12,.165):
        b.torus('winch_cable_winding',(2.11,y,.897),.072,.009,m['alloy'],axis='Y',major_segments=24,minor_segments=6)
    b.cylinder('winch_motor',(2.11,.19,.897),(2.11,.30,.897),.065,trim,vertices=24)
    for z in (.795,.855):
        b.cylinder('fairlead_horizontal_roller',(2.304,-.16,z),(2.304,.16,z),.015,m['alloy'],vertices=16)
    for y in (-.165,.165):
        b.cylinder('fairlead_vertical_roller',(2.304,y,.795),(2.304,y,.855),.018,m['alloy'],vertices=16)
    b.tube('winch_lead',[(2.18,0,.897),(2.245,0,.885),(2.285,0,.85),
                         (2.324,0,.825),(2.34,0,.79)],.008,steel)
    b.torus('winch_hook',(2.34,0,.77),.025,.008,m['bronze'],axis='Y',major_segments=20,minor_segments=8)
    b.box('rear_tow_receiver',(-2.15,0,.53),(.24,.095,.10),steel,bevel=.012)
    b.cylinder('tow_ball_stem',(-2.29,0,.50),(-2.29,0,.57),.023,steel,vertices=16)
    b.sphere('tow_ball',(-2.29,0,.60),(.035,.035,.035),m['chrome'],segments=16,rings=8)


def offroader_access_side_points(points, side, inset=0):
    return [(x,side*(.84-.09*max(0.,min(1.,(z-1.43)/.60))-inset),z)
            for x,z in points]


def offroader_access_glass(b, m, name, outline, offset, role=None):
    """Clear faces and a subtly green physical glass edge; no dark visibility mask."""
    edge = b.mat('offroader_laminated_glass_edge', (.29,.49,.37), roughness=.12, transmission=.82)
    obj = offroader_access_panel(b,name,outline,offset,m['glass'],role=role)
    obj.data.materials.append(edge)
    for face in obj.data.polygons:
        if len(face.vertices) == 4 and face.index >= 2:
            face.material_index = 1
    obj['glazing_thickness_m'] = Vector(offset).length
    obj['component'] = 'closed_clear_glazing_with_green_edge'
    return obj


def offroader_access_jamb(b, m, name, outline, outer_points, inner_points):
    """Body-fixed folded returns around a complete access aperture."""
    # Two parallel returns form a thin steel stamping, not a solid wall in
    # the doorway. The upper run lives directly below the fixed roof header.
    n = len(outline)
    vertices = outer_points + inner_points
    faces = [(i,(i+1)%n,(i+1)%n+n,i+n) for i in range(n)]
    obj = b.mesh(name+'_painted_jamb_return',vertices,faces,m['paint'])
    solid = obj.modifiers.new('Folded jamb sheet thickness','SOLIDIFY')
    solid.thickness = .002
    obj['component'] = 'body_fixed_aperture_jamb'
    seal = offroader_access_path(b,name+'_body_weather_seal',inner_points+[inner_points[0]],.0045,m['rubber'])
    seal['component'] = 'body_fixed_compression_seal'
    return obj


def build_offroader_access_doors(b, m, body_side, side, side_name):
    """Four real open doorways with separate skins, frames, glass and hardware."""
    paint, trim, steel = m['paint'], m['trim'], m['steel']
    front=[(.58,1.515),(.58,.76),(.51,.713),(-.51,.713),(-.57,.76),(-.57,1.515)]
    # Rear door follows the outside of the stationary flare, leaving a 14 mm
    # radial clearance. The original flat-bottom card would cross this arch.
    rear=[(-.61,1.515),(-.61,.73),(-.76,.73)]
    rear += [(x,.43+math.sqrt(.617**2-(x+1.32)**2))
             for x in (-.79,-.85,-.93,-1.02,-1.13,-1.24,-1.35,-1.46,-1.55)]
    rear += [(-1.57,1.515)]
    front_window=[(.498,1.495),(.258,1.973),(-.510,1.973),(-.510,1.495)]
    rear_window=[(-.652,1.495),(-.652,1.973),(-1.461,1.973),(-1.461,1.495)]
    for label,points,window,handle_x,hinge_x,limit in (
            ('front',front,front_window,-.39,.590,70),
            ('rear',rear,rear_window,-1.37,-.605,65)):
        name=side_name+'_'+label
        role=label+'_'+side_name+'_door_hinge_pivot'
        window=offroader_rounded_outline(window,.026)
        # Cut right through the top of the lower side sheet. No body-fixed
        # belt rail is left across the opening when the complete door swings.
        cutter=offroader_access_panel(b,name+'_aperture_tool',
                    [(x,side*.84,z) for x,z in points],(0,.24,0),None)
        bpy.context.view_layer.update()
        modifier=body_side.modifiers.new('Open stamped door aperture','BOOLEAN')
        modifier.operation='DIFFERENCE'; modifier.solver='EXACT'; modifier.object=cutter
        body_side.modifiers.move(len(body_side.modifiers)-1,0)
        bpy.context.view_layer.objects.active=body_side
        bpy.ops.object.modifier_apply(modifier=modifier.name)
        b.objects.remove(cutter)
        bpy.data.objects.remove(cutter,do_unlink=True)

        if label=='front':
            skin_points=[(.503,1.496),(.530,1.429),(.58,1.419)]+points[1:-1]+[(-.57,1.425),(-.522,1.496)]
        else:
            skin_points=[(-.640,1.496),(-.642,1.432),(-.61,1.419)]+points[1:-1]+[(-1.57,1.425),(-1.473,1.496)]
        skin_points=offroader_inset_outline(skin_points,.004)
        skin=offroader_crowned_door_skin(b,name+'_door_skin',skin_points,side,paint,role)
        skin['component']='opening_door_outer_skin'
        shell_points=offroader_inset_outline([(x,min(z,1.424)) for x,z in points],.022)
        shell=offroader_access_panel(b,name+'_door_inner_pressing',
                    [(x,side*.806,z) for x,z in shell_points],(0,.043,0),paint,.004,role)
        shell['component']='door_inner_shell'
        offroader_access_path(b,name+'_door_folded_hem',
                    [(x,side*.824,z) for x,z in offroader_inset_outline([(x,min(z,1.424)) for x,z in points],.009)+offroader_inset_outline([(x,min(z,1.424)) for x,z in points],.009)[:1]],.0025,paint,role)

        # Perimeter jambs, including roof and sill, stay attached to the body.
        if label=='front':
            aperture=points[1:-1]+[(-.57,1.43),(-.540,2.003),(.305,2.003),(.58,1.43)]
        else:
            aperture=points[1:-1]+[(-1.57,1.43),(-1.500,2.003),(-.620,2.003),(-.61,1.43)]
        outer=offroader_access_side_points(aperture,side,.018)
        inner=offroader_access_side_points(offroader_inset_outline(aperture,.008),side,.065)
        offroader_access_jamb(b,m,name,aperture,outer,inner)

        # Window frames, narrow glass scrapers and mirrors move with each door.
        glass_points=offroader_access_side_points(window,side,.001)
        offroader_access_glass(b,m,name+'_door_glass',glass_points,(0,.006,0),role)
        frame_points=offroader_access_side_points(window[2:-2],side,-.003)
        offroader_access_path(b,name+'_door_window_frame',frame_points,.0085,paint,role)
        offroader_access_path(b,name+'_door_window_seal',
              offroader_access_side_points(window+window[:1],side,-.007),.0045,m['rubber'],role)
        bottom_x=[p[0] for p in window if p[1]==min(q[1] for q in window)]
        rail_center=(min(bottom_x)+max(bottom_x))*.5
        rail_length=max(bottom_x)-min(bottom_x)+.023
        for suffix,z,y,width,height,material in (
                ('door_inner_glass_scraper',1.490,.818,.008,.009,m['rubber']),):
            obj=b.box(name+'_'+suffix,(rail_center,side*y,z),(rail_length,width,height),material,bevel=.004)
            obj['access_role']=role
        for suffix,location,size,material,bevel in (
                ('handle_recess',(handle_x,side*.866,1.32),(.20,.025,.07),trim,.022),
                ('door_handle',(handle_x,side*.895,1.333),(.14,.028,.025),m['alloy'],.009)):
            b.box(name+'_'+suffix,location,size,material,bevel=bevel)['access_role']=role
        # Latch in the door's trailing edge, matching a fixed U-shaped striker.
        latch_x=-.551 if label=='front' else -1.545
        b.box(name+'_door_latch_plate',(latch_x,side*.807,1.292),(.008,.036,.062),m['alloy'],bevel=.003)['access_role']=role
        b.box(name+'_rotary_latch_slot',(latch_x-.005,side*.807,1.294),(.006,.018,.026),trim,bevel=.003)['access_role']=role
        striker_x=-.582 if label=='front' else -1.581
        b.box(name+'_fixed_striker_base',(striker_x,side*.786,1.294),(.012,.046,.069),steel,bevel=.003)
        offroader_access_path(b,name+'_fixed_latch_striker',
            [(striker_x,side*.775,1.285),(striker_x+.019,side*.775,1.285),
             (striker_x+.019,side*.805,1.285),(striker_x,side*.805,1.285)],.004,m['alloy'])
        for screw_z in (1.270,1.318):
            b.cylinder(name+'_striker_screw',(striker_x+.006,side*.786,screw_z),
                       (striker_x+.011,side*.786,screw_z),.004,m['alloy'],vertices=6)
        # Coaxial external hinges use alternating fixed and mobile knuckles.
        for z in (.91,1.26):
            fixed_x=hinge_x+(.026 if label=='front' else .018)
            fixed_width=.055 if label=='front' else .028
            b.box(name+'_fixed_hinge_leaf',(fixed_x,side*.868,z),(fixed_width,.020,.065),paint,bevel=.004)
            b.box(name+'_moving_hinge_leaf',(hinge_x-.030,side*.870,z),(.060,.023,.065),paint,bevel=.004)['access_role']=role
            for index in range(3):
                obj=b.cylinder(name+'_hinge_knuckle',(hinge_x,side*.875,z-.033+index*.022),
                               (hinge_x,side*.875,z-.013+index*.022),.012,paint,vertices=14)
                if index!=1:
                    obj['access_role']=role
            b.cylinder(name+'_fixed_hinge_pin',(hinge_x,side*.875,z-.037),
                       (hinge_x,side*.875,z+.037),.005,steel,vertices=12)
        pivot=offroader_control(b,role,(hinge_x,side*.875,1.085),-limit if side==1 else 0,
                               0 if side==1 else limit,'Z')
        pivot['closed_degrees']=0
        pivot['opens_outward']=True
        pivot['hinge_axis_source']='+Z'
        pivot['component']='access_control'



def build_offroader_access_tailgate(b, m):
    """Side-hinged rear door in a physical rear aperture, clear of fixed lamps."""
    role='tailgate_hinge_pivot'
    paint,trim,steel=m['paint'],m['trim'],m['steel']
    # Lamps live on fixed corner panels; the central lower leaf widens above
    # them into the full-height glazed tailgate. The hinge axis is outside
    # its entire width so no part of the leaf swings forward into the cabin.
    lower=[(-.610,.822),(.610,.822),(.610,1.400),(.739,1.449),(-.739,1.449),(-.610,1.400)]
    def rear_points(yz,x_offset=0):
        return [(-1.981+.031*max(0,min(1,(z-.81)/1.18))+x_offset,y,z) for y,z in yz]
    offroader_access_panel(b,'tailgate_lower',rear_points(lower),(.029,0,0),paint,.004,role)
    inner=offroader_inset_outline(lower,.022)
    offroader_access_panel(b,'tailgate_inner_pressing',rear_points(inner,.027),(.020,0,0),paint,.005,role)
    for side in (-1,1):
        corner=[(side*.622,.785),(side*.840,.785),(side*.840,1.442),(side*.752,1.442),(side*.622,1.386)]
        offroader_access_panel(b,'rear_fixed_lamp_corner',rear_points(corner),(.035,0,0),paint,.005)
        b.box('rear_corner_pillar',(-1.960,side*.785,1.732),(.065,.065,.596),paint,bevel=.007,rotation=(0,.033,0))
    b.box('rear_aperture_threshold',(-1.970,0,.798),(.115,1.255,.035),steel,bevel=.007)
    b.box('rear_window_header',(-1.95,0,2.025),(.055,1.51,.050),paint,bevel=.006)
    aperture=[(-.618,.807),(.618,.807),(.618,1.401),(.749,1.453),(.749,1.998),
              (-.749,1.998),(-.749,1.453),(-.618,1.401)]
    outer=rear_points(aperture,.012)
    inside=rear_points(offroader_inset_outline(aperture,.008),.058)
    offroader_access_jamb(b,m,'tailgate',aperture,outer,inside)

    window=offroader_rounded_outline([(-.710,1.504),(.710,1.504),(.710,1.963),(-.710,1.963)],.024)
    window_points=rear_points(window,-.002)
    offroader_access_glass(b,m,'tailgate_glass',window_points,(.006,0,0),role)
    sash=offroader_rounded_outline([(-.721,1.493),(.721,1.493),(.721,1.974),(-.721,1.974)],.028)
    offroader_access_path(b,'tailgate_window_frame',rear_points(sash+sash[:1]),.011,paint,role)
    offroader_access_path(b,'tailgate_window_seal',rear_points(window+window[:1],-.004),.009,m['rubber'],role)
    b.box('tailgate_mobile_window_sill',(-1.965,0,1.469),(.043,1.475,.030),paint,bevel=.006)['access_role']=role
    b.box('tailgate_mobile_window_header',(-1.952,0,1.984),(.036,1.475,.025),paint,bevel=.005)['access_role']=role
    b.box('tailgate_interior_liner',(-1.932,0,1.105),(.018,1.105,.50),m['interior'],bevel=.023)['access_role']=role
    b.box('tailgate_inner_pull_recess',(-1.918,.38,1.316),(.012,.185,.051),trim,bevel=.012)['access_role']=role
    b.box('tailgate_inner_pull',(-1.904,.38,1.316),(.021,.130,.017),m['alloy'],bevel=.006)['access_role']=role
    b.box('tailgate_handle',(-2.012,.51,1.350),(.030,.17,.061),trim,bevel=.017)['access_role']=role

    # Fine fired-copper demister lines sit on the inner glass surface.
    demister=b.mat('offroader_rear_demister_copper',(.16,.075,.029),metallic=.48,roughness=.56)
    for index in range(10):
        z=1.548+index*.040
        points=rear_points([(-.668,z),(.668,z)],.0012)
        obj=offroader_access_path(b,'rear_glass_defroster_'+str(index),points,.00065,demister,role)
        obj['max_lod']=1
    for y in (-.668,.668):
        obj=offroader_access_path(b,'rear_glass_defroster_busbar',rear_points([(y,1.542),(y,1.919)],.0012),.00125,demister,role)
        obj['max_lod']=1
        b.box('rear_defroster_terminal',(-1.948,y,1.53),(.008,.013,.017),m['alloy'],bevel=.002)['access_role']=role
    offroader_access_path(b,'rear_window_wiper',rear_points([(.46,1.49),(.15,1.70),(-.05,1.78)],-.020),.009,steel,role)
    offroader_access_path(b,'rear_window_wiper_blade',rear_points([(-.29,1.79),(.18,1.79)],-.012),.010,trim,role)

    # Hinge knuckles share one real vertical line outside the right D pillar.
    # Keep the pin behind both the rear corner and the sloped side D pillar.
    # The moving strap wraps around their inner edge instead of sweeping
    # through the fixed pillar as a straight rectangular leaf would.
    hinge_x,hinge_y=-2.040,-.792
    for z in (1.494,1.902):
        # The tapered upper pillar sits farther forward than the lower mount.
        # Extend that fixed leaf into its mounting face; the pin stays coaxial.
        fixed_length=.066 if z>1.8 else .052
        fixed_x=hinge_x+(.027 if z>1.8 else .020)
        b.box('tailgate_fixed_hinge_leaf',(fixed_x,hinge_y-.013,z),(fixed_length,.049,.070),paint,bevel=.004)
        inner_a=offroader_cabin_taper_y(-.726,z)
        inner_b=offroader_cabin_taper_y(-.710,z)
        strap=[(hinge_x-.007,hinge_y-.006,z),(hinge_x+.007,hinge_y-.006,z),
               (hinge_x+.007,inner_a,z),(-1.950,inner_a,z),
               (-1.950,inner_b,z),(hinge_x-.007,inner_b,z)]
        offroader_access_panel(b,'tailgate_moving_hinge_leaf',strap,(0,0,.064),paint,.002,role)
        for index in range(3):
            obj=b.cylinder('tailgate_hinge_knuckle',(hinge_x,hinge_y,z-.036+index*.024),
                            (hinge_x,hinge_y,z-.014+index*.024),.014,paint,vertices=14)
            if index!=1:
                obj['access_role']=role
        b.cylinder('tailgate_fixed_hinge_pin',(hinge_x,hinge_y,z-.040),(hinge_x,hinge_y,z+.040),.006,steel,vertices=12)
    b.box('tailgate_moving_latch',(-1.955,.603,1.322),(.043,.013,.071),m['alloy'],bevel=.004)['access_role']=role
    b.box('tailgate_fixed_striker_base',(-1.944,.625,1.322),(.060,.014,.078),steel,bevel=.004)
    offroader_access_path(b,'tailgate_fixed_latch_striker',[(-1.966,.624,1.319),(-1.966,.604,1.319),
                          (-1.938,.604,1.319),(-1.938,.624,1.319)],.0045,m['alloy'])
    pivot=offroader_control(b,role,(hinge_x,hinge_y,1.45),0,100,'Z')
    pivot['closed_degrees']=0
    pivot['opens_outward']=True
    pivot['hinge_axis_source']='+Z'
    pivot['component']='access_control'



def rig_offroader_access(b, m):
    """Attach complete leaves and spare after chassis and cabin construction."""
    roles=('front_left_door_hinge_pivot','rear_left_door_hinge_pivot',
           'front_right_door_hinge_pivot','rear_right_door_hinge_pivot','tailgate_hinge_pivot')
    pivots={obj.get('part_role'):obj for obj in b.objects if obj.get('part_role') in roles}
    if len(pivots)!=len(roles):
        raise RuntimeError('Missing offroader access pivot: '+str(set(roles)-set(pivots)))
    counts={role:0 for role in roles}
    for obj in b.objects:
        short_name=obj.name.removeprefix(b.name+'_')
        if short_name.startswith(('rear_spare_','spare_carrier_')):
            obj['access_role']='tailgate_hinge_pivot'
        role=obj.get('access_role')
        if role:
            if role not in pivots:
                raise RuntimeError('Unknown access_role on '+obj.name+': '+role)
            b.parent(obj,pivots[role])
            counts[role]+=1
    for role,pivot in pivots.items():
        pivot['moving_component_count']=counts[role]
        pivot['collision_note']='Visual articulation only; static broad collision proxies require engine tuning.'
    return counts


def build_offroader(b):
    """Original expedition 4x4 with a hollow cabin and articulated road wheels."""
    m = {
        'paint': b.mat('offroader_sage_paint', (.105,.163,.119), metallic=0., roughness=.255),
        'roof': b.mat('offroader_sand_roof', (.55,.51,.40), metallic=0, roughness=.32),
        'trim': b.mat('offroader_graphite_trim', (.018,.024,.027), roughness=.46),
        'rubber': b.mat('offroader_tire_rubber', (.012,.016,.018), roughness=.85),
        'tread': b.mat('offroader_tread_rubber', (.022,.027,.029), roughness=.90),
        'alloy': b.mat('offroader_satin_alloy', (.43,.48,.51), metallic=.90, roughness=.31),
        'steel': b.mat('offroader_dark_steel', (.065,.083,.090), metallic=.82, roughness=.38),
        'chrome': b.mat('offroader_polished_metal', (.62,.68,.71), metallic=1., roughness=.085),
        'bronze': b.mat('offroader_bronze_wheels', (.30,.23,.13), metallic=.86, roughness=.30),
        'glass': b.mat('offroader_smoked_glass', (.985,.995,1.), roughness=.008, transmission=1.),
        'optic': b.mat('offroader_optical_polycarbonate', (.99,.995,1.), roughness=.018, transmission=1.),
        'indicator_glass': b.mat('offroader_amber_optical_cover', (.98,.62,.18), roughness=.06, transmission=.94),
        'indicator_chip': b.mat('offroader_unlit_amber_emitter', (.90,.29,.018), roughness=.32),
        'lightguide': b.mat('offroader_frosted_headlamp_guide', (.67,.73,.77), roughness=.24, emission=(.50,.55,.60)),
        'reflector': b.mat('offroader_optical_aluminium', (.55,.59,.63), metallic=.90, roughness=.19),
        'red': b.mat('offroader_red_lens', (.50,.008,.005), roughness=.23, emission=(.55,.008,.004)),
        'amber': b.mat('offroader_amber_lens', (.90,.20,.012), roughness=.23, emission=(.70,.10,.003)),
        'lamp': b.mat('offroader_led_lens', (.70,.82,.90), roughness=.16, emission=(.48,.61,.74)),
        'fabric': b.mat('offroader_woven_seat', (.065,.060,.050), roughness=.79),
        'seat_insert': b.mat('offroader_seat_insert', (.065,.060,.050), roughness=.79),
        'leather': b.mat('offroader_saddle_leather', (.16,.087,.045), roughness=.575),
        'stitch': b.mat('offroader_sand_stitch', (.26,.18,.10), roughness=.80),
        'cast': b.mat('offroader_cast_engine_alloy', (.22,.24,.25), metallic=.85, roughness=.49),
        'reservoir': b.mat('offroader_reservoir_plastic', (.65,.65,.50), roughness=.39),
        'blue': b.mat('offroader_blue_marking', (.025,.20,.38), roughness=.38),
        'screen_ink': b.mat('offroader_screen_ink', (.58,.77,.85), roughness=.46, emission=(.16,.24,.29)),
        'interior': b.mat('offroader_cabin_polymer', (.030,.038,.039), roughness=.63),
        'display': b.mat('offroader_instrument_display', (.009,.026,.037), roughness=.25),
    }
    m['lightguide'].node_tree.nodes.get('Principled BSDF').inputs['Emission Strength'].default_value=.45
    for key in ('paint', 'roof'):
        shader = m[key].node_tree.nodes.get('Principled BSDF')
        shader.inputs['Coat Weight'].default_value = .80 if key=='paint' else .35
        shader.inputs['Coat Roughness'].default_value = .13 if key=='paint' else .22
        for node in m[key].node_tree.nodes:
            if node.type == 'NORMAL_MAP':
                node.inputs['Strength'].default_value = .035
    for key in ('lamp', 'red', 'amber'):
        m[key].node_tree.nodes.get('Principled BSDF').inputs['Emission Strength'].default_value = .45
    for key in ('trim','interior','chrome','alloy','steel','leather'):
        for node in m[key].node_tree.nodes:
            if node.type=='NORMAL_MAP':
                node.inputs['Strength'].default_value=.055 if key in ('trim','interior','leather') else .018
            if node.type=='TEX_IMAGE' and node.image and node.image.name.endswith('_metallic_roughness'):
                pixels=np.array(node.image.pixels[:],dtype=np.float32).reshape(b.texture_size,b.texture_size,4)
                values=pixels[:,:,1]
                values[:]=values.mean()+(values-values.mean())*.35
                node.image.pixels.foreach_set(pixels.ravel()); node.image.pack()
    offroader_upholstery_texture(b,m['seat_insert'])
    m['sidewall_marking']=offroader_sidewall_material(b)
    # Directional woven normal texture: exportable pixels, not a renderer-only shader.
    weave = np.ones((b.texture_size, b.texture_size, 4), dtype=np.float32)
    gy, gx = np.mgrid[:b.texture_size, :b.texture_size]
    weave[:, :, 0] = .5 + .08*np.sin(gx*math.pi/2)
    weave[:, :, 1] = .5 + .08*np.sin(gy*math.pi/2)
    weave[:, :, 2] = .99
    for node in m['fabric'].node_tree.nodes:
        if node.type == 'TEX_IMAGE' and node.image and node.image.name.endswith('_normal'):
            node.image.pixels.foreach_set(weave.ravel())
            node.image.pack()
    offroader_finish_materials(b, m)
    build_offroader_body(b, m)
    build_offroader_chassis(b, m)
    build_offroader_engine(b, m)
    build_offroader_interior(b, m)
    offroader_mechanical_wear(b, m)
    rig_offroader_access(b, m)

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
        'description': 'Original expedition 4x4 with a raised ventilated hood, integral window waists, continuous rounded front stamping, recessed optics, opening doors and tailgate, detailed engine bay and a stitched contoured interior.',
        'dimensions_design_m': {'wheelbase': 2.64, 'track': 1.84, 'tire_diameter': .86},
        'moving_parts': ['front_left_steering_pivot','front_right_steering_pivot',
                         'front_left_wheel_pivot','front_right_wheel_pivot',
                         'rear_left_wheel_pivot','rear_right_wheel_pivot',
                         'hood_hinge_pivot','cabin_steering_pivot','gear_lever_pivot',
                         'transfer_lever_pivot','handbrake_pivot','accelerator_pivot',
                         'brake_pivot','clutch_pivot',
                         'front_left_door_hinge_pivot','rear_left_door_hinge_pivot',
                         'front_right_door_hinge_pivot','rear_right_door_hinge_pivot',
                         'tailgate_hinge_pivot'],
        'pivot_notes': 'Road wheels: local Y; front steering and doors/tailgate: local Z; hood and cabin levers/pedals: local Y; tilted cabin steering wheel: local X. Hood and left doors open with negative rotation; right doors and tailgate with positive rotation. Limits are on each control pivot. No animation clips or vehicle physics.',
        'notes': ['The hood has a raised central pressing and a real 340 by 490 mm louvered air opening through the skin and liner, with an open thin-walled duct beneath. This represents engine-bay ventilation; it is not connected to the snorkel-fed combustion intake.',
                  'Headlights have 2.5 mm convex optical covers over separate projectors and reflectors. Six unlit prismatic amber modules have separate mounted bezels and reflector cups.',
                  'Integral rolled door and quarter-panel waists replace separate window shelves. The front fascia and fixed side wings form one connected shell with 100 mm rounded front corners. Mirror arms attach to triangular window sail mounts.',
                  'The body-shape pass adds gently crowned door skins, 35 mm extra upper-cabin taper per side, rounded glazing corners, broad hood shoulders, a thin crowned roof, slim rack mounts and wrapped bumper wings.',
                  'The 17 percent larger optical assemblies sit in enlarged apertures beside a narrower curved grille. Slender window frames, mirror mounts and tapered flare ends refine the proportions.' ,
                  'Sculpted door cards, stitched seat inserts, aligned driver seating and a fixed footrest complement the console and padded armrest; the original wheelbase and all 19 controls are retained.',
                  'A modeled inline-four engine bay includes cooling, intake, belt drive, battery, fluids and wiring; pistons and mechanical simulation are not included.',
                  'Manual six-speed cabin includes three pedals, separate transfer lever, handbrake, labeled instruments, route display and upholstered seats.',
                  'Window transmission uses KHR_materials_transmission; verify support in the target-engine importer.',
                  'Paint clearcoat uses KHR_materials_clearcoat. Subtle lower-body dust is baked to vertex colors. Displays are authored static graphics, not interactive UI.',
                  'Original molded tire lettering uses a shared normal/roughness atlas of at least 1024 pixels. Upholstery weave and recessed seams use authored normal maps with aligned UVs.',
                  'Four door leaves carry their glass, trim and inner cards; the hinged tailgate carries its glazing, heater lines, wiper and spare wheel. Cargo trim and fixed jambs remain on the body.',
                  'Metric finish textures distinguish paint, polymer, leather, cloth and rubber. Brake contact rings, exhaust heat tint, underbody dirt and contact wear are baked into portable image textures or vertex colors.',
                  'Four-wheel drive is represented by mechanical geometry, not a vehicle-physics simulation.',
                  'Doors and tailgate are articulated geometry with limits; game input, latching and collision interaction must be implemented in the target engine. Suspension and shift boots remain static.',
                  'Ten broad static collision boxes need engine tuning and do not follow wheels, doors or tailgate; cabin collision must be replaced for player entry.'],
    }

def build_offroader_body(b, m):
    """Hollow expedition body, five opening access panels and physical clear glazing."""
    paint, trim, steel = m['paint'], m['trim'], m['steel']

    def trim_path(name, points, radius, material):
        return offroader_access_path(b,name,points,radius,material)

    def panel(name, outline, offset, material, bevel=0):
        return offroader_access_panel(b,name,outline,offset,material,bevel)

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
    lower_edge.append((1.978,lower))
    side_shape=[(1.978,1.407),(.62,1.43),(.60,1.492),(-1.94,1.492),(-1.98,1.38)]+lower_edge
    for side, side_name in ((1,'left'),(-1,'right')):
        body_side=panel(side_name+'_body_side',[(x,side*.84,z) for x,z in side_shape],(0,.032,0),paint,bevel=.003)
        # Rolled continuous flare profile: no broad flat horizontal shelf.
        for axle,label in ((1.32,'front'),(-1.32,'rear')):
            controls=[(.522,.842),(.526,.915),(.534,.993),(.544,1.047),
                      (.554,1.063),(.567,1.055),(.579,1.010),(.586,.930),(.588,.842)]
            profile=[]
            for j in range(len(controls)-1):
                a=Vector(controls[max(0,j-1)]);c=Vector(controls[j]);d=Vector(controls[j+1]);e=Vector(controls[min(j+2,len(controls)-1)])
                for k in range(3):
                    t=k/3
                    point=.5*((2*c)+(-a+d)*t+(2*a-5*c+4*d-e)*t*t+(-a+3*c-3*d+e)*t*t*t)
                    profile.append(tuple(point))
            profile.append(controls[-1])
            verts=[]
            steps=64
            for i in range(steps+1):
                a=math.pi*i/steps
                flare_width=.80+.20*math.sin(a)**.7
                verts.extend((axle+r*math.cos(a),side*(.842+(y-.842)*flare_width),
                              .43+r*math.sin(a)) for r,y in profile)
            count=len(profile)
            faces=[(i*count+j,i*count+(j+1)%count,(i+1)*count+(j+1)%count,(i+1)*count+j)
                   for i in range(steps) for j in range(count)]
            faces += [tuple(reversed(range(count))),tuple(steps*count+j for j in range(count))]
            b.mesh(side_name+'_'+label+'_fender_flare',verts,faces,trim,smooth=True,bevel=.003)
            for i in range(7):
                a=math.pi*(i+.5)/7
                pos=(axle+.554*math.cos(a),side*(.842+.221*(.80+.20*math.sin(a)**.7)+.005),
                     .43+.554*math.sin(a))
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

        # Fixed structural pillars remain behind independent opening doors.
        quarter_window=offroader_rounded_outline([(-1.595,1.495),(-1.595,1.975),(-1.869,1.975),(-1.898,1.495)],.022)
        offroader_access_glass(b,m,side_name+'_quarter_glass',side_outline(quarter_window,side),(0,.006,0))
        trim_path(side_name+'_quarter_window_seal',side_outline(quarter_window+quarter_window[:1],side,.004),.0045,m['rubber'])
        pillars=[((.62,1.43),(.32,2.03),.135,.061),
                 ((-.58,1.43),(-.58,2.03),.086,.080),
                 ((-1.53,1.43),(-1.53,2.03),.084,.078),
                 ((-1.96,1.43),(-1.94,2.03),.120,.125)]
        for index,(lower_point,upper_point,lower_width,upper_width) in enumerate(pillars):
            lx,lz=lower_point; ux,uz=upper_point
            outline=[(lx-lower_width*.5,lz),(lx+lower_width*.5,lz),
                     (ux+upper_width*.5,uz),(ux-upper_width*.5,uz)]
            panel(side_name+'_pressed_cabin_pillar_'+str(index),side_outline(outline,side),
                  (0,.038,0),paint,bevel=.006)
        b.box(side_name+'_window_header',(-.81,side*.755,2.025),(2.28,.045,.066),paint,bevel=.007)
        b.box(side_name+'_roof_gutter',(-.795,side*.785,2.065),(2.39,.020,.018),trim,bevel=.004)
        build_offroader_access_doors(b,m,body_side,side,side_name)
        offroader_form_fixed_side(body_side,side)

        # Mirrors face aft toward the driver, with an independent dark housing.
        before_mirror=len(b.objects)
        offroader_access_panel(b,side_name+'_mirror_mount',
            offroader_access_side_points([(.492,1.504),(.448,1.594),(.414,1.504)],side,-.010),
            (0,.014,0),trim,.003)
        b.tube(side_name+'_mirror_arm',[(.452,side*.843,1.54),(.415,side*.96,1.60),(.37,side*1.04,1.65)],.014,trim)
        b.box(side_name+'_mirror_housing',(.36,side*1.075,1.655),(.115,.15,.19),trim,bevel=.035)
        b.box(side_name+'_mirror_glass',(.298,side*1.075,1.655),(.006,.119,.15),m['chrome'],bevel=.015)
        for obj in b.objects[before_mirror:]:
            obj['access_role']='front_'+side_name+'_door_hinge_pivot'
        build_offroader_indicator(b,m,side_name+'_side_indicator',(.77,side*.860,1.23),(0,side,0),(1,0,0),.113,.031,3)

    # Hood and the upper cowl are separate from the open passenger cell.
    build_offroader_hood(b, m)
    for side in (-1,1):
        b.box('hood_latch',(1.88,side*.84,1.315),(.065,.035,.12),trim,bevel=.013)
        b.cylinder('hood_latch_pin',(1.88,side*.85,1.28),(1.88,side*.89,1.28),.013,m['alloy'],vertices=10)
    b.box('front_cowl',(.59,0,1.435),(.11,1.60,.07),paint,bevel=.015)
    b.box('cowl_vent',(.57,0,1.48),(.075,.83,.012),trim,bevel=.006)
    for y in (-.32,-.24,-.16,-.08,0,.08,.16,.24,.32):
        b.box('cowl_vent_slit',(.57,y,1.488),(.052,.025,.005),steel,bevel=.001)

    windshield=offroader_rounded_outline([(.601,-.775,1.485),(.601,.775,1.485),(.343,.707,1.992),(.343,-.707,1.992)],.032)
    # 7.6 mm laminated windshield thickness follows its actual sloped normal.
    offroader_access_glass(b,m,'windshield',windshield,(-.006773,0,-.003446))
    trim_path('windshield_seal',windshield+[windshield[0]],.008,m['rubber'])
    b.box('windshield_lower_frame',(.62,0,1.44),(.045,1.64,.047),paint,bevel=.007)
    b.box('windshield_upper_frame',(.32,0,2.03),(.043,1.51,.050),paint,bevel=.007)
    for y in (-.38,.36):
        b.tube('windscreen_wiper_arm',[(.622,y,1.48),(.53,y-.10,1.65),(.455,y-.15,1.79)],.009,steel)
        b.tube('windscreen_wiper_blade',[(.448,y-.35,1.80),(.448,y-.15,1.80),(.448,y+.07,1.80)],.012,trim)

    build_offroader_roof_equipment(b,m)
    build_offroader_access_tailgate(b,m)

    # Original wide horizontal grille rather than a brand-specific pattern.
    build_offroader_front(b, m)
    for side in (-1,1):
        build_offroader_indicator(b,m,('left' if side==1 else 'right')+'_front_signal',(2.074,side*.636,.986),(1,0,0),(0,1,0),.178,.045,4)
        b.box('rear_light_housing',(-2.015,side*.704,1.191),(.065,.177,.36),trim,bevel=.020)
        build_offroader_indicator(b,m,('left' if side==1 else 'right')+'_rear_indicator',(-2.050,side*.704,1.115),(-1,0,0),(0,1,0),.135,.048,3)
        for z,material in ((1.30,m['red']),(1.205,m['red']),(1.055,m['lamp'])):
            b.box('rear_light_lens',(-2.056,side*.704,z),(.016,.137,.055),material,bevel=.012)

    build_offroader_bumpers(b,m)

    # Passenger-side raised air intake and a restrained expedition roof basket.
    b.tube('snorkel_riser',[(1.12,-.90,1.27),(.88,-.92,1.40),(.64,-.91,1.52),(.37,-.87,2.08)],.052,trim)
    b.box('snorkel_head',(.39,-.87,2.135),(.21,.13,.12),trim,bevel=.025)
    b.box('snorkel_intake',(.505,-.87,2.142),(.016,.103,.066),steel,bevel=.010)
    for z in (2.125,2.146,2.167):
        b.box('snorkel_intake_slit',(.516,-.87,z),(.008,.085,.007),m['rubber'],bevel=.002)


    return {'body': 'Hollow four-door expedition wagon with opening framed doors, side-hinged glazed tailgate, clear laminated glazing, rack, winch and snorkel.',
            'note': 'Mirrors reach approximately 2.27 m width; antenna reaches 2.30 m. Glass relies on the transmission material supplied by the caller.'}



def offroader_upholstery_texture(b, material):
    """Bake textile weave, depressed seams and restrained creases into one normal map."""
    n=max(1024,b.texture_size)
    yy,xx=np.mgrid[:n,:n]; u=(xx+.5)/n; v=(yy+.5)/n
    height=.000018*np.sin(xx*math.pi/2)*np.sin(yy*math.pi/2)
    edge=(np.exp(-((u-.08)/.09)**2)+np.exp(-((u-.92)/.09)**2))
    height+=.00012*edge*np.sin(v*83+np.sin(u*17)*2)
    inside=np.clip((u-.04)/.03,0,1)*np.clip((.96-u)/.03,0,1)
    dots=np.exp(-(np.sin(u*math.tau*42)/.50)**2)
    for seam in (.23,.46,.69):
        height-=.00065*np.exp(-((v-seam)/.007)**2)*inside
        for offset in (-.012,.012):
            height+=.00011*np.exp(-((v-seam-offset)/.0025)**2)*dots*inside
    for seam in (.045,.955):
        height-=.00050*np.exp(-((u-seam)/.008)**2)
        # Approximately 3 mm stitch pitch along the panel edge, with two
        # distinct threads beside the seam instead of a continuous raised line.
        thread=np.exp(-(np.sin(v*math.tau*70)/.48)**2)
        for offset in (-.010,.010):
            height+=.00012*np.exp(-((u-seam-offset)/.0028)**2)*thread
    dv,du=np.gradient(height,.43/n,.30/n)
    normals=np.stack((-du,-dv,np.ones_like(u)),axis=-1)
    normals/=np.linalg.norm(normals,axis=-1,keepdims=True)
    pixels=np.ones((n,n,4),dtype=np.float32); pixels[:,:,:3]=normals*.5+.5
    orm=np.ones_like(pixels)
    orm[:,:,1]=.79+.018*np.sin(u*math.tau*40)*np.sin(v*math.tau*58)
    orm[:,:,2]=0.
    for node in material.node_tree.nodes:
        if node.type=='TEX_IMAGE' and node.image:
            values=pixels if node.image.name.endswith('_normal') else orm
            image=bpy.data.images.new(node.image.name+'_stitched',n,n,alpha=True)
            image.colorspace_settings.name='Non-Color'
            image.pixels.foreach_set(values.ravel()); image.pack(); node.image=image
        if node.type=='NORMAL_MAP':
            node.inputs['Strength'].default_value=1.
    material['upholstery_atlas_pixels']=n
    material['stitch_pitch_m']=.43/140


def offroader_sidewall_material(b):
    """Original moulded lettering, baked at metric scale into a shared atlas."""
    material=b.mat('offroader_moulded_sidewall',(.012,.016,.018),roughness=.84)
    glyphs={
        'T':['11111','00100','00100','00100','00100','00100','00100'],
        'R':['11110','10001','10001','11110','10100','10010','10001'],
        'A':['01110','10001','10001','11111','10001','10001','10001'],
        'I':['11111','00100','00100','00100','00100','00100','11111'],
        'L':['10000','10000','10000','10000','10000','10000','11111'],
        '/':['00001','00001','00010','00100','01000','10000','10000'],
        '2':['01110','10001','00001','00010','00100','01000','11111'],
        '8':['01110','10001','10001','01110','10001','10001','01110'],
        '5':['11111','10000','10000','11110','00001','10001','01110'],
        '7':['11111','00001','00010','00100','01000','01000','01000'],
        '1':['00100','01100','00100','00100','00100','00100','01110'],
        ' ':['00000']*7,
    }
    n=max(1024,b.texture_size)
    mask=np.zeros((n,n),dtype=np.float32)
    # The atlas has a long physical U span (circumference), but only 62 mm in V.
    sx,sy=max(1,n//512),max(1,n//22)
    for label,centre in (('TRAIL A/T',.50),('285/75 R17',.06)):
        left=int(n*centre-(len(label)*6-1)*sx/2)
        bottom=int(n*.5-7*sy/2)
        for index,char in enumerate(label):
            for row,line in enumerate(glyphs[char]):
                for col,value in enumerate(line):
                    if value=='1':
                        for dx in range(sx):
                            x=(left+(index*6+col)*sx+dx)%n
                            y=bottom+(6-row)*sy
                            mask[y:y+sy,x]=1.
    # Rounded mould edges and a shallow raised rubber face; no white paint.
    mask=(mask*4+np.roll(mask,1,0)+np.roll(mask,-1,0)+np.roll(mask,1,1)+np.roll(mask,-1,1))/8
    height=mask*.00070
    dv,du=np.gradient(height,.062/n,(math.tau*.326)/n)
    normal=np.stack((-du,-dv,np.ones_like(mask)),axis=-1)
    normal/=np.linalg.norm(normal,axis=-1,keepdims=True)
    normal_pixels=np.ones((n,n,4),dtype=np.float32)
    normal_pixels[:,:,:3]=normal*.5+.5
    orm=np.ones((n,n,4),dtype=np.float32)
    orm[:,:,1]=.84-.20*mask; orm[:,:,2]=0
    for node in material.node_tree.nodes:
        if node.type=='TEX_IMAGE' and node.image:
            pixels=normal_pixels if node.image.name.endswith('_normal') else orm
            image=bpy.data.images.new(node.image.name+'_atlas',n,n,alpha=True)
            image.colorspace_settings.name='Non-Color'
            image.pixels.foreach_set(pixels.ravel()); image.pack(); node.image=image
        elif node.type=='NORMAL_MAP':
            node.inputs['Strength'].default_value=1.
    return material


def prepare_offroader_insert_uvs(b):
    for obj in b.objects:
        if obj.type!='MESH' or not any(key in obj.name for key in ('back_insert','cushion_insert')):
            continue
        axes=(1,2) if 'back_insert' in obj.name else (1,0)
        low=[min(v.co[a] for v in obj.data.vertices) for a in axes]
        span=[max(v.co[a] for v in obj.data.vertices)-lo for a,lo in zip(axes,low)]
        for loop in obj.data.loops:
            p=obj.data.vertices[loop.vertex_index].co
            obj.data.uv_layers.active.data[loop.index].uv=tuple((p[a]-lo)/max(length,1e-8) for a,lo,length in zip(axes,low,span))


def offroader_inset_outline(points, distance):
    """Miter a simple x/z polygon inward by a real metric gap."""
    area=sum(a[0]*c[1]-c[0]*a[1] for a,c in zip(points,points[1:]+points[:1]))
    sign=1 if area>0 else -1
    result=[]
    for i,p in enumerate(points):
        p=Vector(p); before=Vector(points[i-1]); after=Vector(points[(i+1)%len(points)])
        a=(p-before).normalized(); c=(after-p).normalized()
        na=Vector((-a.y,a.x))*sign; nc=Vector((-c.y,c.x))*sign
        bisector=na+nc
        shifted=p+bisector*(distance/max(1.e-8,bisector.dot(na)))
        result.append(tuple(shifted))
    return result


def prepare_offroader_surface_tints(b):
    """Assign metric UV layers and full linear COLOR_0 tint after mesh resolution.

    Atlas UVMap coordinates remain intact. Vertex color includes base color, so
    connected shaders explicitly have a white baseColorFactor for glTF export.
    """
    names=('sage_paint','sand_roof','graphite_trim','tire_rubber','tread_rubber',
           'dark_steel','moulded_sidewall','saddle_leather','cabin_polymer',
           'satin_alloy','bronze_wheels','cast_engine_alloy','woven_seat',
           'heat_tinted_stainless','step_contact_polymer','handpolished_alloy')
    materials={mat for mat in b.materials.values() if any(mat.name.endswith(key) for key in names)}
    base={}
    for mat in materials:
        shader=mat.node_tree.nodes.get('Principled BSDF')
        base[mat]=tuple(mat.get('surface_base_color',shader.inputs['Base Color'].default_value))
        mat['surface_base_color']=base[mat]
        node=next((node for node in mat.node_tree.nodes if node.type=='VERTEX_COLOR' and node.layer_name=='surface_tint'),None)
        if node is None:
            node=mat.node_tree.nodes.new('ShaderNodeVertexColor'); node.layer_name='surface_tint'
        mat.node_tree.links.new(node.outputs['Color'],shader.inputs['Base Color'])
        shader.inputs['Base Color'].default_value=(1.,1.,1.,1.)
    underbody_tokens=('ladder_frame','frame_end','frame_crossmember','axle_tube','differential',
        'diff_cover','transfer_case','gearbox_housing','prop_shaft','radius_arm','upper_link',
        'panhard_rod','tie_rod','drag_link','spring_seat','suspension_spring','steering_knuckle')
    for obj in b.objects:
        if obj.type!='MESH' or not obj.data.materials:
            continue
        mesh=obj.data; matrix=world_matrix(obj)
        positions=[matrix@vertex.co for vertex in mesh.vertices]
        used=[mat for mat in mesh.materials if mat is not None]
        if any(mat.get('finish_uv')=='FinishUV' for mat in used):
            uv=mesh.uv_layers.get('FinishUV') or mesh.uv_layers.new(name='FinishUV')
            # Keep authored UVMap active for any existing seam or lettering map.
            for polygon in mesh.polygons:
                material=mesh.materials[polygon.material_index]
                tile=float(material.get('finish_tile_m',.08)) if material else .08
                normal=matrix.to_3x3()@polygon.normal
                axis=max(range(3),key=lambda index:abs(normal[index]))
                # Positive handed frames make encoded tangent normal direction
                # agree with the physical plane on both sides of the vehicle.
                axes=((1,2),(2,0),(0,1))[axis]
                sign=1 if normal[axis]>=0 else -1
                for loop_index in polygon.loop_indices:
                    p=positions[mesh.loops[loop_index].vertex_index]
                    uv.data[loop_index].uv=(p[axes[0]]/tile,sign*p[axes[1]]/tile)
        if any(mat.get('finish_uv')=='BrakeUV' for mat in used):
            uv=mesh.uv_layers.get('BrakeUV') or mesh.uv_layers.new(name='BrakeUV')
            cx=(min(p.x for p in positions)+max(p.x for p in positions))*.5
            cz=(min(p.z for p in positions)+max(p.z for p in positions))*.5
            for loop in mesh.loops:
                p=positions[loop.vertex_index]
                uv.data[loop.index].uv=((p.x-cx)/.36+.5,(p.z-cz)/.36+.5)
        if mesh.uv_layers.get('UVMap'):
            mesh.uv_layers.active=mesh.uv_layers['UVMap']
        if not any(material in materials for material in used):
            continue
        layer=mesh.color_attributes.get('surface_tint') or mesh.color_attributes.new(name='surface_tint',type='BYTE_COLOR',domain='CORNER')
        mesh.color_attributes.active_color=layer
        name=obj.name
        exterior=any(key in name for key in ('body_side','door_skin','fender_flare','bumper','running_board',
            'rock_slider','front_left','front_right','rear_left','rear_right','mud_flap'))
        underbody=any(token in name for token in underbody_tokens)
        exhaust='heat tint' in obj.get('mechanical_finish','')
        handle='hand contact' in obj.get('mechanical_finish','')
        for polygon in mesh.polygons:
            material=mesh.materials[polygon.material_index]
            if material not in materials:
                # Mixed-material seats need white COLOR_0 on atlas-only slots;
                # their baseColorFactor continues to supply the actual color.
                for index in polygon.loop_indices:
                    layer.data[index].color=(1.,1.,1.,1.)
                continue
            color=base[material]
            normal=matrix.to_3x3()@polygon.normal
            for index in polygon.loop_indices:
                x,y,z=positions[mesh.loops[index].vertex_index]
                side=max(0.,min(1.,(abs(y)-.70)/.18))
                lower=max(0.,min(1.,(.93-z)/.62))
                spray=math.exp(-((abs(x)-1.32)/.70)**2)*max(0.,min(1.,(1.22-z)/.9))
                variation=.84+.11*math.sin(x*19+y*27+z*31)+.05*math.sin(x*57-y*41)
                amount=side*(.105*lower+.055*spray)*variation if exterior else 0.
                if underbody:
                    # Physical accumulated earth tint on lower running gear,
                    # independent of the scene's lighting or ambient occlusion.
                    splash=math.exp(-((abs(x)-1.32)/.78)**2)
                    low=max(0.,min(1.,(.78-z)/.46))
                    amount=max(amount,(.035+.060*splash)*low*variation)
                dust=(.20,.151,.096)
                tint=[color[i]*(1-amount)+dust[i]*amount for i in range(3)]
                if exhaust:
                    hot=math.exp(-((x-1.24)/.72)**2)
                    band=.5+.5*math.sin((x+z*.35)*18)
                    heat=(.29,.225,.14) if band>.5 else (.225,.24,.265)
                    a=hot*(.12+.055*band)
                    tint=[tint[i]*(1-a)+heat[i]*a for i in range(3)]
                if material.name.endswith('step_contact_polymer') and normal.z>.65:
                    contact=math.exp(-((x+.12)/.49)**2)*.045
                    tint=[v*(1+contact) for v in tint]
                if handle:
                    tint=[v*1.006 for v in tint]
                layer.data[index].color=tuple(tint)+(1.,)


def offroader_control(b, name, location, low, high, axis='Y'):
    pivot = b.empty(name, location)
    pivot['rotation_axis'] = axis
    pivot['rotation_min_degrees'] = low
    pivot['rotation_max_degrees'] = high
    return pivot


def offroader_text(b, name, value, location, size, material, parent=None, facing='cabin'):
    """Small original vector labels, resolved to meshes before GLB export."""
    data = bpy.data.curves.new(name, 'FONT')
    data.body, data.size, data.align_x = value, size, 'CENTER'
    data.resolution_u = 2
    data.materials.append(material)
    obj = b._link(bpy.data.objects.new('offroader_'+name, data))
    obj.location = location
    if facing == 'cabin':
        obj.rotation_euler = Matrix(((0,0,-1),(-1,0,0),(0,1,0))).to_euler()
    elif facing == 'top':
        obj.rotation_euler.z=-math.pi/2
    obj['max_lod'] = 0
    if parent is not None:
        b.parent(obj, parent)
    return obj


def offroader_pad(b, name, location, size, material, rotation=None):
    """Rounded small pads; seat headrests have a tapered, gently bowed face."""
    seating=any(key in name for key in ('seat','cushion','bolster','headrest','back_insert'))
    segments,rings=(32,16) if seating else (24,12)
    exponent=(.43 if 'headrest' in name else .25) if seating else .4
    def power(v, exponent):
        return math.copysign(abs(v)**exponent, v)
    vertices = [(0,0,size[2]*.5)]
    for j in range(1,rings):
        theta=math.pi*j/rings
        for i in range(segments):
            phi=math.tau*i/segments
            x=size[0]*.5*power(math.sin(theta),exponent)*power(math.cos(phi),exponent)
            y=size[1]*.5*power(math.sin(theta),exponent)*power(math.sin(phi),exponent)
            z=size[2]*.5*power(math.cos(theta),exponent)
            t=z/(size[2]*.5)
            if ('seat_back' in name or name.endswith('_back') or 'back_bolster' in name) and seating:
                y*=.96-.075*t+.025*math.cos(t*math.pi)
                if x>0:
                    x+=.008*math.exp(-((t+.38)/.32)**2)*(1-(y/(size[1]*.5))**2)
            if 'bolster' in name:
                y*=.77+.23*max(0.,1-t*t)
            if 'headrest' in name:
                y*=.94-.06*t
                if x>0:
                    x+=.004*max(0.,1-t*t)*(1-(y/(size[1]*.5))**2)
            vertices.append((x,y,z))
    bottom=len(vertices)
    vertices.append((0,0,-size[2]*.5))
    faces=[(0,1+i,1+(i+1)%segments) for i in range(segments)]
    for j in range(rings-2):
        for i in range(segments):
            a=1+j*segments+i; c=1+j*segments+(i+1)%segments
            faces.append((a,a+segments,c+segments,c))
    faces += [(bottom,1+(rings-2)*segments+(i+1)%segments,1+(rings-2)*segments+i) for i in range(segments)]
    obj=b.mesh(name,vertices,faces,material,smooth=True)
    obj.location=location
    if rotation is not None:
        obj.rotation_euler=rotation
    return obj


def offroader_hood_width(x):
    dx=max(0.,x-1.998)
    return .769+math.sqrt(max(0.,.050**2-dx*dx)) if dx else .819


def offroader_hood_skin(x,y):
    u=max(0.,min(1.,(x-.649)/1.399))
    t=max(-1.,min(1.,y/offroader_hood_width(x)))
    crown=(.043+.006*u)*max(0.,1-t**4)**1.25
    # A 45 mm raised central pressing falls into two shaped shoulders.
    # The relief fades at the hem, preserving hinge and nose shut-line fit.
    shoulder=max(0.,min(1.,(.50-abs(y))/.13))
    shoulder=shoulder*shoulder*(3-2*shoulder)
    relief=.045*math.sin(math.pi*u)**1.1*shoulder
    top=1.448-.025*u+crown+relief
    distance=min(u,1-u,(1-abs(t))*.5)
    thickness=.003+.008*math.exp(-(distance/.035)**2)
    return top,top-thickness


def offroader_boolean(b,obj,cutter,operation='DIFFERENCE'):
    """Resolve a real manufactured opening/union, then remove its tool mesh."""
    bpy.context.view_layer.update()
    bpy.context.view_layer.objects.active=obj
    modifier=obj.modifiers.new('Manufactured '+operation.lower(),'BOOLEAN')
    modifier.operation=operation;modifier.solver='EXACT';modifier.object=cutter
    bpy.ops.object.modifier_apply(modifier=modifier.name)
    b.objects.remove(cutter);bpy.data.objects.remove(cutter,do_unlink=True)


def build_offroader_hood(b, m):
    """Raised centre pressing, open louvered airway, thin hem and hinged liner."""
    # An external axis just above the rear pressing lifts the hem away from
    # the cowl when opening; a buried axis would swing it into the windscreen.
    hinge=offroader_control(b,'hood_hinge_pivot',(.636,0,1.505),-72,0)
    rear,front,half_width,corner=.649,2.048,.819,.050
    # Resolve the last 50 mm as an actual quarter-circle in plan. Dense corner
    # rows retain a curved silhouette instead of a chamfer at the two front tips.
    x_rows=[rear+(front-corner-rear)*i/36 for i in range(37)]
    x_rows += [front-corner+corner*math.sin(math.pi*j/24) for j in range(1,13)]
    ny=64
    section_width=offroader_hood_width
    skin=offroader_hood_skin
    vertices=[]
    for layer in (0,1):
        for x in x_rows:
            width=section_width(x)
            for j in range(ny+1):
                y=(-1+2*j/ny)*width
                vertices.append((x,y,skin(x,y)[layer]))
    nx=len(x_rows)-1
    count=(nx+1)*(ny+1)
    faces=[]
    for i in range(nx):
        for j in range(ny):
            a=i*(ny+1)+j
            faces += [(a,a+ny+1,a+ny+2,a+1),
                      (count+a+1,count+a+ny+2,count+a+ny+1,count+a)]
    boundary=list(range(ny+1))
    boundary += [i*(ny+1)+ny for i in range(1,nx+1)]
    boundary += [nx*(ny+1)+j for j in range(ny-1,-1,-1)]
    boundary += [i*(ny+1) for i in range(nx-1,0,-1)]
    faces += [(a,c,c+count,a+count) for a,c in zip(boundary,boundary[1:]+boundary[:1])]
    hood=b.mesh('formed_hood_shell',vertices,faces,m['paint'],smooth=True,bevel=.0015)
    hood['front_corner_radius_m']=corner
    hood['pressed_skin_thickness_m']=.003
    hood['folded_hem_depth_m']=.011
    hood['central_pressing_relief_m']=.045
    b.parent(hood,hinge)
    # The recess is cut through both skins. A closed, thin-walled tapered
    # duct and tilted vanes are real geometry, carried by the opening hood.
    opening=[(1.575+x,y) for x,y in offroader4_round_plan(.34,.49,.035,steps=8)]
    cutter=offroader_access_panel(b,'hood_intake_cut_tool',
                [(x,y,1.49) for x,y in opening],(0,0,.50),None)
    offroader_boolean(b,hood,cutter)
    hood['intake_opening_m']=[.34,.49]
    rings=[]
    for inset,depth in ((0.,.001),(0.,.067),(.003,.067),(.003,.001)):
        rings.append([(1.575+(x-1.575)*(1-inset/.17),y*(1-inset/.245),skin(x,y)[1]-depth) for x,y in opening])
    # Ring cycle closes wall thickness without capping either air opening.
    n=len(opening);verts=[p for ring in rings for p in ring]
    faces=[(j*n+i,j*n+(i+1)%n,((j+1)%4)*n+(i+1)%n,((j+1)%4)*n+i) for j in range(4) for i in range(n)]
    duct=b.mesh('hood_intake_duct',verts,faces,m['trim'],smooth=True,bevel=.001)
    duct['component']='open_through_hood_air_duct';b.parent(duct,hinge)
    rim=b.tube('hood_intake_rolled_lip',[(x,y,skin(x,y)[0]+.001) for x,y in opening]+[(opening[0][0],opening[0][1],skin(*opening[0])[0]+.001)],.003,m['paint'],resolution=1)
    b.parent(rim,hinge)
    for x in (1.445,1.515,1.585,1.655,1.715):
        z=skin(x,0)[0]-.018
        vane=b.box('hood_intake_louver',(x,0,z),(.036,.480,.005),m['steel'],bevel=.002,rotation=(0,-.30,0))
        b.parent(vane,hinge)
    for x in (1.425,1.725):
        for y in (-.217,.217):
            z=skin(x,y)[0]
            fastener=b.cylinder('hood_intake_retainer',(x,y,z-.004),(x,y,z+.002),.004,m['alloy'],vertices=8)
            fastener['max_lod']=0;b.parent(fastener,hinge)
    # Underside ribs follow the curved skin instead of hanging beneath a flat
    # box. The acoustic liner is another closed, curved sheet.
    for side in (-1,1):
        points=[(.75,side*.67),(1.10,side*.46),(1.66,side*.43),(1.96,side*.66)]
        # Sample the underside at every rib station. A free Bezier between
        # sparse endpoints would cut through the new steeper hood shoulders.
        samples=[]
        for a,c in zip(points,points[1:]):
            samples.extend((a[0]+(c[0]-a[0])*i/12,a[1]+(c[1]-a[1])*i/12) for i in range(12))
        samples.append(points[-1])
        rib=offroader_access_path(b,'hood_inner_pressing',
                    [(x,y,skin(x,y)[1]-.016) for x,y in samples],.011,m['paint'])
        b.parent(rib,hinge)
        b.box('hood_hinge_fixed_pedestal',(.623,side*.48,1.482),(.023,.078,.058),
              m['steel'],bevel=.004)
        b.cylinder('hood_hinge_fixed_pin',(.636,side*.48-.035,1.505),(.636,side*.48+.035,1.505),.013,m['steel'],vertices=16)
        leaf_z=skin(.706,side*.48)[1]-.005
        b.parent(b.box('hood_hinge_leaf',(.706,side*.48,leaf_z),(.13,.060,.008),m['steel'],bevel=.003),hinge)
        b.parent(b.cylinder('hood_hinge_offset_arm',(.636,side*.48,1.505),
                           (.685,side*.48,leaf_z-.004),.013,m['steel'],vertices=12),hinge)
        for x in (.69,.74):
            z=skin(x,side*.48)[1]
            bolt=b.cylinder('hood_hinge_bolt',(x,side*.48,z-.010),(x,side*.48,z+.001),.007,m['alloy'],vertices=6)
            bolt['max_lod']=0
            b.parent(bolt,hinge)
        stop_z=skin(1.91,side*.73)[1]
        b.box('hood_rubber_stop',(1.91,side*.73,stop_z-.020),(.028,.028,.040),m['rubber'],bevel=.008)
    for x in (.84,1.82):
        points=[(x,-.66+1.32*i/16,skin(x,-.66+1.32*i/16)[1]-.015) for i in range(17)]
        b.parent(offroader_access_path(b,'hood_inner_crossbrace',points,.009,m['paint']),hinge)
    liner_vertices=[]
    lx,ly=12,16
    for layer in (0,1):
        for i in range(lx+1):
            x=.93+.76*i/lx
            for j in range(ly+1):
                y=-.36+.72*j/ly
                liner_vertices.append((x,y,skin(x,y)[1]-.004-.012*layer))
    n=(lx+1)*(ly+1); liner_faces=[]
    for i in range(lx):
        for j in range(ly):
            a=i*(ly+1)+j
            liner_faces.extend(((a,a+ly+1,a+ly+2,a+1),(n+a+1,n+a+ly+2,n+a+ly+1,n+a)))
    edge=list(range(ly+1))+[i*(ly+1)+ly for i in range(1,lx+1)]
    edge += [lx*(ly+1)+j for j in range(ly-1,-1,-1)]+[i*(ly+1) for i in range(lx-1,0,-1)]
    liner_faces.extend((a,c,c+n,a+n) for a,c in zip(edge,edge[1:]+edge[:1]))
    liner=b.mesh('hood_acoustic_liner',liner_vertices,liner_faces,m['trim'],smooth=True,bevel=.003)
    b.parent(liner,hinge)
    cut=offroader_access_panel(b,'hood_liner_airway_tool',
                [(x,y,1.49) for x,y in opening],(0,0,.50),None)
    offroader_boolean(b,liner,cut)
    latch_top=skin(1.985,0)[1]
    b.parent(b.tube('hood_latch_loop',[(1.985,-.025,latch_top),(1.985,-.025,1.375),
              (1.985,.025,1.375),(1.985,.025,latch_top)],.004,m['steel']),hinge)
    b.box('hood_latch_receiver',(1.99,0,1.369),(.07,.11,.028),m['steel'],bevel=.006)



def build_offroader_front(b, m):
    """A wrapping stamped fascia around real lamp apertures and an open grille."""
    # Continuous U-shaped front stamping: the outer corners have a 100 mm
    # radius. Upper/lower folded returns provide actual sheet thickness.
    path=[(1.930,.756,0.,1.),(1.974,.756,0.,1.)]
    path += [(1.974+.10*math.cos(a),.756+.10*math.sin(a),math.cos(a),math.sin(a))
             for a in [math.pi/2-math.pi*i/48 for i in range(1,25)]]
    path += [(2.074,.756-1.512*i/40,1.,0.) for i in range(1,41)]
    path += [(1.974+.10*math.cos(a),-.756+.10*math.sin(a),math.cos(a),math.sin(a))
             for a in [-math.pi*i/48 for i in range(1,25)]]
    path += [(1.930,-.856,0.,-1.)]
    # The first two points belong to the side, at y=.856.
    path[0]=(1.930,.856,0.,1.);path[1]=(1.974,.856,0.,1.)
    rings=[]
    for x,y,nx,ny in path:
        top=offroader_hood_skin(min(x,2.048),max(-offroader_hood_width(min(x,2.048)),min(offroader_hood_width(min(x,2.048)),y)))[1]-.006
        section=[(0.,.860),(0.,top-.004),(.003,top),(.060,top),(.060,top-.003),
                 (.006,top-.003),(.003,top-.006),(.003,.863),(.025,.863),(.025,.860)]
        ring=[]
        for d,z in section:
            shoulder=.020*max(0.,min(1.,(z-1.15)/.24))**2
            ring.append((x-nx*d,y-ny*(d+shoulder),z))
        rings.append(ring)
    nose=offroader4_closed_rings(b,'continuous_front_panel_crossrail',rings,m['paint'])
    for side in (-1,1):
        cut=b.cylinder('headlamp_aperture_tool',(1.85,side*.636,1.209),
                       (2.20,side*.636,1.209),.13455,None,vertices=64)
        offroader_boolean(b,nose,cut)
        b.box('grille_side_return',(2.035,side*.454,1.185),(.11,.040,.39),m['trim'],bevel=.012)
    opening=offroader4_round_plan(.894,.337,.018,steps=8)
    cut=offroader_access_panel(b,'grille_aperture_tool',[(2.05,y,1.185+z) for y,z in opening],(.35,0,0),None)
    offroader_boolean(b,nose,cut)
    # Join the wrap to the fixed wings before final normals/UVs. The visible
    # corner no longer consists of independent headlamp plates and rails.
    for wing in [o for o in b.objects if o.name.endswith('_body_side')]:
        bpy.context.view_layer.objects.active=wing
        for modifier in list(wing.modifiers):
            bpy.ops.object.modifier_apply(modifier=modifier.name)
        offroader_boolean(b,nose,wing,'UNION')
    nose['component']='continuous_front_and_side_body'
    nose['corner_radius_m']=.100
    nose['sheet_thickness_m']=.003
    # Name retains the construction-check selectors, with a single mesh.
    nose.name=b.name+'_body_side_stamped_headlamp_surround'
    # Keep the planar optical mounting face flat shaded. Averaging its
    # normals with the aperture walls creates false dents around the lamps.
    for face in nose.data.polygons:
        if abs(face.normal.x)>.9999 and face.center.x>2.060:
            face.use_smooth=False
    for z in (1.006,1.361):
        b.box('grille_border',(2.073,0,z),(.050,.935,.024),m['trim'],bevel=.008)
    for z in (1.074,1.181,1.288):
        # Mild convex plan curvature catches a continuous reflection.
        rings=[]
        for j in range(17):
            y=-.432+.864*j/16
            x=2.081+.014*(1-(y/.432)**2)
            rings.append([(x+dx,y,z+dz) for dx,dz in
                          ((-.030,-.010),(.022,-.010),(.030,-.004),
                           (.030,.004),(.022,.010),(-.030,.010))])
        offroader4_closed_rings(b,'grille_aero_slat',rings,m['steel'])
    # Fine expanded-mesh backing is separated from the radiator behind it.
    for y in [i*.052 for i in range(-8,9)]:
        obj=b.cylinder('grille_mesh_vertical',(2.014,y,1.025),(2.014,y,1.342),.0025,m['steel'],vertices=6)
        obj['max_lod']=1
    for z in (1.044,1.10,1.156,1.212,1.268,1.324):
        obj=b.cylinder('grille_mesh_horizontal',(2.014,-.416,z),(2.014,.416,z),.0025,m['steel'],vertices=6)
        obj['max_lod']=1
    for side in (-1,1):
        cy,cz=side*.636,1.209
        build_offroader_headlamp(b,m,cy,cz)
        for dy,dz in ((-.151,.14),(.151,-.14)):
            obj=b.cylinder('headlamp_retainer',(2.076,cy+dy,cz+dz),(2.081,cy+dy,cz+dz),.005,m['alloy'],vertices=6)
            obj['max_lod']=0


def offroader_optical_lathe(b,name,profile,cy,cz,material,segments=48,smooth=True):
    """Closed radial optical shell; profile is a complete wall cross-section."""
    verts=[(x,cy+r*math.cos(i*math.tau/segments),cz+r*math.sin(i*math.tau/segments))
           for x,r in profile for i in range(segments)]
    faces=[(j*segments+i,j*segments+(i+1)%segments,((j+1)%len(profile))*segments+(i+1)%segments,((j+1)%len(profile))*segments+i)
           for j in range(len(profile)) for i in range(segments)]
    obj=b.mesh(name,verts,faces,material,smooth=smooth)
    bm=bmesh.new();bm.from_mesh(obj.data);bmesh.ops.recalc_face_normals(bm,faces=list(bm.faces));bm.to_mesh(obj.data);bm.free()
    return obj


def build_offroader_headlamp(b,m,cy,cz):
    """A recessed reflector/projector behind a thin, convex clear cover."""
    offroader_optical_lathe(b,'headlamp_recess',
        [(1.993,.056),(1.993,.127),(2.074,.133),(2.078,.130),
         (2.078,.125),(2.018,.112),(2.005,.056)],cy,cz,m['trim'])
    profile=[(2.020,.056),(2.026,.075),(2.039,.095),(2.054,.109),(2.069,.122)]
    profile += [(x-.002,r) for x,r in reversed(profile)]
    bowl=offroader_optical_lathe(b,'headlamp_parabolic_reflector',profile,cy,cz,m['reflector'],segments=48)
    bowl['component']='closed_parabolic_optical_reflector'
    offroader_optical_lathe(b,'headlamp_projector_body',
        [(2.018,.046),(2.018,.055),(2.069,.055),(2.069,.046)],cy,cz,m['steel'],segments=32)
    b.cylinder('headlamp_projector_rear_reflector',(2.020,cy,cz),(2.023,cy,cz),.045,m['reflector'],vertices=32)
    b.cylinder('headlamp_led_chip',(2.025,cy,cz),(2.028,cy,cz),.010,m['lightguide'],vertices=16)
    b.torus('headlamp_projector_bezel',(2.069,cy,cz),.049,.003,m['reflector'],axis='X',major_segments=48,minor_segments=6)
    b.sphere('headlamp_convex_projector',(2.074,cy,cz),(.010,.045,.045),m['optic'],segments=32,rings=12)
    b.torus('headlamp_drl_lightguide',(2.077,cy,cz),.112,.0025,m['lightguide'],axis='X',major_segments=64,minor_segments=6)
    b.torus('headlamp_outer_gasket',(2.076,cy,cz),.130,.0035,m['rubber'],axis='X',major_segments=64,minor_segments=6)
    # A 2.5 mm optical meniscus instead of a flattened solid glass sphere.
    segments,rows=64,8;radius=.126
    verts=[]
    for layer in (0,1):
        offset=-.0025*layer
        verts.append((2.098+offset,cy,cz))
        for row in range(1,rows+1):
            r=radius*row/rows;x=2.080+.018*(1-(r/radius)**2)+offset
            verts.extend((x,cy+r*math.cos(i*math.tau/segments),cz+r*math.sin(i*math.tau/segments)) for i in range(segments))
    count=1+rows*segments;faces=[]
    for layer in (0,1):
        base=layer*count;cap=[]
        cap += [(base,base+1+i,base+1+(i+1)%segments) for i in range(segments)]
        for row in range(rows-1):
            for i in range(segments):
                a=base+1+row*segments+i;c=base+1+row*segments+(i+1)%segments
                cap.append((a,a+segments,c+segments,c))
        faces += cap if layer==0 else [tuple(reversed(face)) for face in cap]
    edge=1+(rows-1)*segments
    faces += [(edge+i,edge+(i+1)%segments,count+edge+(i+1)%segments,count+edge+i) for i in range(segments)]
    cover=b.mesh('headlamp_outer_cover',verts,faces,m['optic'],smooth=True)
    bm=bmesh.new();bm.from_mesh(cover.data);bmesh.ops.recalc_face_normals(bm,faces=list(bm.faces));bm.to_mesh(cover.data);bm.free()
    cover['optical_cover_thickness_m']=.0025
    cover['component']='thin_convex_headlamp_cover'
    for i in range(3):
        a=math.tau*i/3+.30
        y,z=cy+.122*math.cos(a),cz+.122*math.sin(a)
        tab=b.box('headlamp_internal_retaining_tab',(2.068,y,z),(.009,.014,.012),m['steel'],bevel=.002)
        tab['max_lod']=1


def build_offroader_indicator(b,m,name,origin,normal,u_axis,width,height,cells):
    """Unlit amber optical module with a bezel, cups and a ribbed inner lens."""
    origin=Vector(origin);normal=Vector(normal);u_axis=Vector(u_axis);v_axis=Vector((0,0,1))
    def point(d,u,v):return tuple(origin+normal*d+u_axis*u+v_axis*v)
    outline=offroader4_round_plan(width,height,min(.009,height*.23),steps=4)
    n=len(outline)
    rings=[]
    for depth,extra in ((-.004,.010),(.017,.010),(.020,0.),(.003,0.)):
        rings.append([point(depth,u*(1+extra/width),v*(1+extra/height)) for u,v in outline])
    verts=[p for ring in rings for p in ring]
    faces=[(j*n+i,j*n+(i+1)%n,((j+1)%4)*n+(i+1)%n,((j+1)%4)*n+i) for j in range(4) for i in range(n)]
    bezel=b.mesh(name+'_housing',verts,faces,m['trim'],smooth=True)
    bezel['component']='indicator_housing';bezel['optical_axis']=list(normal)
    backing=offroader_access_panel(b,name+'_reflector_back',[point(.004,u*.95,v*.9) for u,v in outline],tuple(normal*.002),m['reflector'])
    for cell in range(cells):
        u=(cell-(cells-1)*.5)*(width-.018)/cells
        radius=min((width-.018)/cells*.45,height*.39)
        rings=[]
        for depth,r in ((.006,radius*.24),(.009,radius*.60),(.015,radius),(.014,radius),(.004,radius*.24)):
            rings.append([point(depth,u+r*math.cos(i*math.tau/12),r*math.sin(i*math.tau/12)) for i in range(12)])
        verts=[p for ring in rings for p in ring]
        faces=[(j*12+i,j*12+(i+1)%12,((j+1)%5)*12+(i+1)%12,((j+1)%5)*12+i) for j in range(5) for i in range(12)]
        cup=b.mesh(name+'_faceted_reflector',verts,faces,m['reflector'])
        cup['max_lod']=1
        b.cylinder(name+'_amber_emitter',point(.009,u,0),point(.013,u,0),radius*.30,m['indicator_chip'],vertices=12)
    # Rounded rectangular glazing grid. Fine inner ribs catch highlights;
    # the exterior remains a continuous gently bowed injection-moulded face.
    nx,ny=24,4;corner=min(.008,height*.22)
    us=[-width*.5+width*i/nx for i in range(nx+1)];verts=[]
    for layer in (0,1):
        for i,u in enumerate(us):
            du=max(0.,abs(u)-(width*.5-corner))
            half=height*.5-corner+math.sqrt(max(0.,corner*corner-du*du))
            for j in range(ny+1):
                v=(-1+2*j/ny)*half
                bow=.004*max(0.,1-(2*u/width)**2)*max(0.,1-(v/max(half,1e-6))**2)
                prism=.00045*(i%2) if layer else 0.
                verts.append(point(.022+bow-.002*layer-prism,u,v))
    count=(nx+1)*(ny+1);faces=[]
    for i in range(nx):
        for j in range(ny):
            a=i*(ny+1)+j
            faces += [(a,a+ny+1,a+ny+2,a+1),(count+a+1,count+a+ny+2,count+a+ny+1,count+a)]
    boundary=list(range(ny+1))+[i*(ny+1)+ny for i in range(1,nx+1)]
    boundary += [nx*(ny+1)+j for j in range(ny-1,-1,-1)]+[i*(ny+1) for i in range(nx-1,0,-1)]
    faces += [(a,c,c+count,a+count) for a,c in zip(boundary,boundary[1:]+boundary[:1])]
    lens=b.mesh(name+'_optical_cover',verts,faces,m['indicator_glass'],smooth=True)
    lens['component']='thin_prismatic_amber_cover';lens['optical_axis']=list(normal)
    lens['optical_cover_thickness_m']=.002;lens['module_origin']=list(origin)
    # Consistent outward normals for modules on all three sides of the car.
    for obj in (bezel,backing,lens):
        bm=bmesh.new();bm.from_mesh(obj.data);bmesh.ops.recalc_face_normals(bm,faces=list(bm.faces));bm.to_mesh(obj.data);bm.free()



def build_offroader_engine(b, m):
    """Visible inline-four package. No hidden pistons or simulated powertrain."""
    start=len(b.objects)
    b.box('engine_firewall',(.628,0,1.055),(.025,1.58,.62),m['paint'],bevel=.012)
    for side in (-1,1):
        b.box('engine_bay_accessory_tray',(1.28,side*.665,1.017),(1.16,.28,.025),m['paint'],bevel=.012)
        # Arched inner fenders leave an actual wheel cavity below the tray.
        verts=[]
        for y in (side*.51,side*.82):
            for i in range(25):
                angle=math.pi*i/24
                verts.append((1.32+.565*math.cos(angle),y,.43+.565*math.sin(angle)))
        b.mesh('engine_inner_fender',verts,[(i,i+1,26+i,25+i) for i in range(24)],m['trim'],smooth=True)
        b.cylinder('engine_mount_bracket',(1.20,side*.20,.79),(1.20,side*.48,.67),.044,m['steel'],vertices=16)
        b.box('engine_mount_isolator',(1.20,side*.38,.72),(.13,.11,.065),m['rubber'],bevel=.014)
    b.box('engine_cast_block',(1.23,0,.979),(.66,.38,.34),m['cast'],bevel=.034)
    b.box('engine_sump',(1.25,0,.769),(.56,.32,.10),m['steel'],bevel=.025)
    b.box('engine_head',(1.24,0,1.170),(.68,.40,.080),m['alloy'],bevel=.018)
    b.box('engine_cam_cover',(1.24,0,1.236),(.65,.34,.067),m['trim'],bevel=.029)
    for y in (-.095,0,.095):
        b.box('engine_cover_rib',(1.25,y,1.273),(.50,.012,.009),m['alloy'],bevel=.004)
    b.cylinder('engine_oil_cap',(1.45,.085,1.27),(1.45,.085,1.291),.033,m['trim'],vertices=24)
    b.tube('engine_dipstick',[(1.33,-.24,.89),(1.39,-.23,1.14),(1.44,-.21,1.20)],.004,m['steel'])
    b.torus('engine_dipstick_handle',(1.44,-.21,1.218),.019,.004,m['amber'],axis='Y',major_segments=20,minor_segments=6)
    for x in (1.0,1.16,1.32,1.48):
        for side in (-1,1):
            screw=b.cylinder('engine_cover_fastener',(x,side*.148,1.27),(x,side*.148,1.278),.007,m['steel'],vertices=6)
            screw['max_lod']=0
        b.tube('intake_runner',[(x,.19,1.13),(x,.30,1.13),(x,.32,1.03)],.025,m['cast'])
        b.tube('exhaust_header',[(x,-.19,1.12),(x,-.28,1.04),(1.28,-.32,.95)],.023,m['steel'])
    b.box('intake_plenum',(1.25,.32,1.017),(.58,.12,.11),m['trim'],bevel=.035)
    b.cylinder('bellhousing',(.78,0,.78),(.93,0,.80),.165,m['cast'],vertices=32,radius2=.19)
    b.tube('exhaust_downpipe',[(1.28,-.32,.95),(1.42,-.30,.88),(1.36,-.26,.69),(1.22,-.26,.63)],.027,m['steel'])
    b.box('exhaust_heat_shield',(1.25,-.355,1.10),(.54,.025,.20),m['alloy'],bevel=.022)
    for z in (.82,1.035):
        b.cylinder('belt_pulley',(1.565,0,z),(1.591,0,z),.072,m['steel'],vertices=32)
        b.torus('pulley_lip',(1.595,0,z),.068,.004,m['alloy'],axis='X',major_segments=32,minor_segments=6)
    b.cylinder('alternator',(1.43,.26,.94),(1.58,.26,.94),.073,m['cast'],vertices=24)
    b.cylinder('alternator_pulley',(1.585,.26,.94),(1.604,.26,.94),.044,m['steel'],vertices=24)
    b.tube('serpentine_belt',[(1.603,-.07,.82),(1.603,-.07,1.035),(1.603,0,1.107),
            (1.603,.26,.984),(1.603,.304,.94),(1.603,.26,.896),(1.603,0,.748),(1.603,-.07,.82)],.006,m['rubber'],resolution=1)
    b.box('radiator_core',(1.909,0,1.065),(.050,1.17,.43),m['steel'],bevel=.012)
    for side in (-1,1):
        b.box('radiator_side_tank',(1.909,side*.61,1.065),(.095,.085,.46),m['trim'],bevel=.020)
    for i in range(30):
        fin=b.box('radiator_visible_fin',(1.938,-.56+i*.039,1.065),(.006,.008,.395),m['alloy'],bevel=.001)
        fin['max_lod']=1
    b.box('radiator_top_support',(1.91,0,1.319),(.13,1.41,.035),m['paint'],bevel=.010)
    b.torus('cooling_fan_shroud',(1.835,0,1.03),.231,.020,m['trim'],axis='X',major_segments=48,minor_segments=8)
    b.cylinder('cooling_fan_hub',(1.79,0,1.03),(1.85,0,1.03),.056,m['trim'],vertices=24)
    for i in range(7):
        a=i*math.tau/7
        points=[(1.813,.048*math.cos(a),1.03+.048*math.sin(a)),
                (1.800,.215*math.cos(a+.18),1.03+.215*math.sin(a+.18)),
                (1.826,.218*math.cos(a+.48),1.03+.218*math.sin(a+.48)),
                (1.831,.062*math.cos(a+.65),1.03+.062*math.sin(a+.65))]
        b.mesh('cooling_fan_blade',points,[(0,1,2,3)],m['trim'])
    b.tube('upper_radiator_hose',[(1.89,.47,1.25),(1.65,.43,1.23),(1.48,.22,1.15)],.026,m['rubber'])
    b.tube('lower_radiator_hose',[(1.89,-.47,.89),(1.70,-.40,.83),(1.51,-.20,.86)],.024,m['rubber'])
    b.box('battery_case',(1.08,.636,1.137),(.33,.24,.21),m['trim'],bevel=.012)
    b.box('battery_lid',(1.08,.636,1.251),(.35,.25,.025),m['interior'],bevel=.008)
    for x,material in ((.97,m['red']),(1.19,m['steel'])):
        b.cylinder('battery_terminal',(x,.62,1.26),(x,.62,1.287),.012,m['alloy'],vertices=12)
        b.box('battery_terminal_boot',(x,.62,1.284),(.05,.043,.026),material,bevel=.006)
    b.tube('battery_positive_cable',[(.97,.62,1.282),(.91,.53,1.20),(.83,.37,.95)],.007,m['red'])
    b.tube('battery_ground',[(1.19,.62,1.282),(1.26,.74,1.14),(1.34,.76,1.03)],.007,m['rubber'])
    b.box('battery_hold_down',(1.08,.636,1.277),(.035,.27,.012),m['steel'],bevel=.003)
    b.box('air_filter_box',(1.14,-.64,1.151),(.40,.28,.23),m['trim'],bevel=.032)
    for x in (.99,1.07,1.15,1.23,1.31):
        b.box('airbox_lid_rib',(x,-.64,1.272),(.013,.22,.012),m['interior'],bevel=.004)
    b.tube('snorkel_to_airbox',[(1.12,-.89,1.27),(1.12,-.79,1.22),(1.14,-.70,1.21)],.044,m['rubber'])
    b.tube('air_intake_duct',[(1.32,-.64,1.20),(1.57,-.57,1.24),(1.66,-.30,1.26),(1.63,.26,1.12),(1.50,.32,1.035)],.038,m['rubber'])
    # Thin stainless bands align with the actual hose tangent at each connection.
    for label,centre,direction,radius in (
        ('intake_airbox',(1.335,-.636,1.203),(.25,.07,.04),.039),
        ('intake_plenum',(1.516,.313,1.045),(-.13,.06,-.085),.039),
        ('upper_radiator',(1.873,.467,1.249),(-.24,-.04,-.02),.027),
        ('upper_thermostat',(1.492,.235,1.156),(-.17,-.21,-.08),.027),
        ('lower_radiator',(1.875,-.464,.885),(-.19,.07,-.06),.025),
        ('lower_waterpump',(1.522,-.213,.858),(-.19,.20,.03),.025),
    ):
        axis=Vector(direction).normalized()
        u=axis.cross(Vector((0,0,1))).normalized(); v=axis.cross(u).normalized()
        centre=Vector(centre); verts=[]
        for axial,r in ((-.005,radius),(.005,radius),(.005,radius+.001),(-.005,radius+.001)):
            verts.extend(tuple(centre+axis*axial+(u*math.cos(i*math.tau/32)+v*math.sin(i*math.tau/32))*r) for i in range(32))
        faces=[(j*32+i,j*32+(i+1)%32,((j+1)%4)*32+(i+1)%32,((j+1)%4)*32+i) for j in range(4) for i in range(32)]
        band=b.mesh(label+'_hose_clamp',verts,faces,m['alloy'],smooth=True)
        band['max_lod']=1
        pos=centre+v*(radius+.004)
        fastener=b.cylinder(label+'_clamp_screw',pos-axis*.009,pos+axis*.009,.0035,m['steel'],vertices=8)
        fastener['max_lod']=0
    for name,x,y,z,size in (('coolant_expansion_tank',1.70,.59,1.16,(.22,.20,.18)),
                           ('washer_reservoir',.79,-.64,1.12,(.19,.22,.17)),
                           ('brake_fluid_reservoir',.75,.40,1.25,(.15,.10,.075))):
        b.box(name,(x,y,z),size,m['reservoir'],bevel=.025)
        b.cylinder(name+'_cap',(x,y,z+size[2]*.5),(x,y,z+size[2]*.5+.021),.029,
                   m['blue'] if name=='washer_reservoir' else m['trim'],vertices=24)
    b.cylinder('brake_booster',(.65,.40,1.15),(.70,.40,1.15),.115,m['trim'],vertices=32)
    b.box('fuse_box',(.79,.65,1.14),(.17,.22,.18),m['trim'],bevel=.018)
    b.tube('engine_wiring_harness',[(.66,-.40,1.25),(.70,0,1.30),(.75,.51,1.28),(1.26,.77,1.12),(1.72,.74,1.10)],.009,m['rubber'])
    for x in (1.0,1.16,1.32,1.48):
        b.box('ignition_coil_pack',(x,-.07,1.288),(.074,.075,.026),m['trim'],bevel=.006)
        connector=b.box('coil_electrical_connector',(x,-.122,1.286),(.044,.035,.020),m['interior'],bevel=.003)
        connector['max_lod']=1
        lock=b.box('connector_lock_tab',(x,-.124,1.298),(.018,.022,.004),m['blue'],bevel=.001)
        lock['max_lod']=0
        b.tube('coil_wiring',[(x,-.139,1.285),(x-.022,-.193,1.283),(x-.05,-.205,1.268)],.0035,m['rubber'])
    b.tube('ignition_wire_loom',[(.73,-.22,1.26),(1.08,-.205,1.268),(1.51,-.205,1.268)],.007,m['rubber'])
    for x in (.94,1.23,1.47):
        clip=b.box('wire_loom_mount',(x,-.205,1.264),(.016,.025,.022),m['trim'],bevel=.003)
        clip['max_lod']=1
    for x in (.96,1.20):
        b.box('battery_tray_bracket',(x,.636,1.028),(.028,.29,.018),m['steel'],bevel=.003)
        for y in (.50,.77):
            bolt=b.cylinder('battery_bracket_bolt',(x,y,1.033),(x,y,1.044),.006,m['alloy'],vertices=6)
            bolt['max_lod']=0
    for x in (.99,1.29):
        clip=b.box('airbox_lid_clip',(x,-.785,1.245),(.025,.010,.046),m['alloy'],bevel=.002)
        clip['max_lod']=1
    offroader_text(b,'battery_warning','12 V',(.99,.69,1.269),.025,m['stitch'],facing='top')
    assembly=b.empty('engine_bay_assembly')
    for obj in b.objects[start:-1]:
        b.parent(obj,assembly)


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
        for row in (0,):
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
        # Sloping shoulder blocks follow the carcass instead of floating over it.
        shoulder_vertices=[]; shoulder_faces=[]
        for side in (-1,1):
            for i in range(32):
                a=math.tau*(i+.5)/32
                base=len(shoulder_vertices)
                for depth in (.014,0):
                    for axial,radius,tangent in ((.055,.424,-.024),(.124,.389,-.018),
                                                  (.124,.389,.018),(.055,.424,.024)):
                        radial=radius-depth
                        skew=tangent+side*(axial-.055)*.20
                        shoulder_vertices.append(point(radial*math.sin(a)+skew*math.cos(a),
                                                       side*axial,radial*math.cos(a)-skew*math.sin(a)))
                shoulder_faces.extend(tuple(base+j for j in face) for face in
                    ((3,2,1,0),(4,5,6,7),(0,1,5,4),(1,2,6,5),(2,3,7,6),(3,0,4,7)))
        shoulder=attach(b.mesh(name+'_tapered_tread_shoulders',shoulder_vertices,shoulder_faces,m['tread'],bevel=.0015))
        shoulder.modifiers[0].segments=1
        # A very thin conforming shell carries reusable moulded lettering maps.
        segments=144; vertices=[]; faces=[]
        for radius,axial in ((.295,.1375),(.326,.1385),(.357,.1395)):
            for i in range(segments):
                a=-outward*(math.tau*i/segments-math.pi)
                vertices.append(point(radius*math.sin(a),axial,radius*math.cos(a)))
        for j in range(2):
            for i in range(segments):
                faces.append((j*segments+i,j*segments+(i+1)%segments,
                              (j+1)*segments+(i+1)%segments,(j+1)*segments+i))
        lettering=attach(b.mesh(name+'_moulded_sidewall',vertices,faces,m['sidewall_marking'],smooth=True))
        lettering['preserve_uv']=True; lettering['max_lod']=1
        lettering['sidewall_size']='285/75 R17'; lettering['sidewall_brand']='TRAIL A/T'
        uv=lettering.data.uv_layers.new(name='UVMap')
        for poly in lettering.data.polygons:
            j,i=divmod(poly.index,segments)
            coords=((i/segments,j/2),((i+1)/segments,j/2),((i+1)/segments,(j+1)/2),(i/segments,(j+1)/2))
            for loop,coord in zip(poly.loop_indices,coords):
                uv.data[loop].uv=coord
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
    b.box('gearbox_housing',(.56,0,.70),(.55,.30,.26),m['alloy'],bevel=.065)
    b.box('transfer_case',(.03,0,.59),(.37,.35,.32),m['steel'],bevel=.055)
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

def offroader4_soft_pad(b, name, location, size, material, *, headrest=False, rotation=None):
    """Sewn small upholstery with a continuously rounded, gently compressed edge."""
    segments,rings=32,16
    exponent=.62 if headrest else .55
    def power(value):
        return math.copysign(abs(value)**exponent,value)
    vertices=[(0,0,size[2]*.5-(0. if headrest else .0025))]
    for j in range(1,rings):
        theta=math.pi*j/rings
        t=power(math.cos(theta))
        for i in range(segments):
            phi=math.tau*i/segments
            x=size[0]*.5*power(math.sin(theta))*power(math.cos(phi))
            y=size[1]*.5*power(math.sin(theta))*power(math.sin(phi))
            z=size[2]*.5*t
            if headrest:
                y*=.96-.075*t
                if x>0:
                    x+=.0035*(1-t*t)*max(0.,1-(y/(size[1]*.5))**2)
            elif z>0:
                # A very shallow elbow dish avoids a uniformly inflated pillow.
                z-=.0025*math.exp(-(x/(size[0]*.35))**4-(y/(size[1]*.36))**4)
            vertices.append((x,y,z))
    bottom=len(vertices);vertices.append((0,0,-size[2]*.5))
    faces=[(0,1+i,1+(i+1)%segments) for i in range(segments)]
    for j in range(rings-2):
        for i in range(segments):
            a=1+j*segments+i;c=1+j*segments+(i+1)%segments
            faces.append((a,a+segments,c+segments,c))
    faces.extend((bottom,1+(rings-2)*segments+(i+1)%segments,1+(rings-2)*segments+i) for i in range(segments))
    obj=b.mesh(name,vertices,faces,material,smooth=True)
    obj.location=location
    if rotation is not None:
        obj.rotation_euler=rotation
    return obj


def offroader4_console_shell(b, name, stations, material):
    """Molded console swept through varying-width rounded sections in metres."""
    rings=[]
    for i in range(len(stations)-1):
        before=stations[max(0,i-1)];a=stations[i];c=stations[i+1];after=stations[min(len(stations)-1,i+2)]
        for step in range(4):
            t=step/4
            x=a[0]+(c[0]-a[0])*t
            values=[]
            for k in range(1,4):
                values.append(.5*(2*a[k]+(-before[k]+c[k])*t+
                    (2*before[k]-5*a[k]+4*c[k]-after[k])*t*t+
                    (-before[k]+3*a[k]-3*c[k]+after[k])*t*t*t))
            width,low,high=values
            radius=min(width,high-low)*.32
            rings.append([(x,y,z) for y,z in offroader3_cockpit_round_outline(width,high-low,radius,0,(high+low)*.5,steps=5)])
    x,width,low,high=stations[-1]
    rings.append([(x,y,z) for y,z in offroader3_cockpit_round_outline(width,high-low,min(width,high-low)*.32,0,(high+low)*.5,steps=5)])
    return offroader3_cockpit_loft(b,name,rings,material,caps=True)


def build_offroader_cockpit(b, m):
    """Sculpted cabin mouldings with the original static art and control pivots."""
    plastic, alloy, rubber = m['interior'], m['alloy'], m['rubber']
    # Pale, unlit screen-printed control legends share a real packed PBR material.
    legend=b.mat('offroader_control_legend',(.47,.53,.52),roughness=.66)

    def rounded_box(name, location, size, material, radius, segments=4):
        obj=b.box(name,location,size,material,bevel=radius)
        obj.modifiers[0].segments=segments
        return obj

    # Round the cross-section in geometry so the long top pad has a rolled
    # leading edge, not a large flat chamfer. End stations wrap toward the doors.
    corners=[(.62,1.210),(.62,1.405),(.554,1.452),(.386,1.458),
             (.319,1.432),(.280,1.382),(.298,1.241),(.328,1.195),(.391,1.162)]
    profile=[]
    for j,point in enumerate(corners):
        previous=Vector(corners[j-1]); current=Vector(point); following=Vector(corners[(j+1)%len(corners)])
        before=current+(previous-current)*.24; after=current+(following-current)*.24
        for i in range(5):
            t=i/4
            p=(1-t)**2*before+2*(1-t)*t*current+t*t*after
            profile.append(tuple(p))
    rings=[]
    for i in range(17):
        y=-.715+1.430*i/16
        wrap=(abs(y)/.715)**8
        rings.append([(x+.035*wrap,y,z-.025*wrap) for x,z in profile])
    offroader3_cockpit_loft(b,'sculpted_dashboard',rings,plastic,caps=True)
    b.tube('dashboard_seam',[(.344,-.66,1.431),(.334,0,1.440),(.344,.66,1.431)],.0011,m['stitch'])['max_lod']=1
    rounded_box('dashboard_trim_inlay',(.298,0,1.244),(.014,1.34,.012),alloy,.004)

    # A shallow arched binnacle grows out of the dash. Its rolled upper lip and
    # tapered rear occupy the existing envelope; the wheel retains clearance.
    def binnacle_outline(width, top, bottom, shoulder):
        cy=.424; half=width*.5; radius=.018
        points=[(cy-half+radius,bottom),(cy+half-radius,bottom)]
        for i in range(1,6):
            a=math.radians(-90+90*i/5)
            points.append((cy+half-radius+radius*math.cos(a),bottom+radius+radius*math.sin(a)))
        for i in range(25):
            a=math.pi*i/24
            points.append((cy+half*math.cos(a),shoulder+(top-shoulder)*math.sin(a)))
        for i in range(6):
            a=math.radians(180+90*i/5)
            points.append((cy-half+radius+radius*math.cos(a),bottom+radius+radius*math.sin(a)))
        return points[:-1]
    sections=[(.443,.346,1.462,1.348,1.402),(.363,.382,1.500,1.335,1.420),
              (.277,.397,1.524,1.333,1.431),(.236,.398,1.530,1.337,1.432),
              (.225,.390,1.527,1.341,1.432),(.226,.365,1.518,1.352,1.433),
              (.251,.365,1.518,1.352,1.433)]
    rings=[[(x,y,z) for y,z in binnacle_outline(w,t,lo,s)] for x,w,t,lo,s in sections]
    offroader3_cockpit_loft(b,'instrument_hood',rings,m['trim'],closed=True)
    face=[(.251,y,z) for y,z in binnacle_outline(.365,1.518,1.352,1.433)]
    b.mesh('instrument_recessed_backplate',face,[tuple(range(len(face)))],m['trim'])
    for index,cy in enumerate((.337,.510)):
        cz=1.429
        b.cylinder('instrument_dial',(.239,cy,cz),(.248,cy,cz),.073,m['display'],vertices=48)
        b.torus('instrument_bezel',(.235,cy,cz),.073,.0025,alloy,axis='X',major_segments=48,minor_segments=6)
        for tick in range(25):
            a=math.radians(-135+tick*270/24)
            radius=.055 if tick%4==0 else .061
            b.cylinder('instrument_scale',(.231,cy+radius*math.sin(a),cz+radius*math.cos(a)),
                       (.231,cy+.066*math.sin(a),cz+.066*math.cos(a)),.0009,m['screen_ink'],vertices=4)['max_lod']=0
        for number in range(6):
            a=math.radians(-135+number*54)
            offroader_text(b,'gauge_number',str(number*(40 if index==0 else 1)),
                           (.228,cy+.044*math.sin(a),cz+.044*math.cos(a)-.004),.010,m['screen_ink'])
        a=math.radians(-135 if index==0 else -100)
        b.cylinder('instrument_needle',(.222,cy,cz),(.222,cy+.051*math.sin(a),cz+.051*math.cos(a)),.0017,m['red'],vertices=6)
        b.cylinder('instrument_needle_cap',(.220,cy,cz),(.226,cy,cz),.006,m['steel'],vertices=16)
        offroader_text(b,'gauge_unit','km/h' if index==0 else 'x1000',(.225,cy,1.401),.009,m['screen_ink'])

    # A continuous lower fascia carries both screen and controls. Rounded
    # perimeter rings make a real open screen bezel with an inset glass plane.
    rounded_box('center_stack_fascia',(.311,-.130,1.265),(.087,.382,.402),plastic,.030,5)
    screen_cy,screen_cz=-.130,1.356
    frame_sections=[(.312,.365,.234,.024),(.279,.365,.234,.024),(.253,.349,.217,.019),
                    (.248,.337,.205,.014),(.260,.307,.178,.009),(.269,.307,.178,.009)]
    rings=[[(x,y,z) for y,z in offroader3_cockpit_round_outline(w,h,r,screen_cy,screen_cz)]
           for x,w,h,r in frame_sections]
    offroader3_cockpit_loft(b,'center_screen_frame',rings,m['trim'],closed=True)
    rounded_box('center_screen_surface',(.268,-.130,1.356),(.005,.307,.178),m['display'],.004)
    # The original authored route, title, status and marker retain their layout
    # and visibility, scaled together to fit the more restrained screen size.
    def screen_point(x,y,z):
        return (x+.019,screen_cy+(y-screen_cy)*.92,screen_cz+(z-screen_cz)*.90)
    offroader_text(b,'screen_title','TRAIL  /  NAV',screen_point(.244,-.13,1.432),.0162,m['screen_ink'])
    offroader_text(b,'screen_status','4WD LOW     23 C',screen_point(.244,-.13,1.276),.0108,m['screen_ink'])
    for z in (1.31,1.34,1.37,1.40):
        b.cylinder('map_grid',screen_point(.245,-.278,z),screen_point(.245,.018,z),.0005,m['blue'],vertices=4)['max_lod']=0
    for y in (-.25,-.19,-.13,-.07,-.01):
        b.cylinder('map_grid',screen_point(.245,y,1.30),screen_point(.245,y,1.413),.0005,m['blue'],vertices=4)['max_lod']=0
    b.tube('map_route',[screen_point(*p) for p in ((.240,-.25,1.315),(.240,-.19,1.315),(.240,-.16,1.355),
                       (.240,-.09,1.355),(.240,-.05,1.401),(.240,.0,1.401))],.0018,m['screen_ink'],resolution=1)['max_lod']=0
    b.mesh('map_position_marker',[screen_point(*p) for p in ((.237,-.162,1.348),(.237,-.177,1.329),(.237,-.148,1.329))],[(0,1,2)],m['amber'])['max_lod']=0

    # Flared sockets blend into the dashboard; the openings have a dark cavity
    # behind the louvres instead of a cylinder cap flush with their tips.
    for y in (-.622,-.391,.670):
        scale=.74 if y>.6 else 1.
        rings=[]
        for x,radius in ((.315,.059),(.283,.059),(.265,.056),(.262,.050),(.291,.047)):
            rings.append([(x,y+radius*scale*math.cos(math.tau*i/40),1.365+radius*scale*math.sin(math.tau*i/40)) for i in range(40)])
        offroader3_cockpit_loft(b,'round_vent_recess',rings,m['trim'],closed=True)
        b.cylinder('vent_cavity',(.298,y,1.365),(.300,y,1.365),.048*scale,rubber,vertices=32)
        b.torus('round_vent_bezel',(.263,y,1.365),.054*scale,.0022,alloy,axis='X',major_segments=40,minor_segments=6)
        for dz in (-.032,-.016,0,.016,.032):
            half=math.sqrt(.048**2-dz**2)*scale
            rounded_box('vent_direction_louvre',(.273,y,1.365+dz*scale),(.016,half*2,.0035),m['steel'],.001,2)
        rounded_box('vent_adjuster',(.258,y,1.365),(.015,.018,.008),alloy,.0025,3)

    # Recessed mounting panels, concentric dial collars and one aligned switch
    # rail make the controls read as a manufactured assembly at close range.
    rounded_box('climate_control_panel',(.276,-.13,1.194),(.018,.337,.082),m['trim'],.010)
    for y,label in ((-.248,'TEMP'),(-.13,'FAN'),(-.012,'MODE')):
        b.cylinder('climate_dial_socket',(.263,y,1.197),(.278,y,1.197),.0285,rubber,vertices=32)
        b.torus('climate_dial_collar',(.261,y,1.197),.026,.0015,m['steel'],axis='X',major_segments=32,minor_segments=6)
        b.cylinder('climate_dial',(.253,y,1.197),(.267,y,1.197),.022,alloy,vertices=32)
        b.cylinder('climate_dial_face',(.250,y,1.197),(.253,y,1.197),.0185,plastic,vertices=32)
        rounded_box('climate_pointer',(.248,y,1.209),(.003,.002,.008),legend,.0008,2)['max_lod']=0
        offroader_text(b,'climate_label',label,(.265,y,1.161),.0075,legend)
    rounded_box('dashboard_switch_recess',(.281,-.12,1.108),(.020,.346,.047),rubber,.008)
    for y,label in ((-.25,'A/C'),(-.17,'LOCK'),(-.08,'ESC'),(.01,'HAZ')):
        rounded_box('dashboard_switch',(.269,y,1.109),(.016,.062,.030),m['trim'],.004)
        offroader_text(b,'switch_label',label,(.259,y,1.105),.0075,legend)
        rounded_box('switch_locator',(.259,y,1.117),(.0015,.010,.0015),legend,.0005,2)['max_lod']=0
    rounded_box('passenger_glovebox',(.347,-.49,1.12),(.030,.40,.16),plastic,.019)
    rounded_box('glovebox_latch_recess',(.329,-.49,1.172),(.008,.085,.031),m['trim'],.006)
    rounded_box('glovebox_latch',(.324,-.49,1.176),(.013,.063,.014),alloy,.004)
    for y in (-.65,-.35):
        rounded_box('passenger_handle_mount',(.302,y,1.230),(.023,.042,.043),m['trim'],.010)
    b.tube('passenger_grab_handle',[(.30,-.65,1.23),(.25,-.62,1.23),(.25,-.38,1.23),(.30,-.35,1.23)],.012,m['trim'])

    center=Vector((.115,.42,1.332)); normal=Vector((-.89,0,.455)).normalized()
    u=Vector((0,1,0)); v=normal.cross(u).normalized()
    steering=offroader_control(b,'cabin_steering_pivot',tuple(center),-540,540,'X')
    steering.rotation_mode='QUATERNION'
    steering.rotation_quaternion=Vector((1,0,0)).rotation_difference(normal)
    b.cylinder('steering_column',(.38,.42,1.22),tuple(center-normal*.025),.028,m['steel'],vertices=24)
    for side in (-1,1):
        b.cylinder('column_stalk',(.25,.42+side*.03,1.29),(.22,.42+side*.15,1.31),.007,m['trim'],vertices=12)
        offroader_pad(b,'column_stalk_grip',(.22,.42+side*.15,1.31),(.030,.052,.024),m['trim'])
    rim=b.torus('leather_steering_rim',tuple(center),.158,.019,m['trim'],axis='Z',major_segments=64,minor_segments=12)
    rim.rotation_quaternion=Vector((0,0,1)).rotation_difference(normal)
    b.parent(rim,steering)
    for angle in (0,math.pi,math.pi/2):
        radial=math.cos(angle)*u+math.sin(angle)*v
        tangent=-math.sin(angle)*u+math.cos(angle)*v
        outline=[center+radial*.035+tangent*.026,center+radial*.143+tangent*.017,
                 center+radial*.143-tangent*.017,center+radial*.035-tangent*.026]
        vertices=[tuple(p+normal*d) for d in (-.008,.008) for p in outline]
        spoke=b.mesh('steering_spoke',vertices,[(3,2,1,0),(4,5,6,7),(0,1,5,4),(1,2,6,5),(2,3,7,6),(3,0,4,7)],alloy,bevel=.004)
        b.parent(spoke,steering)
    pad=offroader_pad(b,'steering_airbag',tuple(center+normal*.012),(.063,.17,.125),m['trim'])
    pad.rotation_mode='QUATERNION'; pad.rotation_quaternion=steering.rotation_quaternion.copy()
    b.parent(pad,steering)
    for side in (-1,1):
        for dz in (-.012,.012):
            button=b.box('steering_multifunction_button',tuple(center+u*(side*.104)+v*dz+normal*.019),(.012,.032,.017),m['interior'],bevel=.004)
            button.rotation_mode='QUATERNION'; button.rotation_quaternion=steering.rotation_quaternion.copy()
            b.parent(button,steering)
    b.parent(b.cylinder('steering_emblem',tuple(center+normal*.045),tuple(center+normal*.048),.017,alloy,vertices=24),steering)

    # Vary the cross section along the cabin: a gently rising center tunnel,
    # narrowing forward console, and rounded rear shoulder under the armrest.
    offroader4_console_shell(b,'transmission_tunnel',[
        (-.755,.025,.800,.842),(-.70,.150,.777,.875),(-.43,.225,.777,.901),
        (.02,.270,.777,.911),(.34,.250,.777,.900),(.52,.115,.785,.875),
        (.56,.022,.811,.850)],plastic)
    offroader4_console_shell(b,'center_console',[
        (-.721,.022,.930,1.043),(-.689,.175,.836,1.110),(-.62,.211,.835,1.123),
        (-.40,.225,.835,1.125),(-.27,.227,.838,1.126),(-.08,.232,.845,1.126),
        (.11,.218,.866,1.125),(.185,.151,.906,1.112),(.215,.022,1.008,1.045)],plastic)
    def console_outline(length,width,radius):
        outline=[]
        for lateral,longitudinal in offroader3_cockpit_round_outline(width,length,radius,steps=6):
            x=longitudinal-.10
            lateral*=1-.075*max(0.,(x+.10)/.26)**2
            outline.append((x,lateral))
        return outline
    outer=console_outline(.52,.22,.021)
    inner=console_outline(.502,.204,.016)
    rings=[[(x,y,z) for x,y in outline] for outline,z in
           ((outer,1.128),(outer,1.144),(inner,1.144),(inner,1.131))]
    offroader3_cockpit_loft(b,'console_trim_surround',rings,alloy,closed=True)
    rings=[[(x,y,z) for x,y in inner] for z in (1.131,1.140)]
    offroader3_cockpit_loft(b,'console_rubber_inlay',rings,m['trim'],caps=True)
    # The pad starts behind the cupholders and stays inside the 335 mm seat gap.
    offroader4_soft_pad(b,'center_armrest_lid_seal',(-.535,0,1.124),(.282,.207,.012),m['trim'])
    offroader4_soft_pad(b,'center_armrest',(-.535,0,1.150),(.280,.205,.050),m['leather'])
    for role,x,y,length in (('gear_lever_pivot',.065,-.035,.17),('transfer_lever_pivot',-.13,.055,.105)):
        pivot=offroader_control(b,role,(x,y,1.153),-12,12)
        for i in range(3):
            offroader_pad(b,'shifter_boot_pleat',(x,y,1.15+i*.013),(.11-i*.023,.082-i*.018,.024),rubber)
        b.parent(b.cylinder('gear_lever_shaft',(x,y,1.166),(x-.025,y,1.16+length),.007,alloy,vertices=16),pivot)
        knob=offroader4_soft_pad(b,'shift_knob',(x-.025,y,1.17+length),(.049,.045,.054),m['trim'],headrest=True)
        b.parent(knob,pivot)
        offroader_text(b,'shift_pattern','R 1 3 5\n  2 4 6' if length>.15 else 'H N L',
                       (x-.025,y,1.198+length),.009,m['stitch'],parent=pivot,facing='top')
    for y in (-.055,.055):
        b.cylinder('cupholder_well',(-.29,y,1.140),(-.29,y,1.148),.034,rubber,vertices=24)
        b.torus('cupholder_rim',(-.29,y,1.153),.034,.003,alloy,axis='Z',major_segments=32,minor_segments=6)
    brake=offroader_control(b,'handbrake_pivot',(-.27,.145,1.01),-25,0)
    b.parent(b.cylinder('handbrake_arm',(-.27,.145,1.01),(-.03,.145,1.085),.011,m['steel'],vertices=16),brake)
    b.parent(b.cylinder('handbrake_grip',(-.14,.145,1.051),(-.02,.145,1.088),.018,m['trim'],vertices=24),brake)
    b.parent(b.sphere('handbrake_release',(-.012,.145,1.091),(.010,.013,.013),alloy,segments=16,rings=8),brake)
    for role,y,width,height in (('accelerator_pivot',.28,.040,.110),('brake_pivot',.43,.072,.080),('clutch_pivot',.58,.065,.080)):
        pivot=offroader_control(b,role,(.56,y,1.045),-18,0)
        b.cylinder('pedal_fixed_axle',(.56,y-.025,1.045),(.56,y+.025,1.045),.012,m['steel'],vertices=16)
        b.parent(b.cylinder('pedal_suspended_arm',(.56,y,1.045),(.425,y,.891),.009,m['steel'],vertices=12),pivot)
        b.parent(b.box('pedal_metal_backing',(.419,y,.89),(.017,width+.008,height+.008),alloy,bevel=.006,rotation=(0,-.20,0)),pivot)
        b.parent(b.box('pedal_rubber_pad',(.407,y,.89),(.014,width,height),rubber,bevel=.006,rotation=(0,-.20,0)),pivot)
        for i in range(5):
            z=.89+(i-2)*height*.15
            rib=b.box('pedal_grip_rib',(.398-(z-.89)*.20,y,z),(.003,width*.78,.003),m['trim'],bevel=.001)
            rib['max_lod']=0
            b.parent(rib,pivot)
    b.box('driver_dead_pedal',(.439,.684,.888),(.027,.048,.142),m['interior'],
          bevel=.008,rotation=(0,-.20,0))
    for z in (.845,.870,.895,.920,.945):
        b.box('dead_pedal_grip',(.421-(z-.888)*.20,.684,z),(.004,.036,.004),rubber,bevel=.001)['max_lod']=0


def build_offroader_interior(b, m):
    """Left-hand-drive cabin with contoured seats and a shallow luggage bay.

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
    # Shallow carpeted load bay behind the upright rear bench. All trim stays
    # ahead of the rear aperture; the opening tailgate carries no fixed luggage.
    carpet=b.mat('offroader_cargo_carpet',(.026,.033,.030),roughness=.92)
    b.box('cargo_carpet_load_floor',(-1.755,0,.795),(.31,1.40,.025),carpet,bevel=.009)
    b.box('cargo_removable_floor_panel',(-1.758,0,.809),(.267,1.27,.006),carpet,bevel=.010)
    b.box('cargo_floor_pull_recess',(-1.827,0,.813),(.040,.082,.005),m['trim'],bevel=.005)
    b.box('cargo_floor_flush_pull',(-1.827,0,.816),(.012,.055,.005),plastic,bevel=.003)
    b.box('cargo_loading_sill',(-1.924,0,.794),(.045,1.39,.024),plastic,bevel=.006)
    for j in range(9):
        b.box('cargo_sill_grip',(-1.924,(j-4)*.145,.808),(.028,.086,.0025),rubber,bevel=.001)
    for side in (-1,1):
        b.box('cargo_side_liner',(-1.758,side*.747,1.014),(.295,.030,.409),plastic,bevel=.016)
        b.box('cargo_carpet_side_inset',(-1.753,side*.728,1.018),(.238,.008,.28),carpet,bevel=.014)
        for x in (-1.84,-1.65):
            b.box('cargo_tie_down_recess',(x,side*.601,.815),(.058,.059,.007),m['trim'],bevel=.012)
            b.torus('cargo_folding_tie_down',(x,side*.601,.822),.019,.0032,alloy,
                    axis='Z',major_segments=20,minor_segments=6)
            b.cylinder('cargo_tie_down_hinge',(x-.022,side*.601-.013,.820),
                       (x-.022,side*.601+.013,.820),.0035,steel,vertices=10)
        # A shallow mesh pocket is pressed against each side liner.
        for x in (-1.849,-1.801,-1.753,-1.705,-1.657):
            b.tube('cargo_net_vertical',[(x,side*.713,.934),(x,side*.708,1.013),
                                       (x,side*.713,1.092)],.0015,rubber,resolution=1)['max_lod']=0
        for z in (.934,.974,1.014,1.054,1.092):
            b.tube('cargo_net_horizontal',[(-1.849,side*.713,z),(-1.753,side*.708,z),
                                         (-1.657,side*.713,z)],.0015,rubber,resolution=1)['max_lod']=0
        b.tube('cargo_pocket_top_cord',[(-1.856,side*.712,1.097),(-1.753,side*.708,1.088),
                                     (-1.650,side*.712,1.097)],.003,rubber,resolution=1)
        for x in (-1.856,-1.650):
            b.box('cargo_pocket_anchor',(x,side*.722,1.097),(.016,.013,.024),plastic,bevel=.004)
        b.box('cargo_lamp_housing',(-1.75,side*.725,1.186),(.075,.012,.035),m['trim'],bevel=.006)
        b.box('cargo_lamp_lens',(-1.75,side*.716,1.186),(.058,.005,.021),m['roof'],bevel=.004)

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

    # A single sewn shell forms each cushion/backrest, including its bolsters.
    # Flush cloth regions carry the refined normal map on their authored UVs.
    for side, label in ((1,'driver'),(-1,'passenger')):
        y=side*.42
        for rail_y in (y-.16,y+.16):
            b.box(label+'_seat_rail',(-.18,rail_y,.82),(.46,.032,.035),steel,bevel=.007)
            b.box(label+'_seat_pedestal',(-.20,rail_y,.90),(.25,.045,.14),plastic,bevel=.018)
        b.box(label+'_seat_undertray',(-.18,y,.954),(.425,.421,.053),plastic,bevel=.018)
        _offroader3_upholstered_shell(b,label+'_upholstered_seat_cushion',(-.15,y,1.018),
                                     (.525,.465,.124),m['leather'],m['seat_insert'])
        _offroader3_upholstered_shell(b,label+'_upholstered_seat_back',(-.427,y,1.355),
                                     (.139,.465,.602),m['leather'],m['seat_insert'],
                                     back=True,rotation=(0,-.14,0))
        for offset in (-.09,.09):
            b.cylinder(label+'_headrest_post',(-.472,y+offset,1.619),(-.482,y+offset,1.715),.007,alloy,vertices=12)
            b.box(label+'_headrest_post_guide',(-.472,y+offset,1.649),(.030,.028,.012),plastic,bevel=.005)
        offroader4_soft_pad(b,label+'_headrest',(-.480,y,1.733),(.134,.275,.172),m['leather'],headrest=True,rotation=(0,-.11,0))
        b.cylinder(label+'_seat_recline_hinge',(-.375,side*.638,1.008),(-.375,side*.664,1.008),.045,plastic,vertices=24)
        b.cylinder(label+'_seat_adjustment',(-.22,side*.658,.967),(-.22,side*.679,.967),.029,plastic,vertices=24)
        b.tube(label+'_seat_slide_handle',[(-.015,y-.13,.921),(.055,y-.13,.935),
                                        (.055,y+.13,.935),(-.015,y+.13,.921)],.006,steel,resolution=1)
        # Retracted unoccupied belts hang at the B pillar, outside the seats.
        ribbon(label+'_shoulder_belt',[(-.47,side*.744,1.79),(-.43,side*.735,1.50),
                                      (-.30,side*.67,1.08),(-.30,side*.66,.83)],.040,rubber)
        b.box(label+'_upper_belt_anchor',(-.47,side*.748,1.79),(.078,.025,.055),plastic,bevel=.012)
        b.box(label+'_belt_retractor',(-.30,side*.685,.89),(.11,.075,.16),plastic,bevel=.018)
        b.cylinder(label+'_buckle_stalk',(-.22,side*.14,.91),(-.16,side*.14,1.10),.009,steel,vertices=10)
        b.box(label+'_belt_buckle',(-.16,side*.14,1.12),(.045,.035,.065),plastic,bevel=.010)
        b.box(label+'_buckle_release',(-.143,side*.14,1.14),(.014,.025,.022),m['red'],bevel=.004)

    # One continuous three-position bench, with shallow individual hip/lumbar
    # contours and cloth panels sharing the same leather cover topology.
    b.box('rear_bench_base',(-1.22,0,.90),(.43,1.35,.17),plastic,bevel=.035)
    _offroader3_upholstered_shell(b,'rear_upholstered_seat_cushion',(-1.18,0,1.026),
                                 (.495,1.35,.121),m['leather'],m['seat_insert'],bench=True)
    _offroader3_upholstered_shell(b,'rear_upholstered_seat_back',(-1.463,0,1.346),
                                 (.128,1.35,.590),m['leather'],m['seat_insert'],
                                 back=True,bench=True,rotation=(0,-.10,0))
    for index,y in enumerate((-.45,0,.45)):
        prefix='rear_seat_'+str(index)
        for offset in (-.08,.08):
            b.cylinder(prefix+'_headrest_post',(-1.52,y+offset,1.609),(-1.53,y+offset,1.708),.007,alloy,vertices=10)
            b.box(prefix+'_headrest_post_guide',(-1.522,y+offset,1.633),(.027,.024,.011),plastic,bevel=.004)
        offroader4_soft_pad(b,prefix+'_headrest',(-1.535,y,1.716),(.120,.260,.163),m['leather'],headrest=True,rotation=(0,-.08,0))
        b.box(prefix+'_buckle',(-1.33,y+.158,1.093),(.043,.029,.048),plastic,bevel=.008)
        b.box(prefix+'_buckle_release',(-1.307,y+.158,1.108),(.009,.020,.018),m['red'],bevel=.003)
    for side in (-1,1):
        ribbon('rear_shoulder_belt',[(-1.57,side*.736,1.78),(-1.50,side*.724,1.42),
                                    (-1.23,side*.70,1.07),(-1.30,side*.70,.86)],.038,rubber)
    ribbon('rear_center_belt',[(-1.548,.10,1.64),(-1.447,.10,1.608),
                               (-1.431,.10,1.46),(-1.394,-.08,1.12)],.037,rubber)

    build_offroader_cockpit(b, m)

    # Rear cards clear the wheel-arch cutout. Everything created within one
    # loop receives the door role, including tiny switches and grille bars.
    for side in (-1,1):
        for x in (-.04,-1.11):
            moving_start=len(b.objects)
            rear=x<-.5
            role=('rear_' if rear else 'front_')+('left_' if side>0 else 'right_')+'door_hinge_pivot'
            zc=1.202 if rear else 1.12
            height=.284 if rear else .37
            outline=offroader_rounded_outline([
                (x-.345,zc-height*.43),(x-.30,zc-height*.50),
                (x+.25,zc-height*.50),(x+.345,zc-height*.25),
                (x+.345,zc+height*.33),(x+.27,zc+height*.50),
                (x-.28,zc+height*.50),(x-.345,zc+height*.31)],.022)
            rings=[]
            for inset,depth in ((.007,.014),(0.,.006),(.004,-.008),(.012,-.017)):
                border=offroader_inset_outline(outline,inset)
                rings.append([(px,side*(.785+depth),pz) for px,pz in border])
            offroader4_closed_rings(b,'inner_door_card',rings,plastic)
            offroader4_soft_pad(b,'door_armrest',(x-.04,side*.753,1.24),(.40,.080,.064),m['leather'])
            b.box('door_pull_recess',(x+.17,side*.758,1.315),(.11,.027,.050),m['trim'],bevel=.012)
            b.box('interior_door_handle',(x+.17,side*.739,1.315),(.075,.015,.017),alloy,bevel=.006)
            b.box('door_storage_pocket',(x-.02,side*.752,1.105 if rear else .965),
                  (.45,.070,.080 if rear else .12),plastic,bevel=.018 if rear else .024)
            offroader4_soft_pad(b,'door_padded_insert',(x-.01,side*.761,1.173 if rear else 1.135),
                          (.50,.027,.13 if rear else .18),m['leather'])
            b.tube('door_stitch',[(x-.23,side*.744,1.202),(x,side*.744,1.206),(x+.20,side*.744,1.202)],.0013,m['stitch'])['max_lod']=1
            b.box('window_switch_mount',(x+.07,side*.733,1.281),(.13,.049,.016),m['trim'],bevel=.005)
            for sx in (x+.04,x+.095):
                b.box('window_rocker_switch',(sx,side*.731,1.293),(.032,.028,.010),alloy,bevel=.003)
            speaker_x=x+.23 if rear else x+.20
            speaker_z=1.129 if rear else 1.04
            speaker_r=.052 if rear else .066
            b.cylinder('door_speaker_recess',(speaker_x,side*.758,speaker_z),(speaker_x,side*.739,speaker_z),speaker_r,m['trim'],vertices=32)
            b.torus('door_speaker_trim',(speaker_x,side*.735,speaker_z),speaker_r-.002,.003,alloy,axis='Y',major_segments=40,minor_segments=6)
            for i in range(-4,5):
                dz=i*(.0085 if rear else .011)
                half=math.sqrt((speaker_r-.010)**2-dz**2)
                b.cylinder('speaker_grille',(speaker_x-half,side*.731,speaker_z+dz),
                           (speaker_x+half,side*.731,speaker_z+dz),.0015,m['steel'],vertices=4)['max_lod']=0
            for obj in b.objects[moving_start:]:
                obj['access_role']=role
    b.box('roof_console',(-.12,0,1.975),(.25,.17,.033),plastic,bevel=.015)
    b.box('cabin_dome_light',(-.12,0,1.953),(.13,.10,.008),m['roof'],bevel=.010)
    for side in (-1,1):
        b.box('sun_visor',(.27,side*.39,1.947),(.12,.40,.030),fabric,bevel=.012,rotation=(0,-.07,0))
        b.box('headliner_side',(-.80,side*.66,2.013),(2.25,.15,.020),plastic,bevel=.009)
        b.tube('ceiling_grab_handle',[(-.18,side*.70,1.97),(-.18,side*.67,1.91),
                    (-.41,side*.67,1.91),(-.41,side*.70,1.97)],.012,plastic)
    b.cylinder('rearview_mirror_stalk',(.39,0,1.91),(.27,0,1.84),.010,steel,vertices=16)
    b.box('rearview_mirror_housing',(.26,0,1.839),(.045,.25,.09),plastic,bevel=.020)
    b.box('rearview_mirror_glass',(.234,0,1.839),(.005,.218,.064),m['chrome'],bevel=.010)

    return {
        'seating': 'Two anatomically contoured front seats and a continuous three-place rear bench; flush cloth panels, integrated bolsters, headrests and fixed retracted belts.',
        'cargo': 'Shallow carpeted luggage bay with flush floor pull, folding tie-down rings, side net pockets and a protected loading sill.',
        'controls': 'Articulated wheel, manual gear/transfer selectors, handbrake and three pedals; readable static instruments and original navigation graphic.',
        'limitations': 'No occupants, interactive display, control simulation or animation clips; boot deformation and first-person integration remain engine tasks.'
    }


def build_motorboat(b):
    """Original open sportboat; nominal waterline Z=0, bow along +X."""
    b.root['source_waterline_z_m'] = 0.
    b.root['waterline_reference'] = 'Nominal design reference, not a computed loaded waterline'
    m = {
        'hull': b.mat('boat_navy_gelcoat', (.022,.055,.092), metallic=.10, roughness=.24),
        'deck': b.mat('boat_ivory_gelcoat', (.80,.83,.80), metallic=.07, roughness=.29),
        'accent': b.mat('boat_orange_belt', (.92,.155,.020), metallic=.12, roughness=.30),
        'trim': b.mat('boat_graphite_trim', (.017,.024,.028), roughness=.44),
        'rubber': b.mat('boat_rubber', (.012,.018,.021), roughness=.77),
        'alloy': b.mat('boat_satin_alloy', (.46,.52,.55), metallic=.91, roughness=.28),
        'steel': b.mat('boat_dark_steel', (.065,.085,.10), metallic=.86, roughness=.32),
        'chrome': b.mat('boat_polished_fittings', (.68,.74,.78), metallic=1., roughness=.16),
        'glass': b.mat('boat_clear_windscreen', (.84,.94,.97), roughness=.030, transmission=1.),
        'upholstery': b.mat('boat_marine_vinyl', (.73,.76,.72), roughness=.56),
        'piping': b.mat('boat_seat_piping', (.11,.17,.19), roughness=.58),
        'teak': b.mat('boat_deck_planks', (.26,.135,.060), roughness=.61),
        'display': b.mat('boat_helm_display', (.008,.028,.043), roughness=.23),
        'red': b.mat('boat_port_light', (.65,.010,.006), roughness=.2, emission=(.8,.008,.004)),
        'green': b.mat('boat_starboard_light', (.008,.42,.12), roughness=.2, emission=(.008,.7,.12)),
        'lamp': b.mat('boat_navigation_white', (.75,.84,.9), roughness=.17, emission=(.60,.72,.85)),
    }
    hull = build_motorboat_hull(b, m)
    equipment = build_motorboat_equipment(b, m)
    # Broad hull sections approximate the taper. They are not a buoyancy volume.
    b.collision_box('aft_hull', (-1.75,0,.23), (1.95,2.08,1.0))
    b.collision_box('mid_hull', (-.10,0,.22), (1.35,2.10,1.04))
    b.collision_box('forward_hull', (1.13,0,.30), (1.12,1.70,.96))
    b.collision_box('bow', (2.17,0,.43), (1.0,.97,.85))
    b.collision_box('bow_tip', (2.87,0,.65), (.54,.34,.57))
    b.collision_box('outboard_cowling', (-3.04,0,.70), (.62,.62,.95))
    b.collision_box('outboard_leg', (-3.08,0,-.20), (.32,.20,1.12))
    b.collision_box('propeller_envelope', (-3.25,0,-.44), (.24,.45,.45))
    return {
        'description': 'Original open sport motorboat with a deep-V hull, cockpit, bow seating, transmitting windscreen and articulated outboard.',
        'dimensions_design_m': {'hull_length': 5.95, 'hull_beam': 2.30, 'nominal_source_waterline_z': 0.},
        'moving_parts': ['outboard_trim_pivot','outboard_steering_pivot','boat_propeller_pivot','boat_steering_wheel_pivot'],
        'pivot_notes': 'Outboard trim: local Y. Steering: local Z, child of trim. Propeller: local X, child of outboard steering. Helm wheel: its aligned local X, independent of the engine.',
        'hull': hull, 'equipment': equipment,
        'notes': ['Z=0 is the nominal Blender waterline; below-water geometry is included in exports. It becomes glTF Y=0 after axis conversion.',
                  'The rendered water surface belongs only to the studio collection and is excluded from GLB exports.',
                  'No buoyancy, hydrodynamics, powered propulsion, steering coupling or animated cables are implemented.',
                  'Eight static collision boxes need engine assignment and do not follow outboard motion.',
                  'Transparent windscreen requires KHR_materials_transmission support in the target importer.'],
    }

def build_motorboat_hull(b, m):
    """Closed deep-V hull shell around an open cockpit; waterline is Z=0."""
    # x, beam/2, sheer, keel and nominal interior floor height.
    stations = [
        (-2.80,1.00,.850,-.380,.20), (-2.50,1.08,.860,-.420,.20),
        (-2.00,1.135,.875,-.430,.20),(-1.50,1.15,.890,-.430,.20),
        (-1.00,1.15,.900,-.430,.20),(-.50,1.14,.910,-.420,.20),
        (0.,1.12,.920,-.390,.20),(.50,1.08,.930,-.340,.20),
        (.55,1.075,.933,-.333,.20),(.70,1.055,.939,-.307,.43),
        (1.00,1.00,.950,-.260,.43),(1.50,.86,.970,-.170,.43),
        (2.00,.66,.990,-.040,.43),(2.35,.46,1.010,.100,.43),
        (2.65,.28,1.020,.350,.43),(2.90,.13,1.025,.680,.43),
        (3.08,.045,1.030,.910,.43),(3.15,.008,1.035,1.005,.43),
    ]

    # Monotone Hermite interpolation preserves the station dimensions while
    # removing visible longitudinal facets from the bow and sheer silhouette.
    derivatives=[]
    for column in range(1,5):
        h=[stations[i+1][0]-stations[i][0] for i in range(len(stations)-1)]
        slopes=[(stations[i+1][column]-stations[i][column])/h[i] for i in range(len(h))]
        tangent=[slopes[0]]
        for i in range(1,len(stations)-1):
            if slopes[i-1]*slopes[i] <= 0:
                tangent.append(0.)
            else:
                w1,w2=2*h[i]+h[i-1],h[i]+2*h[i-1]
                tangent.append((w1+w2)/(w1/slopes[i-1]+w2/slopes[i]))
        tangent.append(slopes[-1])
        derivatives.append(tangent)

    def section(x):
        i=next((j for j in range(len(stations)-1) if x <= stations[j+1][0]),len(stations)-2)
        left,right=stations[i],stations[i+1]
        h=right[0]-left[0]
        t=max(0.,min(1.,(x-left[0])/h))
        result=[x]
        for column in range(1,5):
            result.append((2*t**3-3*t*t+1)*left[column]+(t**3-2*t*t+t)*h*derivatives[column-1][i]
                          +(-2*t**3+3*t*t)*right[column]+(t**3-t*t)*h*derivatives[column-1][i+1])
        return result

    # The outside starts at port gunwale, runs down the V and up starboard.
    # The return path is the inner liner and sole, joined along both top rims.
    half=[(1.,1.),(.998,.965),(.995,.910),(.991,.875),(.986,.845),
          (.975,.740),(.960,.570),(.920,.360),(.860,.230),(.680,.150),
          (.400,.075),(.130,.015),(0.,0.)]
    outer=[(-r,t) for r,t in half]+[(r,t) for r,t in reversed(half[:-1])]

    def ring(data):
        x,width,sheer,keel,base_floor=data
        height=sheer-keel
        floor=min(max(base_floor,keel+min(.06,height*.35)),sheer-min(.12,height*.55))
        inner=width-min(.08,width*.10)
        floor_y=min(.91,width*.78)
        top=sheer-min(.03,height*.10)
        shoulder=sheer-min(.12,height*.20)
        lower=floor+min(.10,(shoulder-floor)*.25)
        offset=min(.06,width*.05)
        vertices=[(x,width*r,keel+height*t) for r,t in outer]
        vertices += [(x,inner,top),(x,inner*.98,shoulder),(x,floor_y+offset,lower),
                     (x,floor_y,floor),(x,-floor_y,floor),(x,-floor_y-offset,lower),
                     (x,-inner*.98,shoulder),(x,-inner,top)]
        return vertices

    xs=[]
    for left,right in zip(stations,stations[1:]):
        xs.extend(left[0]+(right[0]-left[0])*i/4 for i in range(4))
    xs.append(stations[-1][0])
    rings=[ring(section(x)) for x in xs]
    count=len(rings[0])
    vertices=[v for cross in rings for v in cross]
    faces=[(i*count+j,i*count+(j+1)%count,(i+1)*count+(j+1)%count,(i+1)*count+j)
           for i in range(len(rings)-1) for j in range(count)]
    faces += [tuple(reversed(range(count))),tuple((len(rings)-1)*count+j for j in range(count))]
    shell=b.mesh('deep_v_hull_shell',vertices,faces,m['hull'],smooth=True)
    shell.data.materials.append(m['deck'])
    shell.data.materials.append(m['accent'])
    for i in range(len(rings)-1):
        for j in range(count):
            polygon=shell.data.polygons[i*count+j]
            if j >= len(outer)-1:
                polygon.material_index=1
            else:
                middle=(outer[j][1]+outer[j+1][1])*.5
                polygon.material_index=1 if middle>=.875 else 2 if middle>=.845 else 0
    for polygon in shell.data.polygons[-2:]:
        polygon.use_smooth=False
    shell['construction']='Closed outer skin, inner liner and cockpit sole joined at gunwales; no cap across cockpit opening.'
    shell['waterline_z']=0.

    def slab(name, outline, offset, material, bevel=0):
        n=len(outline); delta=Vector(offset)*.5
        vv=[tuple(Vector(v)-delta) for v in outline]+[tuple(Vector(v)+delta) for v in outline]
        ff=[tuple(reversed(range(n))),tuple(range(n,2*n))]
        ff += [(j,(j+1)%n,(j+1)%n+n,j+n) for j in range(n)]
        return b.mesh(name,vv,ff,material,bevel=bevel)

    def straight_trim(name, points, radius, material):
        obj=b.tube(name,points,radius,material,resolution=1)
        for spline in obj.data.splines:
            for point in spline.bezier_points:
                point.handle_left_type=point.handle_right_type='VECTOR'
        return obj

    # Narrow external rub rails follow the actual loft, including the rising bow.
    for side,label in ((1,'port'),(-1,'starboard')):
        rail=[]; metal=[]
        for x in xs:
            _,width,sheer,keel,_=section(x)
            rail.append((x,side*(width+.005),sheer-.012))
            metal.append((x,side*(width+.012),sheer-.033))
        b.tube(label+'_rub_rail',rail,.016,m['rubber'],resolution=2)
        b.tube(label+'_rub_rail_insert',metal,.005,m['alloy'],resolution=1)
        # Long triangular spray strakes stand just proud of the V bottom.
        for row,(fraction,vertical) in enumerate(((.52,.107),(.79,.199))):
            vv=[]
            samples=[-2.68+4.90*j/30 for j in range(31)]
            for x in samples:
                _,width,sheer,keel,_=section(x)
                y=side*fraction*width; z=keel+(sheer-keel)*vertical
                vv.extend([(x,y-side*.012,z-.003),(x,y+side*.012,z+.005),(x,y,z-.022)])
            ff=[(i*3+j,i*3+(j+1)%3,(i+1)*3+(j+1)%3,(i+1)*3+j)
                for i in range(len(samples)-1) for j in range(3)]
            ff += [(2,1,0),tuple((len(samples)-1)*3+j for j in range(3))]
            b.mesh(label+'_spray_strake_'+str(row),vv,ff,m['hull'],smooth=True)

    # The aft bulkhead closes the otherwise open liner, with a central motor
    # notch above waterline and a shallow self-draining splashwell inside.
    transom=[(-2.765,y,z) for y,z in [(-.98,.20),(.98,.20),(.98,.83),(.44,.83),
                                     (.44,.56),(-.44,.56),(-.44,.83),(-.98,.83)]]
    slab('transom_bulkhead',transom,(.055,0,0),m['deck'],bevel=.008)
    b.box('splashwell_floor',(-2.55,0,.39),(.44,.90,.025),m['deck'],bevel=.015)
    b.box('splashwell_front_wall',(-2.32,0,.52),(.05,.95,.27),m['deck'],bevel=.015)
    for side in (-1,1):
        b.box('splashwell_side',(-2.55,side*.46,.49),(.44,.025,.18),m['deck'],bevel=.010)
        b.cylinder('splashwell_drain',(-2.57,side*.24,.405),(-2.57,side*.24,.413),.024,m['steel'],vertices=24)
        for i in range(3):
            y=side*.24+(i-1)*.011
            b.box('drain_slot',(-2.57,y,.418),(.025,.004,.003),m['trim'],bevel=.001)

    # A practical non-slip deck; the floor under the seats remains truly open.
    b.box('cockpit_teak_inlay',(-.99,0,.210),(2.78,1.54,.018),m['teak'],bevel=.025)
    for j in range(-7,8):
        b.box('cockpit_deck_caulk',(-.99,j*.102,.221),(2.71,.004,.003),m['trim'],bevel=.001)
    for x,label in ((-.68,'service'),(-1.73,'storage')):
        b.box(label+'_floor_hatch_seal',(x,0,.228),(.63,.48,.012),m['trim'],bevel=.027)
        b.box(label+'_floor_hatch',(x,0,.237),(.60,.45,.011),m['teak'],bevel=.022)
        b.cylinder(label+'_hatch_pull',(x-.20,0,.244),(x-.20,0,.251),.034,m['alloy'],vertices=28)
        b.box(label+'_hatch_pull_slot',(x-.20,0,.254),(.040,.007,.003),m['trim'],bevel=.002)
    # Forward deck inset narrows with the bow rather than using a rectangular box.
    bow_outline=[(.75,-.76,.439),(.75,.76,.439),(1.32,.66,.439),(1.90,.43,.439),
                 (2.34,.21,.439),(2.42,0,.439),(2.34,-.21,.439),(1.90,-.43,.439),(1.32,-.66,.439)]
    slab('bow_deck_inlay',bow_outline,(0,0,.014),m['teak'],bevel=.010)
    for side in (-1,1):
        for offset in (.10,.20,.30):
            straight_trim('bow_deck_caulk',[(.79,side*offset,.449),(1.45,side*offset,.449),
                                           (2.20-offset*.9,side*offset,.449)],.002,m['trim'])

    # A closed forepeak cap provides storage and an anchoring surface ahead of
    # the bow seating; the cockpit and forward seating bay stay uncovered.
    cap_x=[2.40,2.55,2.70,2.85,3.00,3.10,3.15]
    vv=[]
    for x in cap_x:
        _,width,sheer,_,_=section(x)
        z=sheer-.018
        vv.extend([(x,-width*.92,z),(x,width*.92,z),(x,width*.92,z-.035),(x,-width*.92,z-.035)])
    ff=[(i*4+j,i*4+(j+1)%4,(i+1)*4+(j+1)%4,(i+1)*4+j)
        for i in range(len(cap_x)-1) for j in range(4)]
    ff += [(3,2,1,0),tuple((len(cap_x)-1)*4+j for j in range(4))]
    b.mesh('forepeak_deck',vv,ff,m['deck'],smooth=True)
    cap_height=section(2.54)[2]-.018
    b.box('anchor_locker_seal',(2.54,0,cap_height+.004),(.25,.28,.008),m['trim'],bevel=.026)
    b.box('anchor_locker_hatch',(2.54,0,cap_height+.010),(.23,.26,.008),m['deck'],bevel=.022)
    b.cylinder('anchor_locker_latch',(2.47,0,cap_height+.014),(2.47,0,cap_height+.022),.021,m['alloy'],vertices=24)

    # Two independent swim platforms flank the outboard mounting gap.
    for side,label in ((1,'port'),(-1,'starboard')):
        outline=[(-3.13,side*.50,.385),(-3.13,side*1.04,.385),(-2.78,side*1.07,.385),
                 (-2.70,side*.79,.385),(-2.80,side*.50,.385)]
        slab(label+'_swim_platform',outline,(0,0,.055),m['deck'],bevel=.020)
        b.box(label+'_platform_teak',(-2.94,side*.79,.418),(.27,.40,.010),m['teak'],bevel=.021)
        for j in (-1,0,1):
            b.box(label+'_platform_caulk',(-2.94,side*.79+j*.10,.425),(.23,.004,.003),m['trim'],bevel=.001)
        b.cylinder(label+'_platform_support',(-2.82,side*.79,.22),(-3.04,side*.79,.35),.023,m['alloy'],vertices=16)

    return {'hull':'Closed outer skin, inner liner and sole around open cockpit; deep-V keel, chines and spray strakes.',
            'waterline_z':0.,'nominal_dimensions_m':{'hull_length':5.95,'beam':2.30},
            'notes':['Sealed modular meshes are intended for visual use, not a hydrostatic solver.',
                     'Keel is below Z=0; preserve this waterline convention when importing.',
                     'Swim platforms leave the central outboard gap open.']}


def build_motorboat_equipment(b, m):
    """Cockpit furniture, glazing and an independently trimmed/steered outboard."""

    def straight_trim(name, points, radius, material):
        obj = b.tube(name, points, radius, material)
        for spline in obj.data.splines:
            for point in spline.bezier_points:
                point.handle_left_type = point.handle_right_type = 'VECTOR'
        return obj

    def panel(name, outline, offset, material, bevel=0):
        delta = Vector(offset)*.5
        n = len(outline)
        vertices = [tuple(Vector(p)-delta) for p in outline]
        vertices += [tuple(Vector(p)+delta) for p in outline]
        faces = [tuple(reversed(range(n))), tuple(range(n,2*n))]
        faces += [(i,(i+1)%n,(i+1)%n+n,i+n) for i in range(n)]
        return b.mesh(name, vertices, faces, material, bevel=bevel)

    # Two bolster seats leave a usable centre walkway to the open bow.
    for side, label in ((1,'port'),(-1,'helm')):
        y=side*.52
        b.cylinder(label+'_seat_pedestal',(-.35,y,.21),(-.35,y,.47),.055,m['alloy'],vertices=24)
        b.cylinder(label+'_pedestal_foot',(-.35,y,.205),(-.35,y,.225),.11,m['alloy'],vertices=32)
        b.box(label+'_seat_shell',(-.37,y,.52),(.56,.55,.11),m['deck'],bevel=.05)
        b.box(label+'_seat_cushion',(-.32,y,.58),(.51,.49,.13),m['upholstery'],bevel=.045)
        b.box(label+'_seat_insert',(-.30,y,.651),(.37,.32,.015),m['upholstery'],bevel=.015)
        b.box(label+'_seat_back_shell',(-.64,y,.79),(.15,.55,.44),m['deck'],bevel=.055,rotation=(0,-.12,0))
        b.box(label+'_seat_back_pad',(-.552,y,.79),(.055,.47,.36),m['upholstery'],bevel=.025,rotation=(0,-.12,0))
        for dy in (-.225,.225):
            b.box(label+'_side_bolster',(-.33,y+dy,.665),(.43,.066,.12),m['upholstery'],bevel=.028)
            b.tube(label+'_cushion_piping',[(-.50,y+dy,.674),(-.30,y+dy,.68),(-.10,y+dy,.674)],.003,m['piping'],resolution=1)
        for dy in (-.14,.14):
            b.tube(label+'_back_stitch',[(-.505,y+dy,.68),(-.519,y+dy,.80),(-.534,y+dy,.93)],.002,m['piping'],resolution=1)
        b.box(label+'_seat_adjuster',(-.18,y-side*.28,.49),(.12,.028,.032),m['trim'],bevel=.008)

    # Rear lounge is segmented into three cushions, above enclosed stowage.
    b.box('aft_bench_base',(-1.93,0,.39),(.58,1.67,.36),m['deck'],bevel=.04)
    for index,y in enumerate((-.56,0,.56)):
        name='aft_lounge_'+str(index)
        b.box(name+'_cushion',(-1.91,y,.60),(.55,.54,.13),m['upholstery'],bevel=.042)
        b.box(name+'_back_shell',(-2.22,y,.78),(.16,.56,.38),m['deck'],bevel=.035)
        b.box(name+'_back_cushion',(-2.125,y,.78),(.06,.51,.31),m['upholstery'],bevel=.025)
        straight_trim(name+'_cushion_piping',[(-2.14,y-.21,.672),(-1.69,y-.21,.672),
                       (-1.69,y+.21,.672),(-2.14,y+.21,.672)],.0025,m['piping'])
        b.box(name+'_storage_door',(-1.627,y,.40),(.018,.48,.23),m['deck'],bevel=.015)
        b.box(name+'_storage_latch',(-1.612,y,.43),(.015,.075,.025),m['alloy'],bevel=.008)

    # Tapered bow pads follow the hull's narrowing inner footprint.
    for side,label in ((1,'port'),(-1,'starboard')):
        shape=[(.90,side*.34),(1.30,side*.31),(1.70,side*.25),(1.94,side*.22),
               (1.94,side*.46),(1.70,side*.57),(1.30,side*.72),(.90,side*.81)]
        panel(label+'_bow_lounge',[(x,y,.59) for x,y in shape],(0,0,.12),m['upholstery'],bevel=.025)
        straight_trim(label+'_bow_pad_piping',[(x,y,.655) for x,y in shape+[shape[0]]],.0025,m['piping'])
        # Back bolsters sit just inboard of the hull liner.
        b.tube(label+'_bow_back_bolster',[(.95,side*.82,.73),(1.30,side*.73,.74),
               (1.65,side*.60,.77),(1.95,side*.46,.80)],.057,m['upholstery'],resolution=3)
    fore_pad=[(1.96,-.43),(2.15,-.33),(2.30,-.20),(2.32,0),(2.30,.20),(2.15,.33),(1.96,.43)]
    panel('bow_forward_cushion',[(x,y,.59) for x,y in fore_pad],(0,0,.12),m['upholstery'],bevel=.024)

    # Dual consoles leave an open centre passage. Helm is on starboard.
    for side,label in ((1,'passenger'),(-1,'helm')):
        b.box(label+'_console_base',(.57,side*.57,.56),(.30,.66,.60),m['deck'],bevel=.045)
        b.box(label+'_console_top',(.56,side*.57,.885),(.32,.70,.10),m['trim'],bevel=.035)
        b.box(label+'_console_face',(.389,side*.57,.79),(.027,.62,.23),m['trim'],bevel=.02)
    b.box('passenger_glovebox',(.366,.55,.79),(.016,.45,.17),m['deck'],bevel=.02)
    b.box('glovebox_latch',(.352,.55,.83),(.015,.075,.026),m['alloy'],bevel=.007)
    b.tube('passenger_grab_handle',[(.32,.31,.86),(.265,.31,.86),(.265,.70,.86),(.32,.70,.86)],.013,m['alloy'])
    for y in (-.72,-.47):
        b.cylinder('helm_gauge_face',(.368,y,.865),(.377,y,.865),.058,m['display'],vertices=32)
        b.torus('helm_gauge_bezel',(.362,y,.865),.058,.004,m['chrome'],axis='X',major_segments=32,minor_segments=8)
        for i in range(9):
            a=math.radians(-135+i*33.75)
            p=(.354,y+.044*math.sin(a),.865+.044*math.cos(a))
            b.sphere('gauge_tick',p,(.0018,.003,.003),m['piping'],segments=6,rings=4)
        b.cylinder('gauge_needle',(.351,y,.865),(.351,y+.025,.887),.002,m['accent'],vertices=6)
    b.box('navigation_screen_frame',(.366,-.87,.79),(.023,.11,.14),m['alloy'],bevel=.009)
    b.box('navigation_screen',(.351,-.87,.79),(.009,.085,.115),m['display'],bevel=.006)
    for y in (-.71,-.61,-.51,-.41):
        b.box('helm_toggle_switch',(.357,y,.704),(.023,.024,.037),m['rubber'],bevel=.006)
        b.sphere('switch_indicator',(.343,y,.724),(.004,.005,.005),m['lamp'],segments=8,rings=4)

    # Wheel's local X follows the inclined column, while it remains independent
    # of the outboard steering pivot (no simulation or automatic linkage).
    wheel_center=Vector((.27,-.53,.86))
    column_base=Vector((.51,-.53,.79))
    wheel_axis=(wheel_center-column_base).normalized()
    wheel_pivot=b.empty('boat_steering_wheel_pivot',tuple(wheel_center))
    wheel_pivot.rotation_mode='QUATERNION'
    wheel_pivot.rotation_quaternion=Vector((1,0,0)).rotation_difference(wheel_axis)
    b.cylinder('helm_steering_column',tuple(column_base),tuple(wheel_center),.025,m['steel'],vertices=24)
    wheel=b.torus('helm_wheel_rim',tuple(wheel_center),.165,.012,m['rubber'],axis='X',major_segments=64,minor_segments=12)
    wheel.rotation_quaternion=Vector((0,0,1)).rotation_difference(wheel_axis)
    b.parent(wheel,wheel_pivot)
    b.parent(b.cylinder('helm_wheel_hub',tuple(wheel_center-wheel_axis*.025),tuple(wheel_center+wheel_axis*.025),.038,m['alloy'],vertices=24),wheel_pivot)
    u=Vector((0,1,0)); v=wheel_axis.cross(u).normalized()
    for i in range(3):
        a=math.tau*i/3+.35
        end=wheel_center+.151*(math.cos(a)*u+math.sin(a)*v)
        b.parent(b.cylinder('helm_wheel_spoke',tuple(wheel_center),tuple(end),.008,m['chrome'],vertices=12),wheel_pivot)
    b.box('throttle_quadrant',(-.03,-.864,.70),(.18,.065,.24),m['trim'],bevel=.026)
    b.cylinder('throttle_pivot',(-.03,-.88,.74),(-.03,-.81,.74),.024,m['alloy'],vertices=20)
    b.cylinder('throttle_lever',(-.03,-.804,.74),(.03,-.804,.91),.012,m['chrome'],vertices=16)
    b.sphere('throttle_grip',(.03,-.804,.91),(.035,.025,.032),m['rubber'],segments=20,rings=10)
    b.tube('engine_stop_lanyard',[(.28,-.79,.70),(.15,-.78,.61),(.01,-.76,.67)],.003,m['red'],resolution=1)

    # Split closed glazing panes retain the open central walkthrough.
    for side,label in ((1,'port'),(-1,'starboard')):
        front=[(.65,side*.16,.78),(.65,side*.98,.78),(.35,side*.90,1.40),(.35,side*.16,1.40)]
        wing=[(.65,side*.98,.78),(-.45,side*1.00,.80),(-.35,side*.96,1.02),(.35,side*.90,1.40)]
        for kind,outline,offset in (('front',front,(.006,0,0)),('wing',wing,(0,.006,0))):
            panel(label+'_'+kind+'_windscreen',outline,offset,m['glass'])
            straight_trim(label+'_'+kind+'_window_frame',outline+[outline[0]],.013,m['alloy'])
        for x,y,z in ((.65,side*.96,.78),(-.44,side*.99,.80)):
            b.box(label+'_windscreen_mount',(x,y,z-.022),(.08,.052,.04),m['alloy'],bevel=.01)
        b.cylinder(label+'_screen_support',(.15,side*.92,.77),(-.26,side*.94,1.01),.010,m['chrome'],vertices=12)

    # Low rails stay inside the known bow taper; fittings are unbranded.
    for side,label in ((1,'port'),(-1,'starboard')):
        rail=[(1.03,side*.92,1.05),(1.45,side*.79,1.10),(1.85,side*.63,1.14),(2.20,side*.46,1.15)]
        b.tube(label+'_bow_handrail',rail,.015,m['chrome'])
        for x,y,z in (rail[0],rail[-1]):
            b.cylinder(label+'_rail_stanchion',(x,y,z-.16),(x,y,z),.012,m['chrome'],vertices=12)
            b.box(label+'_rail_base',(x,y,z-.16),(.075,.05,.013),m['alloy'],bevel=.007)
        b.tube(label+'_cockpit_grabrail',[(-1.40,side*.99,.87),(-1.40,side*.99,1.01),(-.83,side*.99,1.01),(-.83,side*.99,.87)],.014,m['chrome'])
        for x in (-1.45,-.91):
            b.cylinder(label+'_cupholder_well',(x,side*.925,.76),(x,side*.925,.80),.043,m['trim'],vertices=24)
            b.torus(label+'_cupholder_rim',(x,side*.925,.802),.043,.004,m['alloy'],axis='Z',major_segments=32,minor_segments=8)
        for x,y,z in ((-2.13,side*1.02,.91),(1.04,side*.98,.99)):
            b.box(label+'_cleat_base',(x,y,z),(.15,.06,.015),m['alloy'],bevel=.008)
            for dx in (-.042,.042):
                b.cylinder(label+'_cleat_post',(x+dx,y,z),(x+dx,y,z+.04),.010,m['chrome'],vertices=12)
            b.cylinder(label+'_cleat_horn',(x-.12,y,z+.045),(x+.12,y,z+.045),.012,m['chrome'],vertices=16)
        b.box(label+'_navigation_lamp_base',(1.07,side*1.01,.99),(.17,.065,.045),m['trim'],bevel=.016)
        b.sphere(label+'_navigation_lamp',(1.07,side*1.045,1.00),(.062,.025,.021),m['red'] if side==1 else m['green'],segments=20,rings=10)
    b.cylinder('all_round_light_pole',(-2.13,.84,.88),(-2.13,.84,1.70),.012,m['chrome'],vertices=16)
    b.cylinder('all_round_light_base',(-2.13,.84,.865),(-2.13,.84,.89),.037,m['alloy'],vertices=20)
    b.sphere('all_round_white_light',(-2.13,.84,1.735),(.035,.035,.050),m['lamp'],segments=20,rings=12)
    b.torus('bow_towing_eye',(2.91,0,.705),.035,.009,m['alloy'],axis='Y',major_segments=24,minor_segments=8)
    for y in (-.97,-.65):
        b.tube('stowed_swim_ladder_rail',[(-2.46,y,.51),(-2.76,y,.51),(-2.94,y,.45)],.014,m['chrome'])
    for x in (-2.51,-2.65,-2.79):
        b.cylinder('stowed_swim_ladder_rung',(x,-.97,.51),(x,-.65,.51),.016,m['alloy'],vertices=16)
        b.box('ladder_non_slip_pad',(x,-.81,.527),(.05,.25,.012),m['rubber'],bevel=.005)

    # The mounting bracket remains fixed to the hull. Tilt, steering and prop
    # spin form a strict hierarchy so engines can drive them independently.
    trim_pivot=b.empty('outboard_trim_pivot',(-2.90,0,.58))
    steer_pivot=b.empty('outboard_steering_pivot',(-3.04,0,.60),parent=trim_pivot)
    prop_pivot=b.empty('boat_propeller_pivot',(-3.25,0,-.44),parent=steer_pivot)

    def motor(obj):
        b.parent(obj,steer_pivot)
        return obj

    for side in (-1,1):
        b.box('transom_mount_plate',(-2.825,side*.17,.47),(.055,.13,.30),m['steel'],bevel=.015)
        b.box('outboard_clamp',(-2.86,side*.17,.59),(.17,.09,.07),m['alloy'],bevel=.015)
        b.cylinder('transom_clamp_screw',(-2.77,side*.17,.53),(-2.91,side*.17,.53),.012,m['chrome'],vertices=12)
        b.cylinder('trim_hinge_pin',(-2.90,side*.13,.58),(-2.90,side*.26,.58),.031,m['chrome'],vertices=20)
        bracket=panel('tilting_motor_bracket',[(-2.90,side*.16,.61),(-3.10,side*.16,.59),
                          (-3.13,side*.16,.22),(-2.97,side*.16,.20)],(0,.035,0),m['steel'],bevel=.013)
        b.parent(bracket,trim_pivot)
        b.parent(b.cylinder('trim_hydraulic_ram',(-2.89,side*.17,.26),(-3.04,side*.17,.48),.018,m['chrome'],vertices=16),trim_pivot)
    b.parent(b.cylinder('outboard_swivel_shaft',(-3.04,0,.22),(-3.04,0,.71),.045,m['steel'],vertices=24),trim_pivot)

    # Sculpted closed powerhead cowling, with shoulders rather than a simple box.
    sections=[(.34,-3.055,.20,.22),(.45,-3.10,.27,.27),(.66,-3.12,.30,.29),
              (.94,-3.11,.29,.285),(1.075,-3.105,.25,.25),(1.13,-3.095,.18,.20)]
    vertices=[]; ring=48
    for z,x,hx,hy in sections:
        for j in range(ring):
            a=math.tau*j/ring
            # Rounded superellipse shoulders retain a manufactured appearance.
            xx=math.copysign(abs(math.cos(a))**.72,math.cos(a))
            yy=math.copysign(abs(math.sin(a))**.72,math.sin(a))
            vertices.append((x+hx*xx,hy*yy,z))
    faces=[]
    for i in range(len(sections)-1):
        faces.extend((i*ring+j,i*ring+(j+1)%ring,(i+1)*ring+(j+1)%ring,(i+1)*ring+j) for j in range(ring))
    faces += [tuple(reversed(range(ring))),tuple((len(sections)-1)*ring+j for j in range(ring))]
    cowl=motor(b.mesh('outboard_powerhead_cowling',vertices,faces,m['hull'],smooth=True))
    cowl.data.materials.append(m['accent'])
    for i in range(ring): cowl.data.polygons[ring+i].material_index=1
    for side in (-1,1):
        motor(b.box('cowling_air_intake',(-3.16,side*.286,.96),(.29,.015,.08),m['trim'],bevel=.014))
        for x in (-3.26,-3.20,-3.14,-3.08):
            motor(b.box('cowling_vent_louvre',(x,side*.298,.96),(.019,.016,.064),m['steel'],bevel=.005,rotation=(0,.20,0)))
        motor(b.box('cowling_latch',(-3.07,side*.258,.44),(.06,.026,.07),m['alloy'],bevel=.010))
    motor(b.tube('outboard_rear_lift_handle',[(-3.38,-.12,.99),(-3.44,-.12,1.03),(-3.44,.12,1.03),(-3.38,.12,.99)],.014,m['rubber']))
    motor(panel('outboard_drive_leg',[(-2.96,0,.36),(-3.15,0,.34),(-3.18,0,-.26),
                    (-3.29,0,-.40),(-3.28,0,-.51),(-3.04,0,-.51),(-2.96,0,-.29)],(0,.105,0),m['hull'],bevel=.018))
    motor(b.sphere('outboard_gearcase',(-3.12,0,-.44),(.235,.085,.085),m['hull'],segments=32,rings=16))
    motor(panel('outboard_skeg',[(-3.02,0,-.48),(-3.29,0,-.50),(-3.31,0,-.80),(-3.20,0,-.80)],(0,.028,0),m['hull'],bevel=.008))
    motor(panel('anti_ventilation_plate',[(-2.91,-.13,-.27),(-3.31,-.17,-.27),(-3.42,-.10,-.27),
                    (-3.42,.10,-.27),(-3.31,.17,-.27),(-2.91,.13,-.27)],(0,0,.018),m['hull'],bevel=.009))
    for side in (-1,1):
        for z in (-.31,-.35,-.39):
            motor(b.box('gearcase_water_inlet',(-2.985,side*.056,z),(.044,.008,.012),m['trim'],bevel=.003))
    # Flexible controls are deliberately static at the neutral rig position.
    b.tube('outboard_rigging_boot',[(-2.65,.29,.47),(-2.86,.35,.61),(-3.04,.27,.54)],.033,m['rubber'])

    # Three closed, swept, twisted marine propeller blades rotate about local X.
    b.parent(b.cylinder('marine_propeller_hub',(-3.235,0,-.44),(-3.405,0,-.44),.047,m['alloy'],vertices=32),prop_pivot)
    b.parent(b.cylinder('propeller_retaining_nut',(-3.405,0,-.44),(-3.422,0,-.44),.019,m['chrome'],vertices=6),prop_pivot)
    radii=[.045,.075,.115,.155,.185,.201]
    chords=[.042,.071,.087,.083,.061,.026]
    samples=[.5-.5*math.cos(math.pi*i/16) for i in range(17)]
    profile=[(u,1) for u in samples]+[(u,-1) for u in reversed(samples[1:-1])]
    for blade in range(3):
        a=math.radians(20+blade*120)
        vv=[]
        for radius,chord in zip(radii,chords):
            fraction=(radius-radii[0])/(radii[-1]-radii[0])
            pitch=math.radians(43-25*fraction)
            sweep=.025*fraction*fraction
            for u,sign in profile:
                thickness=5*.10*chord*(.2969*math.sqrt(u)-.126*u-.3516*u*u+.2843*u**3-.1036*u**4)
                chordwise=(u-.30)*chord+sweep
                tangent=chordwise*math.cos(pitch)-sign*thickness*math.sin(pitch)
                x=-3.32+chordwise*math.sin(pitch)+sign*thickness*math.cos(pitch)
                vv.append((x,radius*math.sin(a)+tangent*math.cos(a),-.44+radius*math.cos(a)-tangent*math.sin(a)))
        n=len(profile); ff=[]
        for i in range(len(radii)-1):
            ff.extend((i*n+j,i*n+(j+1)%n,(i+1)*n+(j+1)%n,(i+1)*n+j) for j in range(n))
        ff += [tuple(reversed(range(n))),tuple((len(radii)-1)*n+j for j in range(n))]
        b.parent(b.mesh('marine_propeller_blade',vv,ff,m['alloy'],smooth=True),prop_pivot)

    return {
        'equipment': 'Open five-seat cockpit plus tapered bow lounge, split windscreen, starboard helm, rails and a single sculpted outboard.',
        'moving_parts': ['outboard_trim_pivot','outboard_steering_pivot','boat_propeller_pivot','boat_steering_wheel_pivot'],
        'pivot_notes': 'Outboard trim local Y, steering local Z below trim, propeller local X below steering; helm wheel local X along inclined column.',
        'limitations': 'No steering linkage solver, fluid simulation, working instruments or deforming rigging hoses. Waterline is Z=0; lower unit intentionally extends to Z=-0.80.'
    }

if __name__ == "__main__":
    main()
