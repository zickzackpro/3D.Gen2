"""Small malformed-file fixtures that exercise the exported-file validator."""

import json
from pathlib import Path
import struct
import tempfile
import unittest

from validate_exports import ValidationError, validate_directory, validate_file


def fixture(*, bad_index=False, missing_uv=False, nonfinite=False, cycle=False,
            translate=False, stride=False):
    positions = [0., 0., 0., 1., 0., 0., 0., 1., 0.]
    if nonfinite:
        positions[0] = float("nan")
    if stride:
        position_bytes = b"".join(struct.pack("<4f", *positions[i:i+3], 99.)
                                  for i in range(0, 9, 3))
    else:
        position_bytes = struct.pack("<9f", *positions)
    parts = [position_bytes, struct.pack("<9f", *([0., 0., 1.] * 3)),
             struct.pack("<6f", 0., 0., 1., 0., 0., 1.),
             struct.pack("<3H", 0, 1, 10 if bad_index else 2)]
    binary = b"".join(parts)
    views, offset = [], 0
    for part in parts:
        views.append({"buffer": 0, "byteOffset": offset, "byteLength": len(part)})
        offset += len(part)
    if stride:
        views[0]["byteStride"] = 16
    attrs = {"POSITION": 0, "NORMAL": 1, "TEXCOORD_0": 2}
    if missing_uv:
        del attrs["TEXCOORD_0"]
    node = {"mesh": 0}
    if cycle:
        node["children"] = [0]
    if translate:
        node["translation"] = [5., 6., 7.]
        node["scale"] = [2., 3., 1.]
    doc = {
        "asset": {"version": "2.0"}, "scene": 0,
        "scenes": [{"nodes": [0]}], "nodes": [node],
        "meshes": [{"primitives": [{"attributes": attrs, "indices": 3}]}],
        "buffers": [{"byteLength": len(binary)}], "bufferViews": views,
        "accessors": [
            {"bufferView": 0, "componentType": 5126, "type": "VEC3", "count": 3},
            {"bufferView": 1, "componentType": 5126, "type": "VEC3", "count": 3},
            {"bufferView": 2, "componentType": 5126, "type": "VEC2", "count": 3},
            {"bufferView": 3, "componentType": 5123, "type": "SCALAR", "count": 3},
        ],
    }
    encoded = json.dumps(doc).encode()
    encoded += b" " * (-len(encoded) % 4)
    binary += b"\x00" * (-len(binary) % 4)
    payload = (struct.pack("<II", len(encoded), 0x4E4F534A) + encoded
               + struct.pack("<II", len(binary), 0x004E4942) + binary)
    return struct.pack("<4sII", b"glTF", 2, 12 + len(payload)) + payload


class ValidationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)

    def write(self, content, name="asset_LOD0.glb"):
        path = self.directory / name
        path.write_bytes(content)
        return path

    def test_valid_triangle(self):
        result = validate_file(self.write(fixture()))
        self.assertEqual(result["triangles"], 1)
        self.assertEqual(result["dimensions_gltf_xyz_m"], [1., 1., 0.])

    def test_invalid_indices(self):
        with self.assertRaisesRegex(ValidationError, "index out of range"):
            validate_file(self.write(fixture(bad_index=True)))

    def test_missing_uv(self):
        with self.assertRaisesRegex(ValidationError, "TEXCOORD_0"):
            validate_file(self.write(fixture(missing_uv=True)))

    def test_collision_uv_optional(self):
        result = validate_file(self.write(fixture(missing_uv=True), "asset_collision.glb"))
        self.assertTrue(result["collision"])

    def test_invalid_size(self):
        with self.assertRaisesRegex(ValidationError, "length"):
            validate_file(self.write(fixture() + b"trailing"))

    def test_nonfinite(self):
        with self.assertRaisesRegex(ValidationError, "non-finite"):
            validate_file(self.write(fixture(nonfinite=True)))

    def test_cycle(self):
        with self.assertRaisesRegex(ValidationError, "cycle"):
            validate_file(self.write(fixture(cycle=True)))

    def test_world_bounds(self):
        result = validate_file(self.write(fixture(translate=True)))
        self.assertEqual(result["bounds_min"], [5., 6., 7.])
        self.assertEqual(result["dimensions_gltf_xyz_m"], [2., 3., 0.])

    def test_interleaved_positions(self):
        result = validate_file(self.write(fixture(stride=True)))
        self.assertEqual(result["triangles"], 1)

    def test_lods_must_reduce(self):
        self.write(fixture(), "asset_LOD0.glb")
        self.write(fixture(), "asset_LOD2.glb")
        self.assertFalse(validate_directory(self.directory)["ok"])

    def test_empty_directory_fails(self):
        self.assertFalse(validate_directory(self.directory)["ok"])


if __name__ == "__main__":
    unittest.main()
