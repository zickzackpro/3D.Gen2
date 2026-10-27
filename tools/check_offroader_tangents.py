#!/usr/bin/env python3
"""Check exported tangent bases against the normal-map UVs on actual car parts.

Run in a fresh background Blender process:
  blender --background --factory-startup --python-exit-code 1 \
    --python tools/check_offroader_tangents.py -- generated/offroader/offroader.blend

Uses one continuous leather/cloth seat and one brake rotor from the editable
source, without changing that file. Includes an uncorrected negative control so
that presence of TANGENT alone cannot make the regression pass. Temporary GLBs
are removed after the check; --report writes a durable JSON receipt.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
import hashlib
import json
import math
from pathlib import Path
import sys
import tempfile

import bpy
import bmesh
from mathutils import Matrix, Vector

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tools'))
import generate_vehicles as generator  # noqa: E402
from validate_exports import GLB  # noqa: E402


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def sha256(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def yup(value):
    return (value[0], value[2], -value[1])


def material_kind(material):
    name = material.name if hasattr(material, 'name') else material['name']
    for token, label in [('saddle_leather', 'leather'), ('seat_insert', 'cloth'),
                         ('brake_contact', 'brake')]:
        if token in name:
            return label
    raise AssertionError('Unexpected sample material: ' + name)


def normal_uv(material):
    shader = next(node for node in material.node_tree.nodes if node.type == 'BSDF_PRINCIPLED')
    node = shader.inputs['Normal'].links[0].from_node
    require(node.type == 'NORMAL_MAP', 'Sample does not use a tangent normal map')
    return node.uv_map or 'UVMap'


def original_uvs(objects):
    return {obj.name: {layer.name: [tuple(item.uv) for item in layer.data]
                      for layer in obj.data.uv_layers} for obj in objects}


def shader_bindings(objects):
    materials = {mat for obj in objects for mat in obj.data.materials}
    return {mat.name: [(node.name, node.type, getattr(node, 'uv_map', ''),
                       tuple((link.from_node.name, link.from_socket.name,
                              link.to_node.name, link.to_socket.name)
                             for socket in node.inputs for link in socket.links))
                      for node in mat.node_tree.nodes] for mat in materials}


def expected_bases(objects):
    """Independent per-material MikkTSpace calculation before export helper runs.

    Isolate material regions while retaining source split normals: tangents must
    not average across a cloth/leather boundary using the other shader's UVs.
    """
    tables = {}
    for obj in objects:
        mesh = obj.data
        for slot, material in enumerate(mesh.materials):
            kind = material_kind(material)
            table = defaultdict(list)
            polygons = [polygon for polygon in mesh.polygons if polygon.material_index == slot]
            loop_indices = [index for polygon in polygons for index in polygon.loop_indices]
            uv_name = normal_uv(material)
            normal_coords = [tuple(mesh.uv_layers[uv_name].data[index].uv) for index in loop_indices]
            atlas_coords = [tuple(mesh.uv_layers['UVMap'].data[index].uv) for index in loop_indices]
            normals = [tuple(mesh.corner_normals[index].vector) for index in loop_indices]
            reference = bpy.data.meshes.new('INDEPENDENT_' + kind)
            reference.from_pydata([tuple(vertex.co) for vertex in mesh.vertices], [],
                                  [tuple(polygon.vertices) for polygon in polygons])
            for polygon in reference.polygons:
                polygon.use_smooth = True
            reference.normals_split_custom_set(normals)
            uv = reference.uv_layers.new(name='ActualNormalUV')
            for index, coordinate in enumerate(normal_coords):
                uv.data[index].uv = coordinate
            reference.calc_tangents(uvmap='ActualNormalUV')
            for loop in reference.loops:
                index = loop.index
                position = yup(reference.vertices[loop.vertex_index].co)
                key = tuple(round(value, 5) for value in position)
                table[key].append({'position': position, 'normal': yup(loop.normal),
                                   'atlas_uv': atlas_coords[index],
                                   'normal_uv': normal_coords[index],
                                   'tangent': yup(loop.tangent),
                                   'sign': loop.bitangent_sign})
            tables[kind] = table
            bpy.data.meshes.remove(reference)
    require(set(tables) == {'leather', 'cloth', 'brake'}, 'Missing regression material')
    return tables


def compare_glb(path, expected):
    glb = GLB(path)
    result = {}
    for mesh in glb.doc['meshes']:
        for primitive in mesh['primitives']:
            material = glb.doc['materials'][primitive['material']]
            kind = material_kind(material)
            attributes = primitive['attributes']
            require('TANGENT' in attributes, 'Export omits tangents')
            normal_texture = material['normalTexture']
            require('KHR_texture_transform' not in normal_texture.get('extensions', {}),
                    'Unexpected normal UV transform; extend regression before using it')
            texcoord = normal_texture.get('texCoord', 0)
            positions = glb.read_accessor(attributes['POSITION'])
            normals = glb.read_accessor(attributes['NORMAL'])
            tangents = glb.read_accessor(attributes['TANGENT'])
            atlas_uvs = glb.read_accessor(attributes['TEXCOORD_0'])
            normal_uvs = glb.read_accessor(attributes['TEXCOORD_' + str(texcoord)])
            angles, wrong_signs = [], 0
            for p, n, t, a, u in zip(positions, normals, tangents, atlas_uvs, normal_uvs):
                candidates = expected[kind][tuple(round(value, 5) for value in p)]
                # glTF flips V; tangent and sign remain in the normal image's
                # original basis because the exported image is flipped as well.
                a = (a[0], 1 - a[1]); u = (u[0], 1 - u[1])
                candidates = [row for row in candidates
                              if Vector(n).dot(Vector(row['normal'])) > .99999
                              and max(abs(x-y) for x,y in zip(a,row['atlas_uv'])) < .00001
                              and max(abs(x-y) for x,y in zip(u,row['normal_uv'])) < .00001]
                require(candidates, 'Exported vertex/atlas/normal UV lost correspondence: ' + kind)
                angles_for_vertex = [math.degrees(math.acos(max(-1., min(1.,
                    Vector(t[:3]).normalized().dot(Vector(row['tangent']).normalized())))))
                                     for row in candidates]
                best = min(range(len(candidates)), key=angles_for_vertex.__getitem__)
                angles.append(angles_for_vertex[best])
                wrong_signs += int(t[3] * candidates[best]['sign'] < 0)
            result[kind] = {'vertices': len(positions), 'normal_texcoord': texcoord,
                            'max_angle_degrees': max(angles),
                            'mean_angle_degrees': sum(angles)/len(angles),
                            'wrong_bitangent_signs': wrong_signs}
    require(set(result) == set(expected), 'GLB lost one of the tested materials')
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    parser.add_argument('--report', type=Path)
    args = parser.parse_args(sys.argv[sys.argv.index('--')+1:] if '--' in sys.argv else [])
    require(bpy.app.background, 'Use background Blender to protect open editor sessions')
    source = args.source.resolve()
    bpy.ops.wm.open_mainfile(filepath=str(source))
    source_collection = bpy.data.collections.get('offroader_SOURCE')
    require(source_collection is not None, 'Source .blend has no offroader_SOURCE collection')
    seats = sorted((obj for obj in source_collection.objects
                    if obj.type == 'MESH' and obj.get('seat_surface_uv')), key=lambda obj:obj.name)
    brakes = sorted((obj for obj in source_collection.objects
                     if obj.type == 'MESH' and obj.name.endswith('_brake_rotor')), key=lambda obj:obj.name)
    require(seats and brakes, 'Source lacks a continuous seat or brake rotor')
    originals = [seats[0], brakes[0]]
    original_coordinates = original_uvs(originals)
    original_shaders = shader_bindings(originals)
    collection = bpy.data.collections.new('TANGENT_REGRESSION_ONLY')
    bpy.context.scene.collection.children.link(collection)
    copies = []
    for obj in originals:
        clone = obj.copy(); clone.data = obj.data.copy(); clone.parent = None
        clone.matrix_world = Matrix.Identity(4)
        collection.objects.link(clone)
        bm = bmesh.new(); bm.from_mesh(clone.data)
        bmesh.ops.triangulate(bm, faces=list(bm.faces)); bm.to_mesh(clone.data); bm.free()
        clone.data.update()
        copies.append(clone)
    before_coordinates = original_uvs(copies)
    expected = expected_bases(copies)
    with tempfile.TemporaryDirectory(prefix='offroader-tangents-') as temporary:
        before = Path(temporary)/'uncorrected.glb'
        generator.export_glb(before, copies)
        negative = compare_glb(before, expected)
        require(any(row['max_angle_degrees'] > 10 or row['wrong_bitangent_signs']
                    for row in negative.values()), 'Negative control did not expose old tangent regression')
        generator.prepare_offroader_export_tangents(copies)
        for obj in copies:
            require(obj.data.uv_layers.active.name == 'ExportTangentUV', 'Export tangent UV not active')
            require(obj.data.uv_layers['ExportTangentUV'].active_render, 'Export tangent UV not active_render')
            for name, coordinates in before_coordinates[obj.name].items():
                require([tuple(item.uv) for item in obj.data.uv_layers[name].data] == coordinates,
                        'Helper changed original UV coordinates: ' + name)
        after = Path(temporary)/'corrected.glb'
        generator.export_glb(after, copies)
        corrected = compare_glb(after, expected)
    print(json.dumps({'uncorrected': negative, 'corrected': corrected}, indent=2))
    for kind, row in corrected.items():
        require(row['max_angle_degrees'] < .1, 'Wrong exported normal tangent basis: ' + kind)
        require(row['wrong_bitangent_signs'] == 0, 'Wrong exported bitangent handedness: ' + kind)
    require(original_uvs(originals) == original_coordinates, 'Source UVs changed')
    require(shader_bindings(originals) == original_shaders, 'Source shader bindings changed')
    report = {'status': 'passed', 'source': str(source), 'source_sha256': sha256(source),
              'generator_sha256': sha256(ROOT/'generate_vehicles.py'),
              'checker_sha256': sha256(Path(__file__)), 'blender': bpy.app.version_string,
              'scope': 'Actual leather/cloth seat and brake rotor export copies; numeric per-material tangent bases, bitangent signs, UV preservation and uncorrected negative control. Not an engine render test.',
              'source_parts': [obj.name for obj in originals], 'uncorrected_control': negative,
              'corrected': corrected, 'source_uvs_unchanged': True, 'source_shaders_unchanged': True}
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, indent=2)+'\n')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
