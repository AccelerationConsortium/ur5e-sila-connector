/* Pure rotation and nominal geometry helpers; no DOM, storage, network or motion. */
(function (root) {
  "use strict";
  function pointerAngle(point, center) {
    return Math.atan2(center.y - point.y, point.x - center.x) * 180 / Math.PI;
  }
  function angularDelta(previous, next) {
    return ((next - previous + 540) % 360 + 360) % 360 - 180;
  }
  function rotate(current, previousPointer, nextPointer, limit, fine, snap) {
    let value = current + angularDelta(previousPointer, nextPointer) * (fine ? 0.2 : 1);
    const bounded = Math.max(-limit + 0.1, Math.min(limit - 0.1, value));
    value = snap ? Math.round(bounded) : bounded;
    value = Math.max(-limit + 0.1, Math.min(limit - 0.1, value));
    return {value, raw:bounded, limited: bounded !== current + angularDelta(previousPointer, nextPointer) * (fine ? 0.2 : 1)};
  }
  function forwardKinematics(degrees) {
    let t=Array.from({length:4},(_,i)=>Array.from({length:4},(_,j)=>+(i===j)));
    const points=[[0,0,0]], a=[0,-.425,-.3922,0,0,0], d=[.1625,0,0,.1333,.0997,.0996];
    const alpha=[Math.PI/2,0,0,Math.PI/2,-Math.PI/2,0];
    degrees.forEach((v,i)=>{
      const th=v*Math.PI/180,c=Math.cos(th),s=Math.sin(th),ca=Math.cos(alpha[i]),sa=Math.sin(alpha[i]);
      const b=[[c,-s*ca,s*sa,a[i]*c],[s,c*ca,-c*sa,a[i]*s],[0,sa,ca,d[i]],[0,0,0,1]];
      t=t.map(row=>b[0].map((_,j)=>row.reduce((sum,x,k)=>sum+x*b[k][j],0)));
      points.push(t.slice(0,3).map(row=>row[3]));
    });
    return {points, axes:[0,1,2].map(j=>t.slice(0,3).map(row=>row[j]))};
  }
  const api={pointerAngle, angularDelta, rotate, forwardKinematics};
  if(typeof module!=="undefined" && module.exports)module.exports=api;
  else root.ComposerRotation=Object.freeze(api);
})(typeof window!=="undefined" ? window : {});
