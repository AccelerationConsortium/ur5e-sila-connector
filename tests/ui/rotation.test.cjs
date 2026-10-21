const test=require('node:test');
const assert=require('node:assert/strict');
const R=require('../../src/unitelabs/ur5e_connector/ui/static/composer-rotation.js');
const close=(a,b,t=1e-10)=>assert.ok(Math.abs(a-b)<t,`${a} != ${b}`);

test('pointer angles allow circular motion in both screen directions',()=>{
 close(R.pointerAngle({x:1,y:0},{x:0,y:0}),0);
 close(R.pointerAngle({x:0,y:-1},{x:0,y:0}),90);
 close(R.pointerAngle({x:0,y:1},{x:0,y:0}),-90);
});
test('crossing the atan2 seam does not jump 358 degrees',()=>{
 close(R.angularDelta(179,-179),2);close(R.angularDelta(-179,179),-2);
 close(R.rotate(90,179,-179,355,false,false).value,92);
});
test('multiple turns accumulate without silently wrapping joint angles',()=>{
 let raw=-250,last=0;
 for(let angle=10;angle<=400;angle+=10){const pointer=((angle+180)%360)-180;const result=R.rotate(raw,last,pointer,355,false,false);raw=result.raw;last=pointer;}
 close(raw,150);
});
test('joint limits clamp and reverse immediately without a sticky dead zone',()=>{
 const a=R.rotate(354.9,0,40,355,false,false);close(a.value,354.9);assert.equal(a.limited,true);
 const b=R.rotate(a.raw,40,39,355,false,false);close(b.value,353.9);
 close(R.rotate(-154.9,0,-20,155,false,false).value,-154.9);
});
test('fine and snap modes retain sub-degree pointer accumulation',()=>{
 close(R.rotate(0,0,10,355,true,false).value,2);
 let raw=0,value=0;
 for(let i=1;i<=8;i++){const r=R.rotate(raw,(i-1)*.2,i*.2,355,false,true);raw=r.raw;value=r.value;}
 close(raw,1.6);close(value,2);
});
test('nominal DH zero pose matches independently known flange position',()=>{
 const k=R.forwardKinematics([0,0,0,0,0,0]);assert.equal(k.points.length,7);
 [-.8172,-.2329,.0628].forEach((v,i)=>close(k.points[6][i],v));
});
test('wrist 3 changes flange orientation while its position stays fixed',()=>{
 const q=[-212,-51,-86,267,233,310],a=R.forwardKinematics(q);q[5]+=30;const b=R.forwardKinematics(q);
 a.points.forEach((p,i)=>p.forEach((v,j)=>close(v,b.points[i][j])));
 assert.ok(Math.abs(a.axes[0][0]-b.axes[0][0])>1e-4);
 for(const axis of b.axes)close(axis.reduce((s,v)=>s+v*v,0),1);
});
