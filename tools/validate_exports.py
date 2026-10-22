#!/usr/bin/env python3
"""Read-only structural/geometry checks for noncompressed Blender GLB exports.

Uses only the Python standard library. These checks do not certify visual
quality, UV packing, topology suitability, physics, or an engine import.
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import re
import struct
import sys


class ValidationError(ValueError):
    """The input is malformed or violates this export policy."""


def require(condition, message):
    if not condition:
        raise ValidationError(message)


def index(items, value, label):
    require(isinstance(value, int) and not isinstance(value, bool)
            and 0 <= value < len(items), f"Invalid {label} index: {value!r}")
    return items[value]


def finite_vector(values, size, label):
    require(isinstance(values, list) and len(values) == size,
            f"{label} must have {size} components")
    require(all(isinstance(x, (int, float)) and not isinstance(x, bool)
                and math.isfinite(x) for x in values), f"Non-finite {label}")
    return values


def multiply(a, b):
    return [[sum(a[r][k] * b[k][c] for k in range(4))
             for c in range(4)] for r in range(4)]


IDENTITY = [[float(r == c) for c in range(4)] for r in range(4)]


def node_matrix(node):
    if "matrix" in node:
        require(not any(k in node for k in ("translation", "rotation", "scale")),
                "Node mixes matrix and TRS transforms")
        values = finite_vector(node["matrix"], 16, "node matrix")
        return [[values[c * 4 + r] for c in range(4)] for r in range(4)]
    t = finite_vector(node.get("translation", [0, 0, 0]), 3, "translation")
    s = finite_vector(node.get("scale", [1, 1, 1]), 3, "scale")
    x, y, z, w = finite_vector(node.get("rotation", [0, 0, 0, 1]), 4, "rotation")
    require(abs(x*x + y*y + z*z + w*w - 1) < 0.001,
            "Node rotation quaternion is not normalized")
    rotation = [
        [1 - 2*(y*y + z*z), 2*(x*y-z*w), 2*(x*z+y*w)],
        [2*(x*y+z*w), 1 - 2*(x*x+z*z), 2*(y*z-x*w)],
        [2*(x*z-y*w), 2*(y*z+x*w), 1 - 2*(x*x+y*y)],
    ]
    return [[rotation[r][c] * s[c] for c in range(3)] + [t[r]]
            for r in range(3)] + [[0, 0, 0, 1]]


class GLB:
    COMPONENTS = {5120: ("b", 1), 5121: ("B", 1), 5122: ("h", 2),
                  5123: ("H", 2), 5125: ("I", 4), 5126: ("f", 4)}
    WIDTHS = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4,
              "MAT2": 4, "MAT3": 9, "MAT4": 16}

    def __init__(self, path):
        raw = Path(path).read_bytes()
        require(len(raw) >= 20, "GLB is shorter than its header and first chunk")
        magic, version, length = struct.unpack_from("<4sII", raw)
        require(magic == b"glTF", "Invalid GLB magic")
        require(version == 2, f"Unsupported GLB version {version}")
        require(length == len(raw), "GLB header length does not match file size")
        chunks = []
        offset = 12
        while offset < len(raw):
            require(offset + 8 <= len(raw), "Truncated chunk header")
            size, kind = struct.unpack_from("<II", raw, offset)
            offset += 8
            require(size % 4 == 0, "Chunk length must be four-byte aligned")
            require(offset + size <= len(raw), "Chunk exceeds file size")
            chunks.append((kind, raw[offset:offset + size]))
            offset += size
        require(chunks and chunks[0][0] == 0x4E4F534A, "First chunk must be JSON")
        require(sum(k == 0x4E4F534A for k, _ in chunks) == 1, "Duplicate JSON chunks")
        require(sum(k == 0x004E4942 for k, _ in chunks) <= 1, "Duplicate BIN chunks")
        try:
            self.doc = json.loads(chunks[0][1].decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as error:
            raise ValidationError(f"Invalid GLB JSON: {error}") from error
        require(isinstance(self.doc, dict), "glTF JSON must be an object")
        require(self.doc.get("asset", {}).get("version") == "2.0", "Expected glTF 2.0 asset")
        self.binary = next((data for kind, data in chunks if kind == 0x004E4942), b"")
        self.size = len(raw)
        buffers = self.doc.get("buffers", [])
        require(len(buffers) == 1, "Expected one embedded GLB buffer")
        require("uri" not in buffers[0], "External buffers are not supported")
        declared = buffers[0].get("byteLength", -1)
        require(isinstance(declared, int) and 0 < declared <= len(self.binary),
                "Invalid embedded buffer byteLength")
        require(len(self.binary) - declared <= 3, "Excessive BIN padding")
        self.buffer_length = declared
        self.views = self.doc.get("bufferViews", [])
        self.accessors = self.doc.get("accessors", [])
        self.cache = {}
        for number, view in enumerate(self.views):
            require(view.get("buffer") == 0, f"bufferView {number} references an invalid buffer")
            begin, size = view.get("byteOffset", 0), view.get("byteLength", -1)
            require(isinstance(begin, int) and isinstance(size, int)
                    and begin >= 0 and size > 0 and begin + size <= declared,
                    f"bufferView {number} is out of bounds")
            if "byteStride" in view:
                stride = view["byteStride"]
                require(isinstance(stride, int) and 4 <= stride <= 252 and stride % 4 == 0,
                        f"bufferView {number} has an invalid byteStride")

    def read_accessor(self, number):
        if number in self.cache:
            return self.cache[number]
        acc = index(self.accessors, number, "accessor")
        require("sparse" not in acc, "Sparse accessors are outside this validator's policy")
        view = index(self.views, acc.get("bufferView"), "bufferView")
        component = acc.get("componentType")
        require(component in self.COMPONENTS, f"Unsupported component type {component}")
        kind = acc.get("type")
        require(kind in self.WIDTHS, f"Unsupported accessor type {kind}")
        fmt, component_size = self.COMPONENTS[component]
        width = self.WIDTHS[kind]
        require(not kind.startswith("MAT") or component_size == 4,
                "Packed integer matrix accessors are unsupported")
        item = struct.Struct("<" + fmt * width)
        offset = acc.get("byteOffset", 0)
        count = acc.get("count", 0)
        stride = view.get("byteStride", item.size)
        require(isinstance(offset, int) and offset >= 0 and offset % component_size == 0,
                f"Accessor {number} has an invalid offset")
        require(isinstance(count, int) and count > 0, f"Accessor {number} is empty")
        require(stride >= item.size, f"Accessor {number} stride is too small")
        require(offset + (count - 1) * stride + item.size <= view["byteLength"],
                f"Accessor {number} exceeds its bufferView")
        start = view.get("byteOffset", 0) + offset
        values = [item.unpack_from(self.binary, start + i * stride) for i in range(count)]
        require(all(math.isfinite(x) for row in values for x in row),
                f"Accessor {number} contains non-finite data")
        self.cache[number] = values
        return values

    def validate_references(self):
        doc = self.doc
        for texture in doc.get("textures", []):
            if "source" in texture:
                index(doc.get("images", []), texture["source"], "texture image")
            if "sampler" in texture:
                index(doc.get("samplers", []), texture["sampler"], "texture sampler")
        def walk_material(value):
            if isinstance(value, dict):
                for key, child in value.items():
                    if key.endswith("Texture") and isinstance(child, dict) and "index" in child:
                        index(doc.get("textures", []), child["index"], "material texture")
                    walk_material(child)
            elif isinstance(value, list):
                for child in value:
                    walk_material(child)
        for material in doc.get("materials", []):
            walk_material(material)
        for image in doc.get("images", []):
            require("uri" not in image, "Expected embedded images in portable GLBs")
            view = index(self.views, image.get("bufferView"), "image bufferView")
            begin = view.get("byteOffset", 0)
            payload = self.binary[begin:begin + view["byteLength"]]
            mime = image.get("mimeType")
            valid = ((mime == "image/png" and payload.startswith(b"\x89PNG\r\n\x1a\n"))
                     or (mime == "image/jpeg" and payload.startswith(b"\xff\xd8\xff"))
                     or (mime == "image/webp" and payload.startswith(b"RIFF")
                         and payload[8:12] == b"WEBP"))
            require(valid, f"Image signature does not match supported MIME type {mime!r}")


def validate_file(path):
    path = Path(path)
    glb = GLB(path)
    doc = glb.doc
    glb.validate_references()
    collision = path.stem.lower().endswith("_collision")
    meshes = doc.get("meshes", [])
    require(meshes, "No meshes were exported")
    mesh_data = []
    primitive_count = 0
    for mesh_number, mesh in enumerate(meshes):
        primitives = mesh.get("primitives", [])
        require(primitives, f"Mesh {mesh_number} is empty")
        positions = []
        triangles = 0
        for primitive in primitives:
            primitive_count += 1
            require(primitive.get("mode", 4) == 4, "Expected triangle primitives")
            attrs = primitive.get("attributes", {})
            required = {"POSITION", "NORMAL"} | (set() if collision else {"TEXCOORD_0"})
            require(required <= attrs.keys(), f"Missing attributes: {sorted(required - attrs.keys())}")
            pos_acc = index(glb.accessors, attrs["POSITION"], "position accessor")
            require(pos_acc.get("type") == "VEC3" and pos_acc.get("componentType") == 5126,
                    "Positions must be floating-point VEC3")
            pos = glb.read_accessor(attrs["POSITION"])
            for semantic, accessor_number in attrs.items():
                values = glb.read_accessor(accessor_number)
                require(len(values) == len(pos), f"{semantic} count differs from POSITION count")
                if semantic in ("NORMAL", "TEXCOORD_0"):
                    require(len(values[0]) == (3 if semantic == "NORMAL" else 2),
                            f"{semantic} has the wrong vector size")
            if "material" in primitive:
                index(doc.get("materials", []), primitive["material"], "primitive material")
            if "indices" in primitive:
                idx_acc = index(glb.accessors, primitive["indices"], "indices accessor")
                require(idx_acc.get("type") == "SCALAR"
                        and idx_acc.get("componentType") in (5121, 5123, 5125),
                        "Indices must use unsigned scalar integers")
                values = glb.read_accessor(primitive["indices"])
                require(all(0 <= row[0] < len(pos) for row in values), "Triangle index out of range")
                count = len(values)
            else:
                count = len(pos)
            require(count >= 3 and count % 3 == 0, "Primitive has incomplete or empty triangles")
            triangles += count // 3
            positions.extend(pos)
        mesh_data.append((positions, triangles))

    nodes = doc.get("nodes", [])
    require(nodes, "No scene nodes were exported")
    parents = {}
    local = []
    for number, node in enumerate(nodes):
        local.append(node_matrix(node))
        if "mesh" in node:
            index(meshes, node["mesh"], "node mesh")
        if "camera" in node:
            index(doc.get("cameras", []), node["camera"], "node camera")
        if "skin" in node:
            index(doc.get("skins", []), node["skin"], "node skin")
        for child in node.get("children", []):
            index(nodes, child, "child node")
            require(child not in parents, f"Node {child} has multiple parents or duplicate references")
            parents[child] = number
    states = [0] * len(nodes)
    def visit(number):
        require(states[number] != 1, "Scene node hierarchy contains a cycle")
        if states[number] == 2:
            return
        states[number] = 1
        for child in nodes[number].get("children", []):
            visit(child)
        states[number] = 2
    for number in range(len(nodes)):
        visit(number)
    for skin in doc.get("skins", []):
        for joint in skin.get("joints", []):
            index(nodes, joint, "skin joint")
        if "inverseBindMatrices" in skin:
            index(glb.accessors, skin["inverseBindMatrices"], "inverse bind matrix accessor")
    scenes = doc.get("scenes", [])
    scene = index(scenes, doc.get("scene", 0), "default scene")
    for candidate in scenes:
        seen_roots = set()
        for root in candidate.get("nodes", []):
            index(nodes, root, "scene root")
            require(root not in parents and root not in seen_roots, "Scene root is duplicated or has a parent")
            seen_roots.add(root)
    minimum, maximum = [math.inf] * 3, [-math.inf] * 3
    instances = 0
    triangle_count = 0
    def accumulate(number, parent_matrix):
        nonlocal instances, triangle_count
        node = nodes[number]
        world = multiply(parent_matrix, local[number])
        if "mesh" in node:
            instances += 1
            positions, triangles = mesh_data[node["mesh"]]
            triangle_count += triangles
            for p in positions:
                transformed = [sum(world[r][c] * p[c] for c in range(3)) + world[r][3]
                               for r in range(3)]
                require(all(math.isfinite(x) for x in transformed), "Non-finite transformed geometry")
                for axis in range(3):
                    minimum[axis] = min(minimum[axis], transformed[axis])
                    maximum[axis] = max(maximum[axis], transformed[axis])
        for child in node.get("children", []):
            accumulate(child, world)
    for root in scene.get("nodes", []):
        accumulate(root, IDENTITY)
    require(triangle_count > 0, "Default scene contains no triangles")
    return {
        "path": str(path), "ok": True, "bytes": glb.size,
        "triangles": triangle_count, "meshes": len(meshes), "mesh_instances": instances,
        "primitives": primitive_count, "nodes": len(nodes),
        "materials": len(doc.get("materials", [])), "images": len(doc.get("images", [])),
        "bounds_min": minimum, "bounds_max": maximum,
        "dimensions_gltf_xyz_m": [maximum[i] - minimum[i] for i in range(3)],
        "collision": collision,
    }


def validate_directory(directory):
    directory = Path(directory)
    paths = sorted(directory.rglob("*.glb")) if directory.is_dir() else [directory]
    results, errors, lod_checks = [], [], []
    if not paths:
        errors.append("No GLB files found")
    for path in paths:
        try:
            results.append(validate_file(path))
        except (OSError, ValueError, TypeError, KeyError, struct.error, RecursionError) as error:
            results.append({"path": str(path), "ok": False, "error": str(error)})
    groups = {}
    for result in results:
        if not result["ok"]:
            continue
        path = Path(result["path"])
        match = re.fullmatch(r"(.+)_LOD([012])", path.stem, re.IGNORECASE)
        if match:
            key = str(path.parent / match.group(1))
            groups.setdefault(key, {})[int(match.group(2))] = result["triangles"]
    for name, levels in groups.items():
        ordered = sorted(levels.items())
        ok = all(b[1] <= a[1] for a, b in zip(ordered, ordered[1:]))
        if 0 in levels and 2 in levels:
            ok = ok and levels[2] < levels[0]
        lod_checks.append({"asset": name, "triangles_by_lod": levels, "ok": ok})
        if not ok:
            errors.append(f"LOD triangle counts did not decrease for {name}")
    return {"ok": not errors and bool(results) and all(r["ok"] for r in results),
            "files": results, "lod_checks": lod_checks, "errors": errors,
            "scope": "Static GLB geometry/structure only; no visual or engine certification."}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", type=Path, help="GLB file or output directory to inspect recursively")
    args = parser.parse_args(argv)
    report = validate_directory(args.path)
    print(json.dumps(report, indent=2, allow_nan=False))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
