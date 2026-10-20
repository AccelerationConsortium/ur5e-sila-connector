"use strict";
const JOINTS = ["Base", "Shoulder", "Elbow", "Wrist1", "Wrist2", "Wrist3"];
const $ = id => document.getElementById(id);
let token = "", state = null, preview = null, submitting = false, refreshing = false, positionSignature = "";
let routines = [];
let rows = [{joint: "Wrist1", delta: 5}, {joint: "Wrist1", delta: -5}];
const pretty = name => name.replace("Wrist", "Wrist ");
const degrees = rad => rad * 180 / Math.PI;
const fixed = (number, digits = 2) => Number.isFinite(number) ? number.toFixed(digits) : "—";
const node = (tag, className, text) => { const element = document.createElement(tag); if (className) element.className = className; if (text !== undefined) element.textContent = text; return element; };
function toast(message) { $("toast").textContent = message; $("toast").hidden = false; clearTimeout(toast.timer); toast.timer = setTimeout(() => $("toast").hidden = true, 6000); }
async function api(path, payload) {
  const response = await fetch(path, payload === undefined ? {cache: "no-store"} : {method: "POST", headers: {"Content-Type": "application/json", "X-Panel-Token": token}, body: JSON.stringify(payload)});
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || `Request failed (${response.status}).`);
  return data;
}
function activeJob() { return state?.jobs?.find(job => ["running", "queued"].includes(job.status)); }
function controlsAvailable() { return Boolean(state?.connected && state.MotionControlsEnabled && state.SafetyStatus === "NORMAL" && state.ProgramState === "STOPPED" && !state.ControlOutcomeUnknown && !activeJob() && !submitting); }
function selectView(view) {
  document.querySelectorAll(".view").forEach(element => element.hidden = element.id !== `view-${view}`);
  document.querySelectorAll(".nav").forEach(element => element.classList.toggle("active", element.dataset.view === view));
  const title = {control: "Live control", sequences: "Sequences", positions: "Saved positions"}[view];
  $("page-title").replaceChildren(document.createTextNode(title), node("span", "amber", "."));
}
function buildJoints() {
  JOINTS.forEach((joint, index) => {
    const card = node("article", "joint-card");
    const header = node("div", "joint-label"); header.append(node("span", "", pretty(joint)), node("span", "joint-number", `J${index + 1}`));
    const angle = node("div", "angle"); angle.append(node("span", "angle-number", "—"), node("small", "", "°"));
    const track = node("div", "joint-track"); track.append(node("span", "joint-pointer"));
    const buttons = node("div", "jog-buttons");
    [-1, 1].forEach(direction => { const button = node("button", "jog-button"); button.dataset.direction = direction; button.dataset.index = index; button.disabled = true; button.addEventListener("click", () => jog(joint, direction)); buttons.append(button); });
    card.append(header, angle, track, buttons); $("joint-grid").append(card);
  });
}
function drawJoints() {
  const step = Number($("jog-step").value);
  document.querySelectorAll(".joint-card").forEach((card, index) => {
    const value = state?.connected ? degrees(state.JointPositions[index]) : NaN;
    card.querySelector(".angle-number").textContent = fixed(value);
    const limit = index === 2 ? 160 : 360;
    card.querySelector(".joint-pointer").style.left = `${Math.min(99, Math.max(0, (value + limit) / (2 * limit) * 100)) || 50}%`;
    card.querySelectorAll(".jog-button").forEach(button => {
      const direction = Number(button.dataset.direction); button.textContent = `${direction > 0 ? "+" : "−"}${step}°`;
      button.setAttribute("aria-label", `Move ${pretty(JOINTS[index])} ${direction * step} degrees`);
      button.disabled = !controlsAvailable();
    });
  });
}
function speedChanged() {
  $("speed-value").textContent = $("speed").value;
  const fraction = state?.connected ? state.SpeedSliderFraction : NaN;
  $("pendant-speed").textContent = Number.isFinite(fraction) ? `${Math.round(fraction * 100)}%` : "—";
  $("effective-speed").textContent = Number.isFinite(fraction) ? `≤ ${fixed(Number($("speed").value) * fraction, 1)}°/s` : "—";
}
function clearPreview() {
  preview = null; $("preview-label").textContent = "NO PREVIEW"; $("preview-title").textContent = "Ready when you are.";
  $("preview-description").textContent = "Build and preview a sequence to inspect its targets."; $("preview-target").replaceChildren(); $("run-preview").disabled = true;
}
function showPreview(plan) {
  preview = plan; $("preview-label").textContent = "PREVIEW READY";
  $("preview-title").textContent = `${plan.steps.length} moves · one run`;
  $("preview-description").textContent = `Command speed ${plan.speed}°/s. ${plan.travel_seconds_minimum ? "Travel alone ≥ " + Math.ceil(plan.travel_seconds_minimum) + " s; acceleration, stops and verification add time. " : "Pendant speed is zero. "}Targets and excursion ranges below. Preview expires after 5 minutes.`;
  $("preview-target").replaceChildren();
  JOINTS.forEach((joint, index) => { const row = node("div"); row.append(node("span", "", pretty(joint)), node("strong", "", `${fixed(plan.target_degrees[index])}° · [${fixed(plan.ranges_degrees[index].min, 1)}, ${fixed(plan.ranges_degrees[index].max, 1)}]`)); $("preview-target").append(row); });
  $("run-preview").disabled = !controlsAvailable(); $("run-note").textContent = plan.simulation ? "This run uses the simulated device." : "Run once will move the real robot.";
}
function renderRows() {
  $("sequence-rows").replaceChildren();
  rows.forEach((row, index) => {
    const element = node("div", "sequence-row"); element.append(node("span", "", String(index + 1).padStart(2, "0"))); element.title = row.phrase || "Custom sequence";
    if (row.position_id) {
      const position = state?.positions?.find(p => p.id === row.position_id);
      element.append(node("span", "", position ? `Pose · ${position.name}` : "Saved pose"), node("span", "", "Taught target"));
    } else {
      const select = node("select"); select.setAttribute("aria-label", `Joint for move ${index + 1}`);
      JOINTS.forEach(joint => { const option = node("option", "", pretty(joint)); option.value = joint; select.append(option); }); select.value = row.joint;
      select.addEventListener("change", () => { row.joint = select.value; clearPreview(); });
      const input = node("input"); input.type = "number"; input.min = "-90"; input.max = "90"; input.step = "0.5"; input.value = row.delta;
      input.setAttribute("aria-label", `Relative angle for move ${index + 1}`);
      input.addEventListener("input", () => { row.delta = Number(input.value); clearPreview(); }); element.append(select, input);
    }
    const remove = node("button", "remove-row", "×"); remove.setAttribute("aria-label", `Remove move ${index + 1}`); remove.addEventListener("click", () => { rows.splice(index, 1); clearPreview(); renderRows(); });
    element.append(remove); $("sequence-rows").append(element);
  });
  $("row-count").textContent = `${rows.length} sequence rows`;
}
function loadRecipe(type) {
  if (type === "sweep") rows = [{joint: "Wrist1", delta: 10}, {joint: "Wrist1", delta: -10}];
  if (type === "gentle") rows = JOINTS.flatMap(joint => [{joint, delta: 5}, {joint, delta: -5}]);
  if (type === "demo") rows = JOINTS.flatMap((joint, index) => [{joint, delta: [20, -15, 20, 30, 30, 30][index]}, {joint, delta: [-20, 15, -20, -30, -30, -30][index]}]);
  clearPreview(); renderRows(); toast("Routine loaded. Preview it before running.");
}
async function previewRows(inputRows = rows) {
  const plan = await api("/api/preview", {rows: inputRows, speed: Number($("speed").value)}); showPreview(plan); return plan;
}
async function runPlan(plan) {
  const runId = crypto.randomUUID();
  localStorage.setItem("ur5e-last-run", runId);
  await api("/api/runs", {preview_id: plan.id, run_id: runId});
  clearPreview(); toast(plan.simulation ? "Simulation run started." : "Robot run started.");
}
async function action(callback) {
  if (submitting) return; submitting = true; drawJoints(); $("run-preview").disabled = true;
  try { await callback(); } catch (error) { toast(error.message); }
  finally { submitting = false; await refresh(); }
}
async function jog(joint, direction) { const amount = direction * Number($("jog-step").value); await action(async () => { const plan = await previewRows([{joint, delta: amount}]); await runPlan(plan); }); }
function drawPositions() {
  const positions = state?.positions || []; const signature = JSON.stringify(positions);
  if (signature === positionSignature) return; positionSignature = signature;
  $("position-empty").hidden = positions.length > 0; $("position-list").replaceChildren();
  for (const id of ["approach-position", "target-position"]) {
    const select = $(id), selected = select.value; select.replaceChildren(); const blank = node("option", "", positions.length ? "Select a taught pose" : "Record a position first"); blank.value = ""; select.append(blank);
    positions.forEach(position => { const option = node("option", "", position.name); option.value = position.id; select.append(option); }); select.value = selected;
  }
  positions.forEach(position => {
    const card = node("article", "position-card"); card.append(node("h3", "", position.name));
    const p = position.tool_pose; card.append(node("p", "", `X ${fixed(p[0] * 1000, 1)} · Y ${fixed(p[1] * 1000, 1)} · Z ${fixed(p[2] * 1000, 1)} mm`));
    const button = node("button", "outline small", "Add to sequence →"); button.addEventListener("click", () => { rows.push({position_id: position.id}); clearPreview(); renderRows(); selectView("sequences"); }); card.append(button); $("position-list").append(card);
  });
}
function drawStatus() {
  const connected = state?.connected;
  $("connection-dot").classList.toggle("online", Boolean(connected)); $("connection-text").textContent = connected ? "Connected" : "Disconnected";
  $("backend-mode").textContent = state?.simulation ? "SIMULATED DEVICE" : "REAL ROBOT"; $("backend-mode").className = "pill real";
  $("safety-state").textContent = connected ? state.SafetyStatus : "UNAVAILABLE"; $("safety-state").className = `pill ${connected && state.SafetyStatus === "NORMAL" ? "good" : "bad"}`;
  $("program-state").textContent = connected ? state.ProgramState : "—"; $("version").textContent = connected ? state.SoftwareVersion : "—"; $("robot-mode").textContent = connected ? state.RobotMode : "—";
  $("last-read").textContent = connected ? new Date(state.updated_at).toLocaleTimeString("en-GB") : "—";
  $("endpoint").textContent = `127.0.0.1:${state?.control_port || "—"}`;
  const error = !connected ? state?.error || "Waiting for live robot status." : state.ControlOutcomeUnknown ? "A previous control result is uncertain. Inspect the controller before continuing." : !state.MotionControlsEnabled ? "Motion controls are disabled in the connector configuration." : "";
  $("error-banner").hidden = !error; $("error-banner").textContent = error;
  ["x", "y", "z"].forEach((axis, index) => $("tcp-" + axis).textContent = connected ? fixed(state.ToolPose[index] * 1000, 1) : "—");
  $("tcp-orientation").textContent = connected ? `RX ${fixed(state.ToolPose[3], 3)}   RY ${fixed(state.ToolPose[4], 3)}   RZ ${fixed(state.ToolPose[5], 3)} rad${state.simulation ? " · simulated pose" : ""}` : "RX —   RY —   RZ — rad";
  const job = activeJob() || state?.jobs?.[0];
  $("run-state").textContent = job ? `${activeJob() ? "" : "Last run · "}${job.status.replaceAll("_", " ")}` : "Idle";
  $("run-progress-text").textContent = job ? `${job.completed} / ${job.total}` : "0 / 0";
  $("run-progress").style.width = `${job ? job.completed / job.total * 100 : 0}%`;
  $("current-move").textContent = job?.current ? `${job.current.phrase || "Move"} · ${pretty(job.current.joint)} ${job.current.delta > 0 ? "+" : ""}${fixed(job.current.delta, 1)}°` : job?.error ? `Previous run: ${job.error}. No active sequence.` : "No active sequence.";
  $("cancel-run").disabled = !activeJob(); $("record-position").disabled = !connected || Boolean(activeJob()) || submitting || state.ProgramState !== "STOPPED";
  $("preview-sequence").disabled = !connected || Boolean(activeJob()) || submitting || !rows.length;
  $("run-preview").disabled = !preview || !controlsAvailable();
  $("activity-list").replaceChildren();
  if (!state?.jobs?.length) $("activity-list").append(node("p", "muted", "No panel runs yet."));
  else state.jobs.slice(0, 5).forEach(item => { const row = node("div", "activity-row"); row.append(node("span", "", new Date(item.created_at).toLocaleTimeString("en-GB")), node("span", "", `${item.completed}/${item.total} moves · ${item.speed}°/s`), node("span", item.status === "completed" ? "success" : item.status === "attention_required" ? "attention" : "", item.status.replaceAll("_", " "))); $("activity-list").append(row); });
  drawJoints(); drawPositions(); speedChanged();
  $("flow-run").disabled = !flowAvailable();
  if (activeJob()?.kind) $("cancel-run").disabled = true;
}
async function refresh() {
  if (refreshing) return; refreshing = true;
  try { state = await api("/api/status"); } catch (error) { state = {...state, connected: false, error: error.message}; }
  finally { refreshing = false; drawStatus(); }
}
buildJoints(); renderRows(); drawJoints();
document.querySelectorAll(".nav").forEach(button => button.addEventListener("click", () => selectView(button.dataset.view)));
document.querySelectorAll("[data-recipe]").forEach(button => button.addEventListener("click", () => loadRecipe(button.dataset.recipe)));
$("open-sequences").addEventListener("click", () => selectView("sequences"));
$("jog-step").addEventListener("change", drawJoints);
$("speed").addEventListener("input", () => { clearPreview(); speedChanged(); });
$("add-step").addEventListener("click", () => { if (rows.length >= 30) return toast("Keep each sequence to 30 rows or fewer."); rows.push({joint: "Wrist1", delta: 5}); clearPreview(); renderRows(); });
$("clear-sequence").addEventListener("click", () => { rows = []; clearPreview(); renderRows(); });
$("preview-sequence").addEventListener("click", () => action(() => previewRows()));
$("run-preview").addEventListener("click", () => { if (preview) action(() => runPlan(preview)); });
$("cancel-run").addEventListener("click", () => action(async () => { const result = await api("/api/cancel", {}); toast(result.message); }));
$("record-position").addEventListener("click", () => action(async () => { const position = await api("/api/positions", {name: $("position-name").value}); $("position-name").value = ""; toast(`Recorded ${position.name}.`); }));
$("load-approach").addEventListener("click", () => {
  const approach = $("approach-position").value, target = $("target-position").value;
  if (!approach || !target || approach === target) return toast("Select two different taught positions.");
  rows = [{position_id: approach}, {position_id: target}, {position_id: approach}]; clearPreview(); renderRows(); toast("Approach routine loaded. Preview its joint-space route.");
});
api("/api/session").then(session => { token = session.token; return refresh(); }).catch(error => toast(error.message));
setInterval(refresh, 1500);

api("/routines.json").then(data => { routines = data; $("load-dance").disabled = false; }).catch(error => toast(error.message));
$("load-dance").addEventListener("click", () => { const routine = routines.find(r => r.id === "slow-dance"); if (!routine) return; rows = routine.rows.map(row => ({...row})); $("speed").value = routine.speed; clearPreview(); speedChanged(); renderRows(); toast("Slow Dance loaded. Preview the full range before running."); });

let flowPlan = null, flowCheck = null;
const AXIS_COLORS = ["#e6b467", "#8cdbb4", "#a5bdf0", "#ef9fa3", "#bba4e4", "#a5d9e3"];
function flowAvailable() { return Boolean(flowPlan && flowCheck?.ready && flowCheck?.continuous_enabled && state?.connected && !activeJob() && !submitting); }
function drawFlowPlan(plan) {
  flowPlan = plan;
  $("flow-summary").textContent = `${plan.kind === "reset" ? "Return to captured reference" : "Continuous Flow Dance"} · ${plan.duration} controller seconds · amplitude ${Math.round((plan.amplitude_scale || 1)*100)}% · cap ${plan.speed}°/s · peak ${fixed(plan.peak_speed, 2)}°/s · peak acceleration ${fixed(plan.peak_acceleration, 2)}°/s². Six axes share one timeline. Chart: angle deviation from reference. Reference ${plan.checkpoint_id.slice(0,8)}. Preview expires in 3 minutes.`;
  $("flow-ranges").replaceChildren();
  plan.ranges.forEach((range,index) => { const item = node("div", "", `${pretty(range.joint)}: ${fixed(range.min,1)}° … ${fixed(range.max,1)}°`); item.style.color = AXIS_COLORS[index]; $("flow-ranges").append(item); });
  const chart = $("flow-chart"); chart.removeAttribute("hidden"); chart.replaceChildren();
  const initial = plan.preview_samples[0].degrees;
  const amplitude = Math.max(1,...plan.preview_samples.flatMap(sample => sample.degrees.map((q,i) => Math.abs(q-initial[i]))));
  JOINTS.forEach((joint,index) => { const line = document.createElementNS("http://www.w3.org/2000/svg", "polyline"); line.setAttribute("points",plan.preview_samples.map(sample => `${10+sample.t/plan.duration*580},${90-(sample.degrees[index]-initial[index])/amplitude*75}`).join(" ")); line.setAttribute("fill","none"); line.setAttribute("stroke",AXIS_COLORS[index]); line.setAttribute("stroke-width","1.6"); chart.append(line); });
  $("reset-preview").disabled = false;
  $("flow-run").disabled = !flowAvailable();
}
function drawFlowCheck(check) {
  flowCheck = check; $("motion-checks").replaceChildren();
  check.checks.forEach(item => $("motion-checks").append(node("div",item.passed ? "" : "failed",`${item.passed ? "✓" : "×"} ${item.name}${item.scan ? " · " + item.scan.count + " records · " + fixed(item.scan.elapsed_ms, 1) + " ms" : ""}`)));
  $("flow-gate").textContent = check.simulation ? "SIMULATION · accelerated kinematics only; no physical movement." : check.continuous_enabled ? "REAL ROBOT · separately enabled; Remote Control confirmation required." : "REAL EXECUTION DISABLED · prepare and inspect here; test execution in the simulation panel on port 8766.";
}
async function flowPreflight() { const check = await api("/api/motion/check",{}); drawFlowCheck(check); return check; }
$("motion-check").addEventListener("click",() => action(flowPreflight));
for (const id of ["flow-speed","flow-duration"]) $(id).addEventListener("change",()=>{flowPlan=null;$("flow-run").disabled=true;$("flow-summary").textContent="Settings changed. Prepare a fresh preview.";});
async function prepareFlow(reset, lowSpeed = false) { await action(async()=>{ await flowPreflight(); const plan=await api("/api/motion/preview",{speed:Number($("flow-speed").value),duration:lowSpeed ? 30 : Number($("flow-duration").value),reset,low_speed:lowSpeed}); drawFlowPlan(plan); toast(reset ? "Return-to-reference preview ready. No movement sent." : "Continuous trajectory prepared. No movement sent."); }); }
$("flow-preview").addEventListener("click",()=>prepareFlow(false));
$("reset-preview").addEventListener("click",()=>prepareFlow(true));
$("flow-run").addEventListener("click",()=>action(async()=>{ if (!flowPlan) return; const runId=crypto.randomUUID(); localStorage.setItem("ur5e-last-run",runId); await api("/api/motion/run",{plan_id:flowPlan.id,run_id:runId,remote_confirmed:$("remote-confirmed").checked}); flowPlan=null; $("flow-run").disabled=true; toast("Prepared continuous motion submitted once through SiLA."); }));
$("inspect-run").addEventListener("click",()=>action(async()=>{ const result=await api("/api/motion/inspect",{}); $("motion-inspection").hidden=false; $("motion-inspection").textContent=JSON.stringify({run_id:result.run_id,request_id:result.request_id,status:result.status,receipt_created_at:result.receipt_created_at,panel_failed_at:result.panel_failed_at,message:result.message},null,2); }));

$("low-speed-preview").addEventListener("click",()=>prepareFlow(false,true));
