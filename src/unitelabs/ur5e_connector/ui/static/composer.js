"use strict";
const $ = id => document.getElementById(id);
const JOINTS = ["Base", "Shoulder", "Elbow", "Wrist 1", "Wrist 2", "Wrist 3"];
const LIMITS = [355, 355, 155, 355, 355, 355];
const KEY = "ur5e-action-composer-v1";
let token = "", state = null, check = null, plan = null, preparedAt = 0, busy = false, consumed = false, validationError = "";
let q = [0, -86, -86, 0, 86, 0], reference = null, waypoints = [], selected = -1;
let playing = false, progress = 0, last = 0, dragging = null, pollBusy = false, revision = 0, selectedJoint = 0;
const element = (tag, text) => {const e = document.createElement(tag); if (text !== undefined) e.textContent = text; return e;};
const tell = text => $("message").textContent = text;
async function api(path, body) {
  const r = await fetch(path, body === undefined ? {cache:"no-store"} : {method:"POST", headers:{"Content-Type":"application/json", "X-Panel-Token":token}, body:JSON.stringify(body)});
  const data = await r.json(); if (!r.ok) throw Error(data.error || "Request failed."); return data;
}
function saveDraft() {try {localStorage.setItem(KEY, JSON.stringify(documentData()));} catch {tell("Browser draft storage unavailable. Export JSON to save your chain.");}}
function documentData() {return {schema:"ur5e.action-chain/2", timing_mode:"automatic", units:"degrees", reference, speed:Number($("speed").value), waypoints, editor_pose:q.slice(), editor_name:$("name").value, editor_hold:Number($("hold").value), selected_joint:selectedJoint, selected_waypoint:selected,task_settings:taskSettings(),task_name:$("task-name").value,task_description:$("task-description").value,teaching:teachingRecord};}
function invalidate({persist=true,redraw=true}={}) {revision++; validationError=""; consumed=false; plan = null; playing = false; progress = 0; $("seek").disabled = true; $("play").disabled = true; $("summary").textContent = "Set motion speed and validate. Travel times are calculated automatically."; $("duration").textContent="Calculated on validation"; gate(); if(persist)saveDraft(); if(redraw)render();}
function gate() {
  const active = state?.jobs?.some(j => ["queued","running","pausing","stopping"].includes(j.status));
  const enabled = Boolean(plan && !consumed && check?.ready && check?.action_chains_enabled && (!plan.playback_controls || check?.feedback_motion_enabled) && state?.connected && !active && !busy && Date.now()-preparedAt < 175000 && (state.simulation || $("remote").checked));
  $("run").disabled = !enabled; $("run").textContent = state?.simulation ? "Run once via SiLA · simulation" : "Run once via SiLA · real robot";
  $("prepare").disabled = busy || !state?.connected || active || !waypoints.length;
  $("read").disabled = busy || !state?.connected || active;
  $("save").disabled = busy || !state?.connected || (!state.simulation && !reference);
  const reason = busy ? "Validation or dispatch is in progress." : !state?.connected ? "Device disconnected." : active ? "A run is active." : validationError ? "Validation failed. See the reason above." : !plan ? "Validate the current chain before running." : consumed ? "This preview was already dispatched. Validate again for a new run." : Date.now()-preparedAt >= 175000 ? "Preview expired. Validate again." : !check?.ready ? "Stationary preflight did not pass." : !check?.action_chains_enabled ? "Real action-chain execution is disabled." : plan.playback_controls && !check?.feedback_motion_enabled ? "Real feedback playback awaits hardware qualification; simulation is available." : !state.simulation && !$("remote").checked ? "Confirm the physical review and Remote Control before dispatch." : "Ready for an explicit one-time run.";
  $("run").title = reason;
  if ($("run-reason")) $("run-reason").textContent = reason;
  updateExecution();
}
function validPose(values) {return Array.isArray(values) && values.length===6 && values.every((v,i)=>typeof v==="number" && Number.isFinite(v) && Math.abs(v)<LIMITS[i]);}
function acceptFile(d) {
  const legacy=d?.schema==="ur5e.action-chain/1";
  if(!d || (!legacy && (d.schema!=="ur5e.action-chain/2" || d.timing_mode!=="automatic")) || d.units!=="degrees" || !Array.isArray(d.waypoints) || d.waypoints.length>24 || !Number.isFinite(d.speed) || d.speed<1 || d.speed>40 || (d.reference!==null && !validPose(d.reference))) throw Error("Invalid action-chain document.");
  if(d.editor_pose!==undefined&&!validPose(d.editor_pose))throw Error("Invalid saved editor pose.");
  if(d.task_settings!==undefined){const t=d.task_settings,finite6=v=>Array.isArray(v)&&v.length===6&&v.every(x=>typeof x==="number"&&Number.isFinite(x));if(!t||t.template!=="pickup_rehearsal"||!finite6(t.target)||!finite6(t.tcp)||![t.approach_m,t.lift_m].every(v=>typeof v==="number"&&v>=.01&&v<=.3)||typeof t.return_to_start!=="boolean")throw Error("Invalid saved task settings.");}
  const rows=d.waypoints.map(r=>{if(!r || !validPose(r.degrees) || typeof r.name!=="string" || !r.name.length || r.name.length>60 || !Number.isFinite(r.hold??0) || (r.hold??0)<0 || (r.hold??0)>10 || (legacy && (!Number.isFinite(r.duration)||r.duration<1||r.duration>60)) || (!legacy && "duration" in r))throw Error("Invalid waypoint values.");return {name:r.name,degrees:[...r.degrees],hold:r.hold??0};});
  waypoints=rows;reference=d.reference?[...d.reference]:null;$("speed").value=d.speed;selected=-1;if(rows.length)q=[...rows[0].degrees];invalidate();render();
  if(d.editor_pose!==undefined){
    q=d.editor_pose.slice();
    if(typeof d.editor_name==="string"&&d.editor_name.length<=60)$("name").value=d.editor_name;
    if(Number.isFinite(d.editor_hold)&&d.editor_hold>=0&&d.editor_hold<=10)$("hold").value=d.editor_hold;
    if(Number.isInteger(d.selected_joint)&&d.selected_joint>=0&&d.selected_joint<6)selectedJoint=d.selected_joint;
    if(Number.isInteger(d.selected_waypoint)&&d.selected_waypoint>=-1&&d.selected_waypoint<rows.length)selected=d.selected_waypoint;
    saveDraft();render();
  }
  restoreTaskSettings(d.task_settings);if(typeof d.task_name==="string")$("task-name").value=d.task_name;if(typeof d.task_description==="string")$("task-description").value=d.task_description;teachingRecord=d.teaching||null;saveDraft();
  if(legacy)tell("Saved poses preserved. Legacy travel times will be recalculated from Motion speed.");
}
const Rotation=window.ComposerRotation;
const SVG_NS="http://www.w3.org/2000/svg";
const scenes=[];
let drawRequest=0, poseEditStart=null, pointerPending=null, pointerRequest=0;
const poseHistory=[];
function svgNode(tag, attributes, parent) {
  const e=document.createElementNS(SVG_NS,tag);
  for(const [key,value] of Object.entries(attributes))e.setAttribute(key,value);
  if(parent)parent.append(e);return e;
}
function project(point,axis,x0){return {x:x0+point[axis]*190,y:365-point[2]*190};}
function createScene(id,axis,x0){
  const g=$(id), links=[], handles=svgNode("g",{},g), joints=[], labels=[];
  for(let i=0;i<6;i++)links.push(svgNode("line",{stroke:"#9dc4b3","stroke-width":8},g));
  g.append(handles);
  for(let i=0;i<6;i++){
    const circle=svgNode("circle",{r:10,fill:"#efbc70",class:"joint-handle","data-joint":i},handles);
    svgNode("title",{},circle).textContent=`J${i+1} ${JOINTS[i]}: select or drag in an arc`;
    circle.addEventListener("pointerdown",e=>startRotation(e,i,$("robot"),id));joints.push(circle);
    labels.push(svgNode("text",{class:"joint-label-svg"},g));labels[i].textContent=`J${i+1}`;
  }
  const axes=["#ee9c8e","#95d4b1","#94bce3"].map(stroke=>svgNode("line",{class:"tcp-axis",stroke},g));
  const ring=svgNode("circle",{r:48,class:"rotation-ring","data-rotation-ring":id},g);
  const grip=svgNode("circle",{r:11,class:"rotation-grip","data-rotation-grip":id},g);
  const scene={id,axis,x0,links,joints,handles,labels,axes,ring,grip,center:{x:x0,y:365}};
  ring.addEventListener("pointerdown",e=>startRotation(e,selectedJoint,$("robot"),id,scene.center));
  grip.addEventListener("pointerdown",e=>startRotation(e,selectedJoint,$("robot"),id,scene.center));
  return scene;
}
function updateDraw(){
  const rig=Rotation.forwardKinematics(q),points=rig.points;
  if(!scenes.length){scenes.push(createScene("left",0,250),createScene("right",1,750));}
  for(const scene of scenes){
    const mapped=points.map(p=>project(p,scene.axis,scene.x0));
    scene.links.forEach((line,i)=>{const a=mapped[i],b=mapped[i+1];for(const [k,v] of Object.entries({x1:a.x,y1:a.y,x2:b.x,y2:b.y}))line.setAttribute(k,v);});
    scene.joints.forEach((circle,i)=>{
      circle.setAttribute("cx",mapped[i].x);circle.setAttribute("cy",mapped[i].y);
      circle.classList.toggle("selected-axis",i===selectedJoint);
      scene.labels[i].setAttribute("x",mapped[i].x+12);scene.labels[i].setAttribute("y",mapped[i].y+(i%2?-11:17));
    });
    if(scene.handles.lastElementChild!==scene.joints[selectedJoint])scene.handles.append(scene.joints[selectedJoint]);
    const tip=points[6], origin=mapped[6];
    scene.axes.forEach((line,i)=>{const endpoint=project(tip.map((v,k)=>v+rig.axes[i][k]*.12),scene.axis,scene.x0);for(const [k,v] of Object.entries({x1:origin.x,y1:origin.y,x2:endpoint.x,y2:endpoint.y}))line.setAttribute(k,v);});
    scene.center=dragging?.view===scene.id?dragging.center:mapped[selectedJoint];
    const c=scene.center, angle=q[selectedJoint]*Math.PI/180;
    scene.ring.setAttribute("cx",c.x);scene.ring.setAttribute("cy",c.y);
    scene.grip.setAttribute("cx",c.x+48*Math.cos(angle));scene.grip.setAttribute("cy",c.y-48*Math.sin(angle));
  }
  const angle=q[selectedJoint]*Math.PI/180,x=120+84*Math.cos(angle),y=120-84*Math.sin(angle);
  $("dial-needle").setAttribute("x2",x);$("dial-needle").setAttribute("y2",y);
  $("dial-grip").setAttribute("cx",x);$("dial-grip").setAttribute("cy",y);
  $("dial-label").textContent=`J${selectedJoint+1} · ${JOINTS[selectedJoint].toUpperCase()}`;
  $("dial-value").textContent=`${q[selectedJoint].toFixed(2)}°`;
  $("rotation-title").textContent=`${JOINTS[selectedJoint]} rotation`;
  const limit=LIMITS[selectedJoint]-.1;
  $("rotation-limit").textContent=`Range −${limit}° to +${limit}° · positive travel left ${(limit-q[selectedJoint]).toFixed(1)}° · no angle wrapping`;
  const dial=$("rotation-dial");dial.setAttribute("aria-valuenow",q[selectedJoint].toFixed(2));dial.setAttribute("aria-valuemin",-limit);dial.setAttribute("aria-valuemax",limit);dial.setAttribute("aria-valuetext",`${JOINTS[selectedJoint]} ${q[selectedJoint].toFixed(2)} degrees`);
  document.querySelectorAll("#joint-selector button").forEach((e,i)=>e.setAttribute("aria-pressed",i===selectedJoint));
  document.querySelectorAll(".joint").forEach((e,i)=>{e.classList.toggle("selected-axis",i===selectedJoint);const number=e.querySelector("input[type=number]");if(document.activeElement!==number)number.value=q[i].toFixed(2);e.querySelector("input[type=range]").value=q[i];});
  $("undo-pose").disabled=!poseHistory.length || Boolean(dragging);
}
function draw(){if(!drawRequest)drawRequest=requestAnimationFrame(()=>{drawRequest=0;updateDraw();});}
function selectAxis(index){selectedJoint=index;playing=false;$("rotation-feedback").textContent=`${JOINTS[index]} selected. Drag around either ring or use the separate dial.`;draw();}
function beginPoseEdit(){if(poseEditStart)return;poseEditStart=q.slice();invalidate({persist:false,redraw:false});render();}
function endPoseEdit(){
  if(!poseEditStart)return;
  if(q.some((v,i)=>Math.abs(v-poseEditStart[i])>1e-9)){poseHistory.push(poseEditStart);if(poseHistory.length>30)poseHistory.shift();}
  poseEditStart=null;saveDraft();draw();
}
function setAngle(i,v,transient=false){
  if(!Number.isFinite(v)||Math.abs(v)>=LIMITS[i]){tell(`${JOINTS[i]} must stay inside ±${LIMITS[i]}°. No angle wrapping.`);draw();return;}
  if(Math.abs(q[i]-v)<1e-10)return;
  beginPoseEdit();selectedJoint=i;q[i]=v;revision++;
  $("rotation-feedback").textContent=`${JOINTS[i]} ${v.toFixed(2)}° · editor only`;
  draw();if(!transient)endPoseEdit();
}
function localPointer(svg,event){return new DOMPoint(event.clientX,event.clientY).matrixTransform(svg.getScreenCTM().inverse());}
function startRotation(event,index,svg,view,explicitCenter){
  if(event.button!==0||dragging)return;
  event.preventDefault();event.stopPropagation();selectAxis(index);
  const pointer=localPointer(svg,event);
  let center=explicitCenter;
  if(!center){
    const scene=scenes.find(s=>s.id===view),pivot=project(Rotation.forwardKinematics(q).points[index],scene.axis,scene.x0);
    center=Math.hypot(pointer.x-pivot.x,pointer.y-pivot.y)>18?pivot:{x:pointer.x-64,y:pointer.y};
  }
  dragging={index,svg,view,center:{x:center.x,y:center.y},pointerId:event.pointerId,last:Rotation.pointerAngle(pointer,center),raw:q[index],start:q.slice()};
  svg.setPointerCapture(event.pointerId);draw();
}
function applyPointer(){
  pointerRequest=0;if(!dragging||!pointerPending)return;
  const event=pointerPending;pointerPending=null;
  const pointer=localPointer(dragging.svg,event);
  if(Math.hypot(pointer.x-dragging.center.x,pointer.y-dragging.center.y)<12)return;
  const angle=Rotation.pointerAngle(pointer,dragging.center);
  const next=Rotation.rotate(dragging.raw,dragging.last,angle,LIMITS[dragging.index],event.shiftKey,$("rotation-snap").checked);
  dragging.last=angle;dragging.raw=next.raw;
  setAngle(dragging.index,next.value,true);
  if(drawRequest){cancelAnimationFrame(drawRequest);drawRequest=0;}updateDraw();
  if(next.limited)$("rotation-feedback").textContent=`${JOINTS[dragging.index]} reached its angle limit. Rotate back to continue; no angle wrapping.`;
}
function moveRotation(event){if(!dragging||event.pointerId!==dragging.pointerId)return;event.preventDefault();pointerPending={clientX:event.clientX,clientY:event.clientY,shiftKey:event.shiftKey};if(!pointerRequest)pointerRequest=requestAnimationFrame(applyPointer);}
function finishRotation(cancel=false){
  if(!dragging)return;if(pointerRequest)cancelAnimationFrame(pointerRequest);applyPointer();
  const previous=dragging;dragging=null;pointerPending=null;
  if(cancel){q=previous.start.slice();$("rotation-feedback").textContent="Rotation cancelled. Original editor pose restored.";}
  endPoseEdit();if(previous.svg.hasPointerCapture(previous.pointerId))previous.svg.releasePointerCapture(previous.pointerId);draw();
}
function installRotationControls(){
  JOINTS.forEach((name,i)=>{const button=element("button",`J${i+1} ${name}`);button.type="button";button.setAttribute("aria-pressed",i===selectedJoint);button.onclick=()=>selectAxis(i);$("joint-selector").append(button);});
  $("rotation-dial").addEventListener("pointerdown",e=>{const p=localPointer($("rotation-dial"),e);if(Math.hypot(p.x-120,p.y-120)>42)startRotation(e,selectedJoint,$("rotation-dial"),"dial",{x:120,y:120});});
  for(const svg of [$("robot"),$("rotation-dial")]){
    svg.addEventListener("pointermove",moveRotation);
    svg.addEventListener("pointerup",()=>finishRotation());
    svg.addEventListener("pointercancel",()=>finishRotation(true));
    svg.addEventListener("lostpointercapture",()=>{if(dragging)finishRotation();});
  }
  $("rotation-dial").addEventListener("keydown",e=>{if(!["ArrowLeft","ArrowRight"].includes(e.key))return;e.preventDefault();setAngle(selectedJoint,Math.max(-LIMITS[selectedJoint]+.1,Math.min(LIMITS[selectedJoint]-.1,q[selectedJoint]+(e.key==="ArrowRight"?1:-1)*(e.shiftKey?.1:1))));});
  document.addEventListener("keydown",e=>{if(e.key==="Escape"&&dragging){e.preventDefault();finishRotation(true);}});
  $("undo-pose").onclick=()=>{if(!poseHistory.length)return;q=poseHistory.pop();invalidate();$("rotation-feedback").textContent="Previous editor pose restored. Saved waypoints are unchanged.";};
}

function render() {
  draw();$("waypoints").replaceChildren();$("count").textContent=`${waypoints.length} / 24`;
  waypoints.forEach((row,i)=>{const li=element("li");li.className=i===selected?"selected":"";li.draggable=true;li.append(element("strong",`${i+1}. ${row.name}`),element("p",`${row.degrees.map(v=>v.toFixed(1)+"°").join(" / ")}\nTravel ${plan?.waypoints?.[i-planActionOffset]?plan.waypoints[i-planActionOffset].duration.toFixed(3)+"s (auto)":"automatic"} · hold ${row.hold}s`));
    const action=(label,fn)=>{const b=element("button",label);b.onclick=()=>{if(runningJob()||i<editableStart())return;fn();};li.append(b);};
    action("Edit",()=>{selected=i;q=[...row.degrees];$("name").value=row.name;$("duration").textContent=plan?.waypoints?.[i-planActionOffset]?`${plan.waypoints[i-planActionOffset].duration.toFixed(3)} s (automatic)`:"Calculated on validation";$("hold").value=row.hold;playing=false;render();});
    action("↑",()=>move(i,i-1));action("↓",()=>move(i,i+1));action("Duplicate",()=>{if(waypoints.length>=24)return tell("Maximum 24 waypoints.");waypoints.splice(i+1,0,structuredClone(row));invalidate();render();});action("Delete",()=>{waypoints.splice(i,1);selected=-1;invalidate();render();});
    li.ondragstart=e=>e.dataTransfer.setData("text/plain",String(i));li.ondragover=e=>e.preventDefault();li.ondrop=e=>{e.preventDefault();const from=Number(e.dataTransfer.getData("text/plain"));if(Number.isInteger(from)&&from>=0&&from<waypoints.length)move(from,i);};$("waypoints").append(li);
  });$("update").disabled=selected<0;$("return").disabled=!reference;gate();
}
function move(from,to){if(runningJob()||from<editableStart()||to<editableStart()||to<0||to>=waypoints.length)return;const [r]=waypoints.splice(from,1);waypoints.splice(to,0,r);selected=to;invalidate();render();}
function currentRow(){const row={name:$("name").value.trim(),degrees:[...q],hold:Number($("hold").value)};if(!row.name||!validPose(row.degrees)||row.hold<0||row.hold>10||!Number.isFinite(row.hold))throw Error("Enter a name, valid angles and hold 0–10s.");return row;}
async function capture(){busy=true;gate();try{check=await api("/api/motion/check",{});if(!check.ready)throw Error("Fresh stationary preflight failed.");q=check.positions.map(v=>v*180/Math.PI);if(!reference)reference=[...q];invalidate();render();tell("Current pose read through SiLA. Editing does not move the robot.");}catch(e){tell(e.message);}finally{busy=false;gate();}}
async function prepare(){
  busy=true;invalidate();const version=revision;const paused=executionJob();const offset=paused?.status==="paused"?completedCount(paused):0;resumeJobId=paused?.status==="paused"?paused.id:null;const authored=structuredClone(waypoints.slice(offset));const speed=Number($("speed").value);gate();
  try{
    check=await api("/api/motion/check",{});if(!check.ready)throw Error("Preflight failed.");
    const result=await api("/api/action-chain/preview",{speed,waypoints:authored,timing_mode:"automatic",resume_job_id:resumeJobId});
    if(revision!==version)throw Error("Editor changed during validation. Validate again.");
    plan=result;planActionOffset=offset;preparedAt=Date.now();
    $("summary").textContent=`Automatic timing · ${plan.waypoints.length} waypoints · ${plan.duration.toFixed(2)} controller seconds\nMotion speed cap ${plan.speed}°/s · predicted peak ${plan.peak_speed.toFixed(2)}°/s · acceleration ${plan.peak_acceleration.toFixed(2)}°/s²\n${plan.waypoints.map((r,i)=>`${i+1}. ${r.name}: travel ${r.duration.toFixed(3)}s · hold ${r.hold}s`).join("\n")}\n${plan.ranges.map(r=>`${r.joint}: ${r.min.toFixed(1)}° … ${r.max.toFixed(1)}°`).join("\n")}\n${plan.validation}\nPreview expires in 180 seconds. ${check.action_chains_enabled&&(!plan.playback_controls||check.feedback_motion_enabled)?"Execution gate enabled.":"Real feedback playback awaits hardware qualification."}`;
    $("play").disabled=false;$("seek").disabled=false;progress=0;render();showProgress();
    if(selected>=planActionOffset&&selected-planActionOffset<plan.waypoints.length)$("duration").textContent=`${plan.waypoints[selected-planActionOffset].duration.toFixed(3)} s (automatic)`;
    else $("duration").textContent=`Total ${plan.duration.toFixed(2)} s; see each waypoint`;
    tell("Travel times calculated and checked through SiLA. All positions and hold times preserved.");
  }catch(e){plan=null;validationError=e.message;$("summary").textContent="Validation failed:\n"+e.message+"\nNo positions, order or holds changed. Motion has not been sent.";tell(e.message);}
  finally{busy=false;gate();}
}
function showProgress(){if(!plan)return;const samples=plan.preview_samples,t=progress*plan.duration;let i=samples.findIndex(s=>s.t>=t);if(i<0)i=samples.length-1;const b=samples[i],a=samples[Math.max(0,i-1)],u=b.t===a.t?0:(t-a.t)/(b.t-a.t);q=b.degrees.map((v,j)=>a.degrees[j]+(v-a.degrees[j])*u);$("seek").value=progress;draw();}
async function run(){if($("run").disabled)return;busy=true;playing=false;gate();const reviewed=plan;consumed=true;try{const job=await api("/api/motion/run",{plan_id:reviewed.id,run_id:crypto.randomUUID(),remote_confirmed:$("remote").checked,task_context:{name:$("task-name").value,instructions:$("task-description").value,settings:taskSettings(),teaching:teachingRecord}});$("job").textContent=`${job.id}\n${job.status}`;trackRun(job.id);state.jobs=[job,...(state.jobs||[]).filter(j=>j.id!==job.id)];tell("Dispatched once through SiLA. No automatic replay. Observe the pendant.");}catch(e){tell(e.message+" Inspect run history before attempting any new execution.");}finally{busy=false;gate();}}
async function refresh(){if(pollBusy)return;pollBusy=true;try{state=await api("/api/status");$("mode").textContent=state.connected?(state.simulation?"SIMULATION · NO PHYSICAL MOVEMENT":"REAL ROBOT · EDITING ONLY UNTIL RUN"):"DISCONNECTED";const job=state.jobs?.[0];if(job)$("job").textContent=`${job.id}\n${job.status}${job.error?"\n"+job.error:""}`;gate();}catch(e){state=null;gate();tell(e.message);}finally{pollBusy=false;}}
// Task authoring is local until a read-only SiLA solve; playback remains an explicit separate action.
let teachingRecord=null, resumeJobId=null, taskRevision=0, planActionOffset=0, trackedRunId=null;
try{trackedRunId=sessionStorage.getItem("ur5e-composer-last-run");}catch{}
function trackRun(id){trackedRunId=id;try{if(id)sessionStorage.setItem("ur5e-composer-last-run",id);else sessionStorage.removeItem("ur5e-composer-last-run");}catch{}}
const TCP_LABELS=["X (mm)","Y (mm)","Z (mm)","RX (rad)","RY (rad)","RZ (rad)"];
function executionJob(){return state?.jobs?.find(j=>j.kind==="action_chain"&&["queued","running","pausing","stopping","paused"].includes(j.status)) || state?.jobs?.find(j=>j.kind==="action_chain"&&j.id===trackedRunId);}
function runningJob(){return state?.jobs?.find(j=>["queued","running","pausing","stopping"].includes(j.status));}
function completedCount(job){return (job?.action_offset||0)+(job?.progress?.completed_actions||0);}
function editableStart(){const j=executionJob();return j?.status==="paused"?completedCount(j):0;}
function rowEqual(a,b){return Boolean(a&&b&&a.name===b.name&&a.hold===b.hold&&a.degrees.every((v,i)=>Math.abs(v-b.degrees[i])<1e-7));}
function taskSettings(){return {template:"pickup_rehearsal",target:tcpValues(false),tcp:tcpValues(true),approach_m:Number($("task-approach").value)/1000,lift_m:Number($("task-lift").value)/1000,return_to_start:$("task-return").checked};}
function restoreTaskSettings(settings){if(!settings)return;setTcpFields(settings.target||[0,0,0,0,0,0],false);setTcpFields(settings.tcp||[0,0,0,0,0,0],true);$("task-approach").value=(settings.approach_m??.06)*1000;$("task-lift").value=(settings.lift_m??.08)*1000;$("task-return").checked=settings.return_to_start!==false;}
function tcpValues(offset){return Array.from({length:6},(_,i)=>Number($(`${offset?"offset":"target"}-${i}`).value)/(i<3&&!offset?1000:1));}
function setTcpFields(values,offset){values.forEach((v,i)=>$(`${offset?"offset":"target"}-${i}`).value=(i<3&&!offset?v*1000:v).toFixed(i<3&&!offset?2:5));}
async function solveTcp(operation,extra={}){return api("/api/kinematics",{operation,seed:q.map(v=>v*Math.PI/180),tcp:tcpValues(true),...extra});}
async function editorTcp(){try{const result=await solveTcp("forward");setTcpFields(result.tcp_pose,false);$("tcp-report").textContent="Nominal TCP read from the editor pose. This does not read or move the robot.";saveDraft();}catch(e){$("tcp-report").textContent=e.message;}}
async function solveTarget(){if(runningJob())return;busy=true;gate();const version=revision,target=tcpValues(false),tool=tcpValues(true);try{const result=await solveTcp("inverse",{target});if(version!==revision||JSON.stringify(tool)!==JSON.stringify(tcpValues(true)))throw Error("Editor changed during IK. Solve again.");q=result.degrees;invalidate();$("tcp-report").textContent=`Nominal solution · residual ${(result.position_error_m*1000).toFixed(3)} mm / ${(result.orientation_error_rad*180/Math.PI).toFixed(3)}° · ${result.iterations} iterations. Add or update a waypoint, then validate the whole chain.`;}catch(e){$("tcp-report").textContent=e.message;}finally{busy=false;gate();}}
async function teachTarget(){if(runningJob())return;busy=true;gate();try{const record=await api("/api/positions",{name:$("task-name").value.trim()||"TCP teaching"});teachingRecord=record;q=record.joints.map(v=>v*180/Math.PI);if(!reference)reference=q.slice();const result=await solveTcp("forward");setTcpFields(result.tcp_pose,false);invalidate();$("tcp-report").textContent=`Stationary ${record.simulation?"simulated":"measured"} joints saved. Target uses nominal FK with your virtual TCP; the controller TCP observation is retained separately in the teaching record.`;}catch(e){$("tcp-report").textContent=e.message;}finally{busy=false;gate();}}
async function generateTask(){if(runningJob())return;busy=true;gate();const version=revision,settings=taskSettings();try{const result=await solveTcp("task",{settings});if(version!==revision||JSON.stringify(settings)!==JSON.stringify(taskSettings()))throw Error("Inputs changed during generation. Generate again.");const prefix=editableStart();if(!prefix&&executionJob()?.status!=="paused")trackRun(null);if(prefix+result.waypoints.length>24)throw Error("Combined chain exceeds 24 nodes. Reduce the task height or save a shorter task.");waypoints=[...waypoints.slice(0,prefix),...result.waypoints];selected=-1;invalidate();render();$("tcp-report").textContent=result.note+" Review all generated nodes, then validate via SiLA. No movement sent.";}catch(e){$("tcp-report").textContent=e.message;}finally{busy=false;gate();}}
async function controlRun(action){const job=executionJob();if(!job)return;try{const result=await api("/api/motion/control",{run_id:job.id,action});$("execution-status").textContent=result.message||`${action==="stop"?"Stop":"Boundary pause"} requested. Awaiting controller verification.`;if(action==="stop")resumeJobId=null;await refresh();}catch(e){$("execution-status").textContent=e.message+" Use the pendant if an immediate stop is needed.";}}
async function validateRemainder(){const job=executionJob();if(job?.status!=="paused")return;resumeJobId=job.id;await prepare();}
function updateExecution(){
 const job=executionJob(),active=runningJob(),pause=job?.status==="paused";
 $("stop-all").disabled=!state?.playback_available||!(active||pause);
 $("pause-after").disabled=!state?.playback_available||!active||!active.plan_supports_pause||["pausing","stopping"].includes(active.status);
 $("continue-chain").disabled=!pause||busy;$("episode-export").disabled=!job||Boolean(active);
 if(job){const report=job.progress||{},offset=job.action_offset||0,stale=active&&Date.now()/1000-(state.motion_progress?.request_id===job.id?state.motion_progress.observed_at:Date.parse(job.created_at)/1000)>3;
   const text=!job.authored_waypoints?.length?`Legacy run: ${job.status}. Per-action progress was not recorded.`:stale?"Progress unavailable — last state is not current. Use the pendant if needed.":`${job.status.toUpperCase()} · ${completedCount(job)} / ${job.action_count||job.authored_waypoints?.length||0} actions complete${report.current_action!=null?` · Action ${offset+report.current_action+1} active`:""}${report.pause_after_action!=null?` · braking to pause after action ${offset+report.pause_after_action+1}`:""}${pause?" · Edit remaining actions or validate them unchanged, then Run once.":""}`;
   $("execution-status").textContent=text;
   document.querySelectorAll("#waypoints>li").forEach((li,i)=>{
     li.classList.remove("action-running","action-completed","action-paused","action-unknown");
     let marker=li.querySelector(".action-signal");if(!marker){marker=element("span");marker.className="action-signal";li.prepend(marker);}let label="Pending",css="";
     const matching=rowEqual(waypoints[i],job.authored_waypoints?.[i]);
     if(matching&&i<completedCount(job)){label="Completed";css="action-completed";}
     else if(matching&&i===offset+report.current_action&&report.current_action!=null&&active){label=stale?"Unknown":"Running";css=stale?"action-unknown":"action-running";}
     else if(pause&&i===completedCount(job)){label="Paused before";css="action-paused";}
     else if(job.status==="stopped"&&matching){label="Cancelled";}
     else if(["attention_required","interrupted"].includes(job.status)&&matching){label="Unknown";css="action-unknown";}
     marker.textContent=label;if(css)li.classList.add(css);
     li.querySelectorAll("button").forEach(b=>b.disabled=Boolean(active)||(pause&&i<completedCount(job)));
     li.draggable=!active&&!pause;
   });
 }
 ["clear","import","task-load"].forEach(id=>$(id).disabled=Boolean(active)||pause);
 ["tcp-solve","task-generate","teach-target"].forEach(id=>$(id).disabled=Boolean(active)||busy||!state?.kinematics_available);
 $("return").disabled=Boolean(active)||!reference||waypoints.length>=24;
 $("update").disabled=Boolean(active)||selected<editableStart();
 if(active){["save","return","update"].forEach(id=>$(id).disabled=true);}
 if(!job)$("execution-status").textContent="Continuous by default. Pause and Stop are optional.";
 const signature=JSON.stringify(state?.task_library||[]);if(signature!==$("task-library").dataset.signature){const chosen=$("task-library").value;$("task-library").replaceChildren(element("option","Select a task"));$("task-library").firstChild.value="";(state?.task_library||[]).forEach(t=>{const o=element("option",`${t.name} · ${t.simulation?"simulation":"real teaching"}`);o.value=t.id;$("task-library").append(o);});$("task-library").value=chosen;$("task-library").dataset.signature=signature;}
}
async function saveTaskRecord(){try{const record=await api("/api/tasks",{name:$("task-name").value.trim(),description:$("task-description").value,document:documentData(),teaching:teachingRecord});tell(`Task saved: ${record.name}. Loading it will require fresh validation.`);await refresh();}catch(e){tell(e.message);}}
async function loadTaskRecord(){if(runningJob()||executionJob()?.status==="paused")return;const id=$("task-library").value;if(!id)return;try{const record=await api(`/api/tasks/${encodeURIComponent(id)}`);trackRun(null);acceptFile(record.document);teachingRecord=record.teaching;$("task-name").value=record.name;$("task-description").value=record.description||"";tell("Saved task loaded for editing only. Confirm the physical target has not moved and validate from the current pose.");}catch(e){tell(e.message);}}
function downloadJson(data,name){const url=URL.createObjectURL(new Blob([JSON.stringify(data,null,2)],{type:"application/json"}));const a=element("a");a.href=url;a.download=name;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}
async function exportEpisode(){const job=executionJob();if(!job)return;try{downloadJson(await api(`/api/datasets/${encodeURIComponent(job.id)}`),`ur5e-episode-${job.id}.json`);}catch(e){tell(e.message);}}
function installTaskControls(){
 [false,true].forEach(offset=>TCP_LABELS.forEach((name,i)=>{const label=element("label",offset?["X (m)","Y (m)","Z (m)","RX (rad)","RY (rad)","RZ (rad)"][i]:name);const input=element("input");input.id=`${offset?"offset":"target"}-${i}`;input.type="number";input.step=offset||i>=3?".001":"1";input.value="0";input.setAttribute("aria-label",`${offset?"TCP offset":"TCP target"} ${name}`);input.oninput=()=>{taskRevision++;saveDraft();};label.append(input);$(offset?"tcp-offset-fields":"tcp-target-fields").append(label);}));
 $("tcp-from-editor").onclick=editorTcp;$("tcp-solve").onclick=solveTarget;$("teach-target").onclick=teachTarget;$("task-generate").onclick=generateTask;$("stop-all").onclick=()=>controlRun("stop");$("pause-after").onclick=()=>controlRun("pause_after_action");$("continue-chain").onclick=validateRemainder;$("task-save").onclick=saveTaskRecord;$("task-load").onclick=loadTaskRecord;$("episode-export").onclick=exportEpisode;
 ["task-approach","task-lift","task-return","task-name","task-description"].forEach(id=>$(id).onchange=saveDraft);
}
installTaskControls();

const runReason=element("p","Validate the current chain before running.");runReason.id="run-reason";runReason.className="muted";runReason.setAttribute("role","status");$("run").after(runReason);
JOINTS.forEach((name,i)=>{const box=element("label");box.className="joint";box.append(element("span",`${i+1} / ${name} (°)`));const number=element("input"),slider=element("input");number.type="number";number.step=".1";slider.type="range";slider.step=".1";[number,slider].forEach(input=>{input.min=-LIMITS[i]+.1;input.max=LIMITS[i]-.1;input.setAttribute("aria-label",`${name} degrees`);input[input===number?"onchange":"oninput"]=()=>setAngle(i,parseFloat(input.value),input===slider);if(input===slider){input.onchange=endPoseEdit;input.onblur=endPoseEdit;}box.append(input);});$("joint-controls").append(box);});
installRotationControls();
$("read").onclick=capture;$("save").onclick=()=>{try{if(waypoints.length>=24)throw Error("Maximum 24 waypoints.");waypoints.push(currentRow());selected=waypoints.length-1;invalidate();render();$("name").value=`Waypoint ${waypoints.length+1}`;}catch(e){tell(e.message);}};$("update").onclick=()=>{try{if(selected<0)return;waypoints[selected]=currentRow();invalidate();render();}catch(e){tell(e.message);}};$("return").onclick=()=>{if(!reference||waypoints.length>=24)return;waypoints.push({name:"Return to reference",degrees:[...reference],hold:0});invalidate();render();};$("clear").onclick=()=>{if(runningJob()||executionJob()?.status==="paused")return;resumeJobId=null;trackRun(null);waypoints=[];selected=-1;invalidate();render();};["speed","name","hold"].forEach(id=>$(id).oninput=invalidate);$("remote").onchange=gate;$("prepare").onclick=prepare;$("run").onclick=run;$("play").onclick=()=>{if(!plan)return;if(progress>=1)progress=0;playing=!playing;};$("seek").oninput=e=>{playing=false;progress=Number(e.target.value);showProgress();};
$("export").onclick=()=>{const url=URL.createObjectURL(new Blob([JSON.stringify(documentData(),null,2)],{type:"application/json"}));const a=element("a");a.href=url;a.download="ur5e-action-chain.json";a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);};$("import").onchange=async e=>{try{if(runningJob()||executionJob()?.status==="paused")throw Error("Stop the active or paused run before importing another chain.");const f=e.target.files[0];if(!f)return;if(f.size>32768)throw Error("File must be at most 32 KiB.");trackRun(null);acceptFile(JSON.parse(await f.text()));tell("Imported for editing only. Validate again through SiLA.");}catch(error){tell(error.message);}finally{e.target.value="";}};
function tick(now){if(playing&&plan){progress=Math.min(1,progress+(last?(now-last)/1000/plan.duration:0));showProgress();if(progress===1)playing=false;}last=now;requestAnimationFrame(tick);}requestAnimationFrame(tick);
(async()=>{try{const saved=localStorage.getItem(KEY);if(saved)acceptFile(JSON.parse(saved));}catch{tell("Saved browser draft could not be restored. Import an exported file instead.");}render();try{token=(await api("/api/session")).token;await refresh();if(state?.connected&&!reference&&!waypoints.length)await capture();tell("Ready. Set Motion speed and save poses; travel times are automatic.");}catch(e){tell(e.message);}setInterval(refresh,500);})();

if (document.body.classList.contains("embedded") && window.parent !== window) {
 let lastHeight = 0;
 const reportHeight = () => {
  const height = Math.ceil(document.body.getBoundingClientRect().height);
  if (height !== lastHeight) { lastHeight = height; window.parent.postMessage({type:"composer-height",height},location.origin); }
 };
 new ResizeObserver(reportHeight).observe(document.body);
 reportHeight();
}
