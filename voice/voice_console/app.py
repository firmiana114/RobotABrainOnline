from __future__ import annotations

from contextlib import asynccontextmanager
import logging
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .commands import CommandError, SystemdManager, read_tour_status
from .config import ConsoleConfig
from .dialogue import DialogueError, DialogueStore
from .dialogue_catalog import DialogueFileCatalog
from .pose import PoseTracker


logger = logging.getLogger(__name__)


class DialogueRequest(BaseModel):
    content: str


class LeaderCallingRequest(BaseModel):
    leader_calling: str


class HotRowsRequest(BaseModel):
    rows: list[dict]
    dialogue_file: str | None = None


class TourDialogueRequest(BaseModel):
    dialogue_file: str | None = None


def _html() -> str:
    return r'''<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>复星 G1 在线导览控制台</title>
  <style>
    :root{--bg:#06121f;--panel:#081d31;--line:#256693;--text:#e9f7ff;--muted:#91abc3;--blue:#15a8f5;--green:#22d884;--red:#e25555;--yellow:#f5c451}
    *{box-sizing:border-box}body{margin:0;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","Microsoft YaHei",sans-serif;background:#e9eef5;color:#172238}.shell{min-height:100vh;display:grid;grid-template-columns:220px 1fr}.sidebar{background:#081426;color:#fff;padding:24px 16px;display:flex;flex-direction:column;gap:24px}.brand{font-size:23px;font-weight:900;letter-spacing:1px;padding:4px 10px}.brand small{display:block;color:#78bde8;font-size:11px;margin-top:6px;letter-spacing:2px}.nav{display:grid;gap:8px}.nav button{border:0;background:transparent;color:#b8cae0;text-align:left;padding:13px 15px;border-radius:9px;font-size:15px;font-weight:800;cursor:pointer}.nav button.active{color:#fff;background:#1261d8}.main{min-width:0}.content{padding:16px}.page[hidden]{display:none}.control-deck{min-height:calc(100vh - 32px);padding:22px;border:1px solid #1e5f95;border-radius:16px;color:var(--text);background:radial-gradient(circle at 50% 40%,rgba(0,145,255,.2),transparent 34%),linear-gradient(180deg,#08223a,var(--bg));position:relative;overflow:hidden}.control-deck:before{content:"";position:absolute;inset:0;background:linear-gradient(90deg,rgba(29,161,242,.06) 1px,transparent 1px),linear-gradient(rgba(29,161,242,.06) 1px,transparent 1px);background-size:46px 46px;pointer-events:none}.control-deck>*{position:relative}.title{text-align:center;font-size:30px;font-weight:900;letter-spacing:3px;text-shadow:0 0 18px rgba(42,183,255,.8);margin:2px 0 20px}.layout{display:grid;grid-template-columns:minmax(240px,.75fr) minmax(320px,1.2fr) minmax(260px,.9fr);gap:16px}.stack{display:grid;gap:14px}.tech-card,.motion-panel,.panel{border:1px solid rgba(61,151,218,.7);border-radius:12px;background:rgba(4,22,39,.8);box-shadow:0 12px 36px rgba(0,0,0,.25);overflow:hidden}.tech-card h3{margin:0;padding:13px 16px;background:linear-gradient(90deg,rgba(25,87,145,.68),rgba(3,18,34,.2));font-size:17px}.tech-body{padding:16px}.metric{padding:12px;border:1px solid rgba(67,157,220,.35);border-radius:8px;background:rgba(3,16,29,.45);margin-bottom:11px}.metric:last-child{margin-bottom:0}.label{color:var(--muted);font-size:12px;margin-bottom:5px}.value{font-size:17px;font-weight:800;line-height:1.45;word-break:break-word}.robot-stage{min-height:500px;border:1px solid rgba(33,128,201,.5);border-radius:16px;background:radial-gradient(circle at center bottom,rgba(0,150,255,.3),transparent 30%),rgba(2,12,24,.45);display:grid;place-items:center;position:relative}.robot-stage:after{content:"";position:absolute;left:15%;right:15%;bottom:42px;height:20px;border:2px solid rgba(55,183,255,.55);border-radius:50%;box-shadow:0 0 32px rgba(55,183,255,.55)}.robot-stage img{position:relative;z-index:1;max-width:min(76%,430px);max-height:86%;object-fit:contain;filter:drop-shadow(0 0 28px rgba(40,183,255,.6));border-radius:12px}.task-grid{display:grid;grid-template-columns:90px 1fr;gap:12px;color:var(--muted)}.task-grid strong{color:var(--text)}.progress{height:10px;border-radius:99px;background:#0c2741;overflow:hidden;margin:13px 0}.progress span{display:block;height:100%;width:0;background:linear-gradient(90deg,#189dff,#1ee6ff);transition:width .3s}.map-grid{height:145px;border:1px solid rgba(67,157,220,.45);border-radius:10px;background:linear-gradient(90deg,rgba(44,151,235,.18) 1px,transparent 1px),linear-gradient(rgba(44,151,235,.18) 1px,transparent 1px);background-size:24px 24px;position:relative}.map-grid .pin{position:absolute;left:46%;top:44%;width:18px;height:18px;border-radius:50%;background:var(--green);box-shadow:0 0 18px var(--green)}.map-path{font:12px ui-monospace,Menlo,monospace;color:#9dc7e7;margin-top:9px;word-break:break-all}.voice-state{font-size:24px;font-weight:900;color:#849cb5}.voice-state.listening{color:#52dc8a}.wave{height:58px;display:flex;align-items:center;gap:8px;opacity:.35}.wave span{width:4px;height:31px;border-radius:8px;background:#26cbff}.wave.listening{opacity:1}.wave.listening span{animation:pulse 1s ease-in-out infinite;animation-delay:calc(var(--i)*.06s)}@keyframes pulse{0%,100%{transform:scaleY(.4)}45%{transform:scaleY(1.25)}}.motion-panel{margin-top:16px;padding:18px}.motion-title{font-size:18px;font-weight:900;margin-bottom:13px}.primary-actions{display:grid;grid-template-columns:repeat(5,minmax(135px,1fr));gap:12px}.btn,button.action{border:0;border-radius:8px;padding:12px 16px;color:white;font-size:15px;font-weight:900;cursor:pointer}.btn:disabled,button.action:disabled{opacity:.45;cursor:not-allowed}.nav-start{background:#1261d8}.arm-start{background:#6f42c1}.tour-restart{background:#b26a00}.tour-start{background:#138a50}.tour-stop{background:#b3261e}.message{min-height:42px;margin:14px 0 0;padding:10px 12px;border:1px solid rgba(61,151,218,.4);border-radius:8px;color:#b7d3e8;background:rgba(2,10,18,.52)}
    .dialogue-page{max-width:1500px;margin:0 auto;display:grid;gap:14px}.panel{background:#fff;border-color:#d6e0ed;color:#172238;box-shadow:0 8px 28px rgba(20,38,70,.08)}.panel-head{padding:17px 18px;border-bottom:1px solid #e3eaf2;display:flex;justify-content:space-between;gap:15px;align-items:center}.panel-title{font-size:18px;font-weight:900}.panel-body{padding:18px}.text-input,.hot-input{width:100%;border:1px solid #cbd7e6;border-radius:8px;padding:10px 11px;font:14px inherit;background:#fbfdff}.file-picker{display:grid;grid-template-columns:minmax(220px,460px) 1fr;gap:12px;align-items:end;margin-bottom:16px}.file-picker label{display:grid;gap:6px;color:#475569;font-size:13px;font-weight:800}.file-picker select{cursor:pointer}.row-actions{display:flex;gap:9px;flex-wrap:wrap;margin-top:12px}.load{background:#334155}.save{background:#137333}.add{background:#0f766e}.remove{background:#b3261e}.summary,.form-message{font-size:13px;color:#64748b;line-height:1.45}.table-wrap{overflow:auto}.hot-table{width:100%;border-collapse:collapse;min-width:930px}.hot-table th,.hot-table td{border-top:1px solid #e1e8f0;padding:10px;text-align:left;vertical-align:top}.hot-table th{color:#667085;font-size:12px}.hot-name{min-height:42px}.hot-coordinate,.hot-script{min-height:88px;resize:vertical;font-family:ui-monospace,Menlo,monospace}.hot-actions{display:flex;gap:7px;white-space:nowrap}.hot-actions button{padding:9px 10px;font-size:13px}.hidden-editor{display:none}
    @media(max-width:1150px){.layout{grid-template-columns:1fr 1fr}.robot-stage{grid-row:span 2}.primary-actions{grid-template-columns:repeat(2,1fr)}}@media(max-width:760px){.shell{grid-template-columns:1fr}.sidebar{padding:12px;gap:12px}.nav{grid-template-columns:1fr 1fr}.content{padding:8px}.layout,.file-picker{grid-template-columns:1fr}.robot-stage{min-height:360px;grid-row:auto}.primary-actions{grid-template-columns:1fr}.control-deck{padding:13px}.title{font-size:22px}.panel-head{align-items:flex-start;flex-direction:column}}
  </style>
</head>
<body>
<div class="shell">
  <aside class="sidebar">
    <div class="brand">Voice Guide<small>FOSUN · UNITREE G1</small></div>
    <nav class="nav">
      <button id="nav-control" class="active" onclick="showPage('control')">任务控制</button>
      <button id="nav-dialogue" onclick="showPage('dialogue')">点位台词</button>
    </nav>
  </aside>
  <main class="main"><div class="content">
    <section id="page-control" class="page">
      <div class="control-deck">
        <div class="title">复星 G1 在线导览系统</div>
        <div class="layout">
          <div class="stack">
            <section class="tech-card"><h3>机器人状态</h3><div class="tech-body">
              <div class="metric"><div class="label">导航</div><div id="navState" class="value">读取中</div></div>
              <div class="metric"><div class="label">手臂控制</div><div id="armState" class="value">读取中</div></div>
              <div class="metric"><div class="label">导览服务</div><div id="tourServiceState" class="value">读取中</div></div>
              <div class="metric"><div class="label">定位状态</div><div id="poseState" class="value">读取中</div></div>
              <div class="metric"><div class="label">当前位姿</div><div id="poseText" class="value">暂无定位位姿</div></div>
            </div></section>
          </div>
          <section class="robot-stage"><img src="/static/voice-console/unitree-g1-dashboard.png" alt="宇树 G1 机器人"></section>
          <div class="stack">
            <section class="tech-card"><h3>任务信息</h3><div class="tech-body">
              <div class="task-grid"><span>任务状态</span><strong id="taskState">待命</strong><span>台词文件</span><strong id="activeDialogueFile">读取中</strong><span>当前站点</span><strong id="currentSite">未知</strong><span>下一站点</span><strong id="nextSite">未知</strong></div>
              <div class="progress"><span id="progressBar"></span></div>
            </div></section>
            <section class="tech-card"><h3>导航地图</h3><div class="tech-body"><div class="map-grid"><span class="pin"></span></div><div id="mapPath" class="map-path">-</div></div></section>
            <section class="tech-card"><h3>语音交互</h3><div class="tech-body"><div id="speechState" class="voice-state">未在监听</div><div id="voiceWave" class="wave">''' + ''.join(f'<span style="--i:{i}"></span>' for i in range(16)) + r'''</div><div class="summary">仅显示运行状态，不保存或展示识别原文。</div></div></section>
          </div>
        </div>
        <section class="motion-panel">
          <div class="motion-title">导览控制</div>
          <div class="primary-actions">
            <button id="startNavigationBtn" class="btn nav-start" onclick="runAction('/api/navigation/start',this)">重启导航</button>
            <button id="startArmBtn" class="btn arm-start" onclick="runAction('/api/arm/start',this)">重启手臂控制</button>
            <button id="restartTourServiceBtn" class="btn tour-restart" onclick="restartTourService(this)">重启导览服务</button>
            <button id="startTourBtn" class="btn tour-start" onclick="startTour(this)">启动导览</button>
            <button id="stopTourBtn" class="btn tour-stop" onclick="stopTour(this)">关闭导览</button>
          </div>
          <p id="controlMessage" class="message"></p>
        </section>
      </div>
    </section>
    <section id="page-dialogue" class="page" hidden>
      <div class="dialogue-page">
        <section class="panel"><div class="panel-head"><div><div class="panel-title">嘉宾称呼</div><div id="leaderSummary" class="summary">未加载</div></div></div><div class="panel-body">
          <input id="leaderInput" class="text-input" maxlength="80" placeholder="例如：各位嘉宾">
          <div class="row-actions"><button class="action load" onclick="loadLeader()">加载嘉宾称呼</button><button class="action save" onclick="saveLeader()">保存嘉宾称呼</button></div>
          <p id="leaderMessage" class="form-message"></p>
        </div></section>
        <section class="panel"><div class="panel-head"><div><div class="panel-title">点位台词热更新</div><div id="rowsSummary" class="summary">未加载</div></div></div><div class="panel-body">
          <div class="file-picker">
            <label for="dialogueFileSelect">台词文件
              <select id="dialogueFileSelect" class="text-input" onchange="switchDialogueFile()"></select>
            </label>
            <div class="summary">自动展示 <code>voice/conf</code> 下的台词 JSON 文件；派生的 <code>*_route.json</code> 不会显示。</div>
          </div>
          <div class="summary">台词用空行分段；功能段需单独一行写在对应台词下方：<code>{"post_wait_seconds":277}</code> 表示播报后等待，<code>{"speak_during_navigation":true}</code> 表示边走边说（改为 <code>false</code> 或删除则到达后再说），<code>{"background_command":"coffee_delivery_run"}</code> 表示异步呼叫咖啡小车。</div>
          <div class="table-wrap"><table class="hot-table"><thead><tr><th>点位名字</th><th>坐标 JSON</th><th>讲解台词（空行分段）</th><th>操作</th></tr></thead><tbody id="rowsBody"></tbody></table></div>
          <div class="row-actions"><button class="action load" onclick="loadRows()">加载点位台词</button><button class="action add" onclick="addRow({})">新增点位</button><button class="action save" onclick="saveRows()">保存点位台词</button></div>
          <p id="rowsMessage" class="form-message"></p>
        </div></section>
      </div>
    </section>
  </div></main>
</div>
<script>
let latestPose=null,loadedDialogueFile='';
function text(id,value){document.getElementById(id).textContent=value==null?'-':value;}
function showPage(name){document.getElementById('page-control').hidden=name!=='control';document.getElementById('page-dialogue').hidden=name!=='dialogue';document.getElementById('nav-control').classList.toggle('active',name==='control');document.getElementById('nav-dialogue').classList.toggle('active',name==='dialogue');if(name==='dialogue'){loadLeader();loadDialogueFiles();}}
async function request(method,url,payload){const options={method,headers:{}};if(payload!==undefined){options.headers['Content-Type']='application/json';options.body=JSON.stringify(payload);}const response=await fetch(url,options);let body={};try{body=await response.json();}catch(e){}if(!response.ok){throw new Error(body.detail||('请求失败：'+response.status));}return body;}
function stateLabel(value){return {active:'运行中',activating:'启动中',inactive:'未启动',failed:'异常'}[value]||value||'未知';}
function taskLabel(value){return {preparing:'预热中',ready:'已预热',starting:'启动中',running:'导览中',stopping:'关闭中',stopped:'已关闭',completed:'已完成',failed:'导览异常',service_stopped:'服务未启动'}[value]||value||'待命';}
function prerequisiteReady(serviceState,externalProcess){return serviceState==='active'||externalProcess;}
async function refresh(){try{const data=await request('GET','/api/status');text('navState',data.external_processes.navigation?'外部进程运行中':stateLabel(data.services.navigation));text('armState',data.external_processes.arm?'外部进程运行中':stateLabel(data.services.arm));text('tourServiceState',stateLabel(data.services.tour));text('poseState',data.pose.message);latestPose=data.pose&&data.pose.fresh?data.pose:null;if(data.pose&&data.pose.available){text('poseText','x='+data.pose.x.toFixed(3)+'  y='+data.pose.y.toFixed(3)+'\noz='+data.pose.oz.toFixed(3)+'  ow='+data.pose.ow.toFixed(3));}else{text('poseText','暂无定位位姿');}text('taskState',data.external_processes.tour?'外部导览服务运行中':taskLabel(data.task.status));text('activeDialogueFile',data.active_dialogue_file||'未知');text('currentSite',data.task.current_site||'未知');text('nextSite',data.task.next_site||'未知');const total=Number(data.task.total_points||0),current=Number(data.task.current_index||0);document.getElementById('progressBar').style.width=(total?Math.min(100,current/total*100):0)+'%';text('mapPath',data.map_path);text('speechState',data.task.speech_state||'未在监听');const listening=['监听中','回答中','启动中'].includes(data.task.speech_state);document.getElementById('speechState').classList.toggle('listening',listening);document.getElementById('voiceWave').classList.toggle('listening',listening);document.getElementById('startNavigationBtn').disabled=false;document.getElementById('startArmBtn').disabled=false;const navigationReady=prerequisiteReady(data.services.navigation,data.external_processes.navigation);const armReady=prerequisiteReady(data.services.arm,data.external_processes.arm);const serviceReady=data.services.tour==='active';const taskStartable=['ready','stopped','completed','failed'].includes(data.task.status);document.getElementById('restartTourServiceBtn').disabled=!navigationReady||!armReady;document.getElementById('startTourBtn').disabled=!navigationReady||!armReady||!serviceReady||!taskStartable;document.getElementById('stopTourBtn').disabled=!['starting','running'].includes(data.task.status);}catch(error){text('controlMessage',error.message);}}
async function runAction(url,button,payload){button.disabled=true;text('controlMessage','正在执行...');try{const body=await request('POST',url,payload||{});text('controlMessage',body.message);}catch(error){text('controlMessage',error.message);}finally{setTimeout(refresh,500);}}
function tourDialoguePayload(){return {dialogue_file:selectedDialogueFile()||loadedDialogueFile||null};}
async function restartTourService(button){if(!confirm('重启会终止当前导览任务，并使用当前选择的台词文件重新加载模型与路线语音缓存。确定继续吗？'))return;await runAction('/api/tour/restart-service',button,tourDialoguePayload());}
async function startTour(button){await runAction('/api/tour/start',button,tourDialoguePayload());}
async function stopTour(button){if(!confirm('只关闭当前导览任务，预热服务、导航和手臂将继续运行。确定关闭吗？'))return;await runAction('/api/tour/stop',button);}
async function loadLeader(){try{const body=await request('GET','/api/dialogue/leader-calling');document.getElementById('leaderInput').value=body.leader_calling||'';text('leaderSummary','文件：'+body.summary.path+' / 地图：'+body.summary.map_file);text('leaderMessage',body.message);}catch(error){text('leaderMessage',error.message);}}
async function saveLeader(){const value=document.getElementById('leaderInput').value.trim();if(!value){text('leaderMessage','嘉宾称呼不能为空');return;}try{const body=await request('POST','/api/dialogue/leader-calling',{leader_calling:value});text('leaderMessage',body.message);await loadLeader();}catch(error){text('leaderMessage',error.message);}}
function rowIsOpening(tr){return tr.dataset.rowType==='opening'||tr.querySelector('.hot-name').value.trim().toLowerCase()==='opening';}
function updateRowState(tr){const opening=rowIsOpening(tr);tr.querySelector('.hot-coordinate').disabled=opening;tr.querySelector('.pose-btn').disabled=opening;tr.querySelector('.remove-btn').disabled=opening;if(opening)tr.querySelector('.hot-coordinate').value='';}
function addRow(row){row=row||{};const tr=document.createElement('tr');tr.dataset.rowId=row.id||'';tr.dataset.rowType=row.row_type||'step';tr.dataset.pointKey=row.point_key||'';tr.dataset.stepIndex=row.step_index==null?'':row.step_index;tr.innerHTML='<td><input class="hot-input hot-name"></td><td><textarea class="hot-input hot-coordinate"></textarea></td><td><textarea class="hot-input hot-script"></textarea></td><td><div class="hot-actions"><button class="action load pose-btn" type="button">更新为机器人当前位置</button><button class="action remove remove-btn" type="button">删除</button></div></td>';tr.querySelector('.hot-name').value=row.point_name||'';tr.querySelector('.hot-coordinate').value=row.coordinate||'';tr.querySelector('.hot-script').value=row.script||'';tr.querySelector('.hot-script').placeholder=(row.row_type||'step')==='opening'?'opening JSON':'台词内容\n{"speak_during_navigation":true}\n{"background_command":"coffee_delivery_run"}\n{"post_wait_seconds":2.5}\n\n下一段台词';tr.querySelector('.hot-name').oninput=()=>updateRowState(tr);tr.querySelector('.pose-btn').onclick=()=>{if(!latestPose){text('rowsMessage','暂无 5 秒内的有效机器人位姿');return;}tr.querySelector('.hot-coordinate').value=JSON.stringify({x:latestPose.x,y:latestPose.y,z:latestPose.z,ox:latestPose.ox,oy:latestPose.oy,oz:latestPose.oz,ow:latestPose.ow,mode:1});text('rowsMessage','已填入机器人当前位置，点击保存后生效');};tr.querySelector('.remove-btn').onclick=()=>tr.remove();document.getElementById('rowsBody').appendChild(tr);updateRowState(tr);}
function selectedDialogueFile(){return document.getElementById('dialogueFileSelect').value||'';}
async function loadDialogueFiles(){const select=document.getElementById('dialogueFileSelect');const previous=select.value||loadedDialogueFile;try{const body=await request('GET','/api/dialogue/files');select.innerHTML='';(body.files||[]).forEach(function(item){const option=document.createElement('option');option.value=item.name;option.textContent=item.name+(item.is_active?'（当前导览）':(item.is_default?'（默认）':''));select.appendChild(option);});const names=(body.files||[]).map(item=>item.name);select.value=names.includes(previous)?previous:(body.active_file||body.default_file||names[0]||'');if(!select.value){document.getElementById('rowsBody').innerHTML='';text('rowsSummary','没有可用台词文件');text('rowsMessage','请先在 conf 目录放入台词 JSON 文件');return;}await loadRows();}catch(error){text('rowsMessage',error.message);}}
async function switchDialogueFile(){const selected=selectedDialogueFile();if(loadedDialogueFile&&selected!==loadedDialogueFile&&!confirm('切换台词文件会丢弃表格中尚未保存的修改。确定继续吗？')){document.getElementById('dialogueFileSelect').value=loadedDialogueFile;return;}await loadRows();}
async function loadRows(){const fileName=selectedDialogueFile();if(!fileName){text('rowsMessage','请先选择台词文件');return;}try{const body=await request('GET','/api/dialogue/hot-rows?dialogue_file='+encodeURIComponent(fileName));const tbody=document.getElementById('rowsBody');tbody.innerHTML='';(body.rows||[]).forEach(addRow);loadedDialogueFile=body.dialogue_file||fileName;document.getElementById('dialogueFileSelect').value=loadedDialogueFile;text('rowsSummary','文件：'+body.summary.path+' / 表格行：'+body.rows.length+' / 点位：'+body.summary.points+' / 台词段：'+body.summary.segments);text('rowsMessage',body.message);}catch(error){text('rowsMessage',error.message);}}
function collectRows(){return Array.from(document.querySelectorAll('#rowsBody tr')).map(tr=>({id:tr.dataset.rowId,row_type:tr.dataset.rowType,step_index:tr.dataset.stepIndex,point_key:tr.dataset.pointKey,point_name:tr.querySelector('.hot-name').value,coordinate:tr.querySelector('.hot-coordinate').value,script:tr.querySelector('.hot-script').value}));}
async function saveRows(){const fileName=selectedDialogueFile();if(!fileName){text('rowsMessage','请先选择台词文件');return;}text('rowsMessage','正在校验并保存 '+fileName+' ...');try{const body=await request('POST','/api/dialogue/hot-rows',{dialogue_file:fileName,rows:collectRows()});await loadRows();text('rowsMessage',body.message);}catch(error){text('rowsMessage',error.message);}}
refresh();setInterval(refresh,2000);
</script>
</body></html>'''


def create_app(
    config: ConsoleConfig | None = None,
    manager: SystemdManager | None = None,
    store: DialogueStore | None = None,
    catalog: DialogueFileCatalog | None = None,
    tracker: PoseTracker | None = None,
) -> FastAPI:
    config = config or ConsoleConfig.from_env()
    manager = manager or SystemdManager(config)
    store = store or DialogueStore(config.dialogue_file, config.route_file, config.qa_context_file, config.map_root)
    catalog = catalog or DialogueFileCatalog(store)
    tracker = tracker or PoseTracker(config.pose_stale_seconds)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        tracker.start()
        try:
            catalog.activate()
        except DialogueError:
            logger.exception("控制台启动时同步导览派生文件失败")
        yield
        tracker.stop()

    app = FastAPI(title="Voice 在线导览控制台", lifespan=lifespan)
    static_dir = Path(__file__).with_name("static")
    app.mount("/static/voice-console", StaticFiles(directory=static_dir), name="voice_console_static")

    def command_call(callback):
        try:
            return callback()
        except CommandError as exc:
            raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc

    def dialogue_call(callback):
        try:
            return callback()
        except DialogueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/", response_class=HTMLResponse)
    def index() -> HTMLResponse:
        return HTMLResponse(_html(), headers={"Cache-Control": "no-store"})

    @app.get("/api/status")
    def status() -> dict:
        states = manager.all_states()
        external_processes = manager.external_processes()
        task = read_tour_status(config.runtime_status_file, states["tour"])
        try:
            active_dialogue_file = catalog.active_file_name()
            map_path = catalog.active_map_path()
        except DialogueError as exc:
            active_dialogue_file = config.dialogue_file.name
            map_path = f"配置错误：{exc}"
        return {
            "ok": True,
            "services": states,
            "external_processes": external_processes,
            "pose": tracker.snapshot(),
            "task": task,
            "active_dialogue_file": active_dialogue_file,
            "map_path": map_path,
        }

    @app.post("/api/navigation/start")
    def start_navigation() -> dict:
        return command_call(manager.start_navigation)

    @app.post("/api/arm/start")
    def start_arm() -> dict:
        return command_call(manager.start_arm)

    @app.post("/api/tour/start")
    def start_tour(payload: TourDialogueRequest | None = None) -> dict:
        activation = dialogue_call(
            lambda: catalog.activate(payload.dialogue_file if payload else None)
        )
        result = command_call(manager.start_tour)
        result["dialogue_file"] = activation["dialogue_file"]
        result["message"] = (
            f"{result.get('message', '导览任务已启动')}；"
            f"台词文件：{activation['dialogue_file']}"
        )
        return result

    @app.post("/api/tour/restart-service")
    def restart_tour_service(payload: TourDialogueRequest | None = None) -> dict:
        activation = dialogue_call(
            lambda: catalog.activate(payload.dialogue_file if payload else None)
        )
        result = command_call(manager.restart_tour_service)
        result["dialogue_file"] = activation["dialogue_file"]
        result["message"] = (
            f"{result.get('message', '导览服务正在预热')}；"
            f"台词文件：{activation['dialogue_file']}"
        )
        return result

    @app.post("/api/tour/stop")
    def stop_tour() -> dict:
        return command_call(manager.stop_tour)

    @app.get("/api/dialogue")
    def get_dialogue() -> dict:
        return dialogue_call(store.read_editor)

    @app.post("/api/dialogue")
    def save_dialogue(payload: DialogueRequest) -> dict:
        return dialogue_call(lambda: store.write_editor(payload.content))

    @app.get("/api/dialogue/leader-calling")
    def get_leader_calling() -> dict:
        return dialogue_call(store.read_leader_calling)

    @app.post("/api/dialogue/leader-calling")
    def save_leader_calling(payload: LeaderCallingRequest) -> dict:
        return dialogue_call(lambda: store.write_leader_calling(payload.leader_calling))

    @app.get("/api/dialogue/files")
    def get_dialogue_files() -> dict:
        return dialogue_call(catalog.list_files)

    @app.get("/api/dialogue/hot-rows")
    def get_hot_rows(dialogue_file: str | None = None) -> dict:
        return dialogue_call(lambda: catalog.read_hot_rows(dialogue_file))

    @app.post("/api/dialogue/hot-rows")
    def save_hot_rows(payload: HotRowsRequest) -> dict:
        return dialogue_call(
            lambda: catalog.write_hot_rows(payload.dialogue_file, payload.rows)
        )

    return app


app = create_app()
