'use strict';
const $ = id => document.getElementById(id);
let currentId = null;
let polling = null;
let lastJob = null;
const finishedStatuses = new Set(['완료','부분 실패','오류','중지']);
const esc = s => String(s ?? '').replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;').replaceAll('"','&quot;').replaceAll("'",'&#39;');
const auth = () => ({'X-Access-Key': $('accessKey').value});
// Opt-in only: persistent browser storage is readable by scripts on this site.
const SAVED_ACCESS_KEY = 'naver_crawler_access_key_v1';
const keyInput = $('accessKey');
const rememberCheckbox = $('rememberAccessKey');
const keyMessage = $('accessStatus');
const setKeyMessage = (message, kind='') => {
  keyMessage.textContent = message;
  keyMessage.className = `field-help ${kind}`;
};
const forgetSavedKey = () => {
  try { localStorage.removeItem(SAVED_ACCESS_KEY); } catch (_) { /* blocked storage */ }
};
const saveVerifiedKey = () => {
  if (!rememberCheckbox.checked) { forgetSavedKey(); return; }
  try {
    localStorage.setItem(SAVED_ACCESS_KEY, keyInput.value);
    setKeyMessage('확인 완료! 이 기기에서는 다음 접속부터 자동으로 불러와요.', 'success');
  } catch (_) {
    rememberCheckbox.checked = false;
    setKeyMessage('브라우저가 저장을 차단했어요. 현재 화면에서는 그대로 사용할 수 있어요.', 'error');
  }
};
async function checkAccessKey({persist=false}={}) {
  if (!keyInput.value.trim()) { setKeyMessage('접근코드를 먼저 입력해주세요.', 'error'); return false; }
  try {
    await requestJson('/api/auth-check');
    if (persist) saveVerifiedKey();
    else setKeyMessage('접근코드가 정상 확인됐어요. 저장하지 않고 사용합니다.', 'success');
    return true;
  } catch (err) {
    forgetSavedKey();
    setKeyMessage(`코드 확인 실패: ${err.message}`, 'error');
    return false;
  }
}
$('verifyKeyBtn').addEventListener('click', async () => {
  const btn = $('verifyKeyBtn');
  btn.disabled = true;
  try { await checkAccessKey({persist:rememberCheckbox.checked}); }
  finally { btn.disabled = false; }
});
rememberCheckbox.addEventListener('change', () => {
  if (!rememberCheckbox.checked) {
    forgetSavedKey();
    setKeyMessage('저장된 접근코드를 삭제했어요. 현재 입력한 코드는 이번 화면에서만 사용할 수 있어요.');
  } else {
    setKeyMessage('개인 기기인지 확인한 뒤 「코드 확인 및 저장」을 눌러주세요.');
  }
});
keyInput.addEventListener('input', () => {
  try {
    const stored = localStorage.getItem(SAVED_ACCESS_KEY);
    if (stored !== null && stored !== keyInput.value) forgetSavedKey();
  } catch (_) { /* blocked storage */ }
  setKeyMessage('코드를 변경했어요. 저장하려면 다시 확인해주세요.');
});
$('forgetKeyBtn').addEventListener('click', () => {
  forgetSavedKey();
  rememberCheckbox.checked = false;
  keyInput.value = '';
  setKeyMessage('이 기기에 저장된 코드를 삭제하고 입력란을 비웠어요.');
});
try {
  const savedKey = localStorage.getItem(SAVED_ACCESS_KEY);
  if (savedKey) {
    keyInput.value = savedKey;
    rememberCheckbox.checked = true;
    setKeyMessage('이 기기에 저장된 코드를 불러왔어요. 유효한지 확인 중입니다.');
    checkAccessKey().then(ok => {
      if (keyInput.value !== savedKey) return; // User changed the field while validation was pending.
      if (ok) setKeyMessage('이 기기에 저장된 접근코드를 자동으로 불러왔어요.', 'success');
      else {
        keyInput.value = '';
        rememberCheckbox.checked = false;
      }
    });
  }
} catch (_) { /* private browsing or blocked storage */ }
async function requestJson(url, options={}) {
  const resp = await fetch(url, {...options, headers:{...(options.body?{'Content-Type':'application/json'}:{}), ...auth(), ...(options.headers||{})}, cache:'no-store'});
  if (!resp.ok) { const data = await resp.json().catch(()=>({})); throw new Error(typeof data.detail === 'string' ? data.detail : `요청 오류 (${resp.status})`); }
  return resp.json();
}
function statusUpdate(status) {
  $('statStatus').textContent = status;
  const badge = $('statusBadge');
  badge.className = 'status-badge ' + (status==='완료'?'success':status==='대기'||status==='수집 중'?'active':status==='대기 중'?'idle':'fail');
  badge.innerHTML = `<span></span>${esc(status)}`;
}
function resetPoll(){if(polling) clearInterval(polling);polling=null;}
function setRunning(running){$('startBtn').disabled=running;$('stopBtn').disabled=!running;}
function renderRows(rows){
  const r = rows || [];
  $('rowCount').textContent = r.length;
  $('statCount').innerHTML = `${r.length}<span>건</span>`;
  $('emptyState').hidden = !!r.length;
  $('resultsWrapper').hidden = !r.length;
  $('resultsBody').innerHTML = r.map(row=>{
    const url=row['게시글 주소'] || '';
    const safe = /^https:\/\/(?:m\.)?blog\.naver\.com\//.test(url) ? url : '';
    return `<tr><td>${esc(row['화면 수집 순번'])}</td><td>${esc(row['검색 키워드'])}</td><td class="title-cell" title="${esc(row['게시글 제목'])}">${esc(row['게시글 제목'])}</td><td>${esc(row['블로그명']||'-')}</td><td>${esc(row['게시일(화면 표기)']||'-')}</td><td>${safe?`<a target="_blank" rel="noopener noreferrer" href="${esc(safe)}">게시글 보기 ↗</a>`:'-'}</td></tr>`;
  }).join('');
}
function renderJob(job){
  lastJob=job;
  statusUpdate(job.status);
  $('statCount').innerHTML=`${job.count}<span>건</span>`;
  $('jobSummary').hidden=false;
  $('jobStatusText').textContent=job.status;
  $('jobMessage').textContent=job.error || (finishedStatuses.has(job.status)?`${job.count}건 수집 / 오류 키워드 ${job.errors}개`:'실제 검색화면을 확인하고 있습니다.');
  $('jobTime').textContent=job.finished || job.created || '-';
  $('logs').textContent=(job.logs||[]).join('\n');
  $('logs').scrollTop=$('logs').scrollHeight;
  $('downloadBtn').disabled=!job.downloadReady;
  if(finishedStatuses.has(job.status)){
    resetPoll(); setRunning(false);
    renderRows(job.rows || []);
  } else setRunning(true);
}
async function poll(){
  if(!currentId)return;
  try {const job=await requestJson(`/api/jobs/${currentId}`);renderJob(job);} catch(err){resetPoll();setRunning(false);$('logs').textContent=`연결 오류: ${err.message}\n수집 서버 상태를 확인해주세요.`;statusUpdate('연결 오류');}
}
$('crawlForm').addEventListener('submit', async event=>{
  event.preventDefault();if(currentId && polling) return;
  const keywords=$('keywords').value.trim();
  const kw=keywords.split(/[\n,]+/).map(s=>s.trim()).filter(Boolean);
  if(!kw.length || kw.length>8){alert('키워드는 1개에서 8개까지 입력해주세요.');return;}
  const payload={keywords,device:document.querySelector('input[name="device"]:checked').value,limit:Number($('limit').value),scrolls:Number($('scrolls').value),pause:Number($('pause').value),lookup_missing_names:$('lookupNames').checked,screenshots:$('screenshots').checked};
  $('statKeywords').innerHTML=`${new Set(kw).size}<span>개</span>`;
  $('statCount').innerHTML='0<span>건</span>';
  $('rowCount').textContent='0';$('emptyState').hidden=false;$('resultsWrapper').hidden=true;$('jobSummary').hidden=false;
  $('jobStatusText').textContent='작업 생성 중...';$('jobMessage').textContent='서버 연결 확인 중';$('logs').textContent='서버에 수집 요청을 전달하는 중입니다.';
  setRunning(true);statusUpdate('요청 중');
  try{const job=await requestJson('/api/jobs',{method:'POST',body:JSON.stringify(payload)});currentId=job.id;saveVerifiedKey();sessionStorage.setItem('naver_job_id',currentId);await poll();polling=setInterval(poll,1800);}catch(err){setRunning(false);statusUpdate('요청 실패');$('logs').textContent=`실행 실패: ${err.message}`;$('jobMessage').textContent=err.message;}
});
$('stopBtn').addEventListener('click',async()=>{if(!currentId)return;try{await requestJson(`/api/jobs/${currentId}/stop`,{method:'POST'});$('logs').textContent+='\n중지 요청을 보냈습니다. 현재 처리 종료 후 저장됩니다.';}catch(err){alert(err.message);}});
$('downloadBtn').addEventListener('click',async()=>{if(!currentId)return;try{const resp=await fetch(`/api/jobs/${currentId}/download`,{headers:auth(),cache:'no-store'});if(!resp.ok){const data=await resp.json().catch(()=>({}));throw new Error(data.detail||'다운로드 실패');}const blob=await resp.blob();const url=URL.createObjectURL(blob);const a=document.createElement('a');a.href=url;a.download=`네이버_블로그_수집_${new Date().toISOString().slice(0,10)}.xlsx`;document.body.append(a);a.click();a.remove();setTimeout(()=>URL.revokeObjectURL(url),1200);}catch(err){alert('엑셀 다운로드 실패: '+err.message);}});
$('toggleLog').addEventListener('click',()=>{const el=$('logs');const collapsed=el.classList.toggle('collapsed');$('toggleLog').textContent=collapsed?'펼치기':'접기';});
$('keywords').addEventListener('input',()=>{$('statKeywords').innerHTML=`${new Set($('keywords').value.split(/[\n,]+/).map(s=>s.trim()).filter(Boolean)).size}<span>개</span>`;});
$('keywords').dispatchEvent(new Event('input'));
const oldId=sessionStorage.getItem('naver_job_id');if(oldId){currentId=oldId;poll().then(()=>{if(lastJob&&!finishedStatuses.has(lastJob.status))polling=setInterval(poll,1800);});}
