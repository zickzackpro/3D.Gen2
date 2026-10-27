#!/usr/bin/env python3
"""Read-only sampled Offroader hood and cabin clearance checks.

Run in background Blender with an editable offroader.blend as positional input
and optional --json receipt path. No source files are modified. Only named
neighboring body/engine surfaces and the seats/armrests/door cards are tested;
this is not full collision detection or human ergonomic certification.
"""
from pathlib import Path
import argparse, hashlib, json, math, sys
import bpy
from mathutils import Matrix, Vector

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'tools'))
from check_offroader_access import geometry, overlapping_bounds, descendant
p=argparse.ArgumentParser(description=__doc__)
p.add_argument('source',type=Path)
p.add_argument('--json',dest='report',type=Path)
a=p.parse_args(sys.argv[sys.argv.index('--')+1:] if '--' in sys.argv else [])
assert bpy.app.background, 'Run in a background Blender process'
a.source=a.source.resolve()
if not a.source.is_file() or a.source.suffix!='.blend':
    p.error('Expected an existing editable source .blend')
digest=hashlib.sha256(a.source.read_bytes()).hexdigest()
bpy.ops.wm.open_mainfile(filepath=str(a.source));bpy.context.view_layer.update()
objects=list(bpy.data.collections['offroader_SOURCE'].objects)
meshes=[o for o in objects if o.type=='MESH']
by_role={o.get('part_role'):o for o in objects if o.get('part_role')}
fixed={o.name:geometry(o) for o in meshes}
report={'source_sha256':digest,'checker_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        'scope':'Sampled rigid hood/body and cabin control/seat/console intersections; dimensional fit, not human ergonomic certification.',
        'hood':[],'controls':{},'measurements':{}}
failures=[]

def sweep(role,angles,targets):
    pivot=by_role[role]; original=pivot.matrix_basis.copy()
    parts=[o for o in meshes if descendant(o,pivot)]
    samples=[]
    try:
        for angle in angles:
            pivot.matrix_basis=original@Matrix.Rotation(math.radians(angle),4,'Y')
            bpy.context.view_layer.update();hits=[]
            for part in parts:
                moving=geometry(part)
                for target in targets:
                    if target in parts:continue
                    if overlapping_bounds(moving,fixed[target.name]):
                        n=len(moving[0].overlap(fixed[target.name][0]))
                        if n:hits.append({'moving':part.name,'obstacle':target.name,'face_pairs':n})
            samples.append({'degrees':angle,'intersections':hits})
            failures.extend(hits)
    finally:
        pivot.matrix_basis=original;bpy.context.view_layer.update()
    return samples

# Explicit neighboring body/engine surfaces; fixed hinge and latching mating
# faces are outside this scope. Underside ribs/liner belong to the moving hood.
hood_targets=[o for o in meshes if any(t in o.name for t in (
    'body_side','front_cowl','cowl_vent','windshield','windscreen_wiper',
    'stamped_headlamp_surround','front_panel_crossrail','engine_block','engine_head',
    'engine_cam_cover','air_filter','intake_manifold','radiator','battery'))
    and not descendant(o,by_role['hood_hinge_pivot'])]
report['hood_targets']=[o.name for o in hood_targets]
report['hood']=sweep('hood_hinge_pivot',(0,-5,-15,-35,-65,-72),hood_targets)
hood_shell=next(o for o in meshes if o.name.endswith('formed_hood_shell'))
nose=next(o for o in meshes if o.name.endswith('front_panel_crossrail'))
report['hood_nose_shut_line_m']=[]
for y in (-.60,-.30,0.,.30,.60):
    underside=fixed[hood_shell.name][0].ray_cast(Vector((2.044,y,1.30)),Vector((0,0,1)))[0]
    panel_top=fixed[nose.name][0].ray_cast(Vector((2.044,y,1.60)),Vector((0,0,-1)))[0]
    assert underside is not None and panel_top is not None, 'Missing closed hood nose surfaces'
    gap=underside.z-panel_top.z
    report['hood_nose_shut_line_m'].append(gap)
    assert .003<gap<.010, ('Hood nose shut line outside 3–10 mm',y,gap)
pedestals=[o for o in meshes if 'hood_hinge_fixed_pedestal' in o.name]
pins=[o for o in meshes if 'hood_hinge_fixed_pin' in o.name]
cowl=next(o for o in meshes if o.name.endswith('front_cowl'))
assert len(pedestals)==len(pins)==2, 'Expected two mounted hood hinges'
report['hood_mount_contacts']={}
for pedestal in pedestals:
    mount_y=(fixed[pedestal.name][1].y+fixed[pedestal.name][2].y)*.5
    pin=min(pins,key=lambda o:abs((fixed[o.name][1].y+fixed[o.name][2].y)*.5-mount_y))
    contacts={target.name:len(fixed[pedestal.name][0].overlap(fixed[target.name][0]))
              for target in (cowl,pin)}
    report['hood_mount_contacts'][pedestal.name]=contacts
    assert all(contacts.values()), ('Disconnected hood hinge mount',contacts)
seat_targets=[o for o in meshes if o.get('seat_surface_uv') or any(t in o.name for t in ('center_armrest','inner_door_card','door_armrest'))]
for role,angles in [('gear_lever_pivot',(-12,0,12)),('transfer_lever_pivot',(-12,0,12)),
                    ('handbrake_pivot',(-25,-12,0)),('accelerator_pivot',(-18,0)),
                    ('brake_pivot',(-18,0)),('clutch_pivot',(-18,0))]:
    report['controls'][role]=sweep(role,angles,seat_targets)
def one(suffix):return next(o for o in meshes if o.name.endswith(suffix))
def bounds(suffix):
    o=one(suffix);return fixed[o.name][1:]
seat=one('driver_upholstered_seat_cushion')
seat_y=(fixed[seat.name][1].y+fixed[seat.name][2].y)/2
wheel_y=by_role['cabin_steering_pivot'].matrix_world.translation.y
alignment=abs(seat_y-wheel_y)
report['measurements']['driver_seat_to_wheel_lateral_offset_m']=alignment
assert alignment<.005,alignment
pedal_y=[by_role[r].matrix_world.translation.y for r in ('accelerator_pivot','brake_pivot','clutch_pivot')]
report['measurements']['pedal_center_spacing_m']=[pedal_y[i+1]-pedal_y[i] for i in (0,1)]
assert all(.10<d<.20 for d in report['measurements']['pedal_center_spacing_m'])
report['failures']=failures;report['passed']=not failures
assert digest==hashlib.sha256(a.source.read_bytes()).hexdigest()
if a.report:
    a.report.parent.mkdir(parents=True,exist_ok=True)
    a.report.write_text(json.dumps(report,indent=2)+'\n')
print(json.dumps({'passed':not failures,'failures':failures,'measurements':report['measurements']},indent=2))
if failures:raise RuntimeError('Sampled fit checks failed')
