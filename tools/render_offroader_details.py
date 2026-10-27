#!/usr/bin/env python3
"""Reproducible detail views from the delivered Blender source, never image edits.

blender -b --python-exit-code 1 --python tools/render_offroader_details.py -- \
    --asset-dir generated/offroader
"""
import argparse
import hashlib
import json
import math
from pathlib import Path
import sys

import bpy
import _cycles
from mathutils import Vector

SCRIPT_SHA256 = hashlib.sha256(Path(__file__).resolve().read_bytes()).hexdigest()


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--asset-dir', type=Path, required=True)
    parser.add_argument('--resolution', type=int, default=1400)
    parser.add_argument('--samples', type=int, default=256)
    parser.add_argument('--views', nargs='+', choices=('engine','cockpit','interior','access'), default=['engine','cockpit','interior','access'])
    args = parser.parse_args(sys.argv[sys.argv.index('--')+1:])
    directory = args.asset_dir.resolve()
    manifest_path = directory/'manifest.json'
    manifest = json.loads(manifest_path.read_text())
    source = directory/'offroader.blend'
    saved = next(item for item in manifest['files'] if item['path'] == source.name)
    if digest(source) != saved['sha256']:
        raise RuntimeError('Source differs from the delivered manifest')
    previews=[]
    provenance=manifest.setdefault('detail_preview_provenance', {})
    for view in args.views:
        bpy.ops.wm.open_mainfile(filepath=str(source))
        scene=bpy.context.scene
        scene.render.resolution_x=args.resolution
        scene.render.resolution_y=int(args.resolution*.75)
        scene.render.resolution_percentage=100
        scene.cycles.samples=args.samples
        scene.cycles.use_denoising=bool(getattr(_cycles,'with_openimagedenoise',False))
        if scene.cycles.use_denoising:
            scene.cycles.denoiser='OPENIMAGEDENOISE'
        scene.cycles.adaptive_threshold=.02
        scene.cycles.adaptive_min_samples=min(32,args.samples)
        scene.cycles.max_bounces=12
        scene.cycles.transmission_bounces=8
        scene.cycles.caustics_reflective=False
        scene.cycles.caustics_refractive=False
        camera=scene.camera
        camera.data.type='PERSP'
        camera.data.clip_start=.01
        studio=bpy.data.collections['STUDIO_RENDER_ONLY']
        poses={}
        def pose(role,angle):
            matches=[obj for obj in scene.objects if obj.get('part_role')==role and '_LOD' not in obj.name]
            if len(matches)!=1:
                raise RuntimeError('Expected one source articulation: '+role)
            pivot=matches[0]
            axis=pivot.get('rotation_axis','Y')
            pivot.rotation_euler['XYZ'.index(axis)]=math.radians(angle)
            poses[role]=angle
        if view=='engine':
            pose('hood_hinge_pivot',-65)
            camera.location=(3.5,-2.50,3.05)
            target=Vector((1.22,0,1.12))
            camera.data.lens=52
        elif view=='cockpit':
            # The real passenger door opens to give this inspection view
            # daylight from the studio; no cabin geometry is removed.
            pose('front_right_door_hinge_pivot',60)
            camera.location=(-.20,-.35,1.76)
            target=Vector((.37,.16,1.24))
            camera.data.lens=20
            for name,location,energy,size in (
                ('Cockpit fill',(-.05,-.34,1.88),12,.75),
                ('Footwell fill',(.14,-.10,1.12),2.5,.32),
            ):
                data=bpy.data.lights.new(name,'AREA')
                data.energy, data.size=energy,size
                lamp=bpy.data.objects.new(name,data)
                studio.objects.link(lamp)
                lamp.location=location
                lamp.rotation_euler=(Vector((.38,.23,1.18))-lamp.location).to_track_quat('-Z','Y').to_euler()
        elif view=='interior':
            # An explicitly labeled cutaway: remove upper exterior shell only
            # for this inspection render. The source and exported GLBs stay intact.
            for obj in bpy.data.collections['offroader_SOURCE'].objects:
                name=obj.name
                if any(token in name for token in ('roof','glass','windshield','windscreen',
                    'headliner','sun_visor','ceiling_grab','rearview_mirror','cabin_dome_light','rear_window_wiper')):
                    obj.hide_render=True
            # The engine remains in its exported geometry but is not in this framing.
            camera.location=(1.15,-2.20,4.25)
            target=Vector((-.67,.02,1.20))
            camera.data.lens=48
        else:
            for role,angle in (('front_left_door_hinge_pivot',-55),
                               ('rear_left_door_hinge_pivot',-55),
                               ('front_right_door_hinge_pivot',55),
                               ('rear_right_door_hinge_pivot',55),
                               ('tailgate_hinge_pivot',95)):
                pose(role,angle)
            camera.location=(-4.6,4.8,3.4)
            target=Vector((-.5,0,1.05))
            camera.data.lens=42
        camera.rotation_euler=(target-camera.location).to_track_quat('-Z','Y').to_euler()
        if view=='access':
            # Frame the actual open leaves and spare, not only the closed body.
            camera.data.sensor_fit='HORIZONTAL'
            bpy.context.view_layer.update()
            inverse=camera.matrix_world.inverted()
            aspect=scene.render.resolution_x/scene.render.resolution_y
            half_width=camera.data.sensor_width*.5
            candidates=[camera.data.lens]
            for obj in bpy.data.collections['offroader_SOURCE'].objects:
                if obj.type!='MESH' or obj.hide_render:
                    continue
                for corner in obj.bound_box:
                    p=inverse@(obj.matrix_world@Vector(corner))
                    if p.z<-.01:
                        candidates.extend((.90*half_width*(-p.z)/max(abs(p.x),1.e-6),
                                           .90*half_width/aspect*(-p.z)/max(abs(p.y),1.e-6)))
            camera.data.lens=min(candidates)
        filename='offroader_'+view+'.png'
        scene.render.filepath=str(directory/filename)
        print('DETAIL_VIEW', view, flush=True)
        bpy.ops.render.render(write_still=True)
        path=directory/filename
        previews.append({'path':filename,'bytes':path.stat().st_size,'sha256':digest(path)})
        provenance[filename]={
            'script':'tools/render_offroader_details.py','script_sha256':SCRIPT_SHA256,
            'source_sha256':saved['sha256'],'samples':args.samples,'resolution':args.resolution,
            'view':view,'cutaway':view=='interior',
            'denoising':scene.cycles.use_denoising,
            'articulation_degrees':poses,
        }
    names={item['path'] for item in previews}
    manifest['files']=[item for item in manifest['files'] if item['path'] not in names]+previews
    manifest_path.write_text(json.dumps(manifest,indent=2,ensure_ascii=False)+'\n')


if __name__=='__main__':
    main()
