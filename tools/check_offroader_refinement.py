#!/usr/bin/env python3
"""Read-only checks for integrated body surfaces, hood airway and optical layers.

Use Blender --background --python this_file -- source.blend --json receipt.json.
Tests manufactured geometry and source articulation, not engine runtime physics.
"""
from pathlib import Path
import argparse, hashlib, json, sys
import bpy, bmesh
from mathutils import Vector
sys.path.insert(0,str(Path(__file__).resolve().parent))
from check_offroader_access import geometry, descendant
p=argparse.ArgumentParser(description=__doc__)
p.add_argument('source',type=Path);p.add_argument('--json',type=Path)
a=p.parse_args(sys.argv[sys.argv.index('--')+1:])
assert bpy.app.background
source=a.source.resolve();digest=hashlib.sha256(source.read_bytes()).hexdigest()
bpy.ops.wm.open_mainfile(filepath=str(source));bpy.context.view_layer.update()
objects=list(bpy.data.collections['offroader_SOURCE'].objects)
meshes=[o for o in objects if o.type=='MESH']
report={'source_sha256':digest,'checker_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        'scope':'Source geometry: real hood/liner air opening, connected manifold nose, integral door waist, mirror mount contact, hood-parent ownership and thin unlit optical covers. No runtime test.'}
def one(token):return next(o for o in meshes if token in o.name)
def topology(obj):
    bm=bmesh.new();bm.from_mesh(obj.data)
    result={'closed_manifold':all(e.is_manifold for e in bm.edges),'volume_m3':bm.calc_volume(signed=True),'zero_area_faces':sum(f.calc_area()<1.e-12 for f in bm.faces)}
    remaining=set(bm.verts);islands=0
    while remaining:
        stack=[remaining.pop()];islands+=1
        while stack:
            v=stack.pop()
            for e in v.link_edges:
                other=e.other_vert(v)
                if other in remaining:remaining.remove(other);stack.append(other)
    result['connected_components']=islands;bm.free()
    assert result['closed_manifold'] and result['volume_m3']>0 and not result['zero_area_faces'],(obj.name,result)
    return result
for token in ('door_window_lower_rail','door_belt_moulding','quarter_lower_rail','quarter_belt_moulding'):
    assert not any(token in o.name for o in objects),('Obsolete window shelf remains',token)
report['door_waists']={}
for obj in meshes:
    if not obj.name.endswith('_door_skin'):continue
    pts=[obj.matrix_world@v.co for v in obj.data.vertices]
    shoulder=max(abs(v.y) for v in pts if 1.38<v.z<1.42)
    upper=max(abs(v.y) for v in pts if v.z>1.485)
    height=max(v.z for v in pts)
    assert 1.488<height<1.497 and .012<shoulder-upper<.040,(obj.name,height,shoulder-upper)
    report['door_waists'][obj.name]={'top_m':height,'roll_in_m':shoulder-upper,**topology(obj)}
assert len(report['door_waists'])==4
hood=one('formed_hood_shell');liner=one('hood_acoustic_liner');duct=one('hood_intake_duct')
for obj in (hood,liner,duct):
    tree=geometry(obj)[0]
    for x,y in ((1.47,0),(1.58,.10),(1.69,-.10)):
        assert tree.ray_cast(Vector((x,y,1.8)),Vector((0,0,-1)),.6)[0] is None,('Airway obstructed',obj.name,x,y)
report['hood_shell']=topology(hood);report['air_duct']=topology(duct)
hood_tree=geometry(hood)[0]
report['underside_rib_intersections']={}
for obj in meshes:
    if 'hood_inner_pressing' not in obj.name and 'hood_inner_crossbrace' not in obj.name:continue
    intersections=len(hood_tree.overlap(geometry(obj)[0]))
    report['underside_rib_intersections'][obj.name]=intersections
    assert intersections==0,('Underside reinforcement breaks through hood skin',obj.name)
assert len(report['underside_rib_intersections'])==4
center=hood_tree.ray_cast(Vector((1.20,0,1.8)),Vector((0,0,-1)))[0]
edge=hood_tree.ray_cast(Vector((1.20,.79,1.8)),Vector((0,0,-1)))[0]
assert center is not None and edge is not None
report['hood_center_above_edge_m']=center.z-edge.z
assert .075<report['hood_center_above_edge_m']<.105,report
pivot=next(o for o in objects if o.get('part_role')=='hood_hinge_pivot')
intake=[o for o in meshes if 'hood_intake_' in o.name]
assert len(intake)>=8 and all(descendant(o,pivot) for o in intake)
report['intake_moving_parts']=len(intake)
nose=next(o for o in meshes if o.get('component')=='continuous_front_and_side_body')
report['continuous_nose']=topology(nose)
assert report['continuous_nose']['connected_components']==1,report['continuous_nose']
report['mirror_contacts']={}
for side in ('left','right'):
    mount=one(side+'_mirror_mount');arm=one(side+'_mirror_arm')
    count=len(geometry(mount)[0].overlap(geometry(arm)[0]))
    assert count,('Mirror arm disconnected from mounting sail',side)
    report['mirror_contacts'][side]=count
# Optical layers must remain real thin shells, not solid luminous blocks.
report['optical_covers']={}
covers=[o for o in meshes if o.get('component') in ('thin_convex_headlamp_cover','thin_prismatic_amber_cover')]
assert len(covers)==8,('Expected two headlamp and six indicator covers',len(covers))
for obj in covers:
    tree,low,high=geometry(obj)
    axis=Vector(obj.get('optical_axis',(1,0,0)))
    center=(low+high)*.5
    first=tree.ray_cast(center+axis*.5,-axis)[0]
    assert first is not None,obj.name
    second=tree.ray_cast(first-axis*.00001,-axis)[0]
    assert second is not None,obj.name
    thickness=(first-second).length
    assert .0015<thickness<.0035,(obj.name,thickness)
    shader=obj.data.materials[0].node_tree.nodes.get('Principled BSDF')
    transmission=shader.inputs['Transmission Weight'].default_value
    emission=max(shader.inputs['Emission Color'].default_value[:3])*shader.inputs['Emission Strength'].default_value
    assert transmission>.90 and emission==0,obj.name
    report['optical_covers'][obj.name]={'center_thickness_m':thickness,'transmission':transmission,'emission':emission,**topology(obj)}
report['indicator_mount_contacts']={}
body=next(o for o in meshes if o.get('component')=='continuous_front_and_side_body')
rear_housings=[o for o in meshes if 'rear_light_housing' in o.name]
for obj in meshes:
    if obj.get('component')!='indicator_housing':continue
    targets=rear_housings if 'rear_indicator' in obj.name else [body]
    contacts=sum(len(geometry(obj)[0].overlap(geometry(t)[0])) for t in targets)
    assert contacts,('Indicator housing floats off its mounting panel',obj.name)
    report['indicator_mount_contacts'][obj.name]=contacts
assert len(report['indicator_mount_contacts'])==6
report['passed']=True
assert hashlib.sha256(source.read_bytes()).hexdigest()==digest
if a.json:a.json.write_text(json.dumps(report,indent=2)+'\n')
print(json.dumps(report,indent=2))
