'use strict';
const $ = (s, root = document) => root.querySelector(s);
const esc = v => String(v ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const label = v => String(v || '').replaceAll('_', ' ').replace(/^./, c => c.toUpperCase());
const date = v => v ? new Date(v * 1000).toLocaleString([], {month:'short',day:'numeric',hour:'2-digit',minute:'2-digit'}) : 'Not set';
const initials = name => name.split(/\s+/).filter(Boolean).slice(0,2).map(x=>x[0]).join('').toUpperCase();
let toastTimer;
function toast(message) { const node = $('#toast'); node.textContent = message; node.hidden = false; clearTimeout(toastTimer); toastTimer = setTimeout(()=>node.hidden=true, 5500); }
async function api(path, method = 'GET', data, extraHeaders = {}) {
  const response = await fetch(path, {method, credentials:'same-origin', headers:{'Content-Type':'application/json','X-LeadHarbor':'1', ...extraHeaders}, ...(data === undefined ? {} : {body:JSON.stringify(data)})});
  const result = await response.json();
  if (!response.ok) { if (response.status === 401 && $('#workspace')) showLogin(); throw new Error(result.error || 'Request failed. Please try again.'); }
  return result;
}
function showLogin() { $('#workspace').hidden = true; $('#login-screen').hidden = false; }
async function busy(button, task) { if (button.disabled) return; button.disabled=true; try { await task(); } catch (e) { toast(e.message); } finally { button.disabled=false; } }

async function contactPage() {
  const form = $('#contact-form');
  const keyName = 'leadharbor-submission';
  // Persist both the key and its content. A changed inquiry gets a new key; a network retry reuses the old one.
  let localKey;
  let localPayload;
  try { const old=JSON.parse(sessionStorage.getItem(keyName)||'null'); localKey=old?.key; localPayload=old?.payload; } catch (_) { /* storage may be disabled */ }
  const config = await api('/api/public-config').catch(()=>null);
  if (config) {
    const p = $('#privacy-notice');
    p.textContent += ` Retention: ${config.retention_days} days. For access or deletion, contact ${config.privacy_contact}.`;
    if (config.demo) { const n=document.createElement('p'); n.className='notice'; n.textContent='Demo form: use synthetic information only. No website or AI requests are made.'; form.prepend(n); }
  }
  form.addEventListener('submit', async e => {
    e.preventDefault();
    if (!form.reportValidity()) return;
    const data = Object.fromEntries(new FormData(form)); data.consent = form.elements.consent.checked;
    const payload=JSON.stringify(data);
    if (localPayload !== payload || !localKey) { localKey=crypto.randomUUID(); localPayload=payload; }
    try { sessionStorage.setItem(keyName, JSON.stringify({key:localKey,payload})); } catch (_) { /* in-memory retries still work */ }
    const button=$('button[type="submit"]',form); button.disabled=true; $('#contact-error').textContent='';
    try {
      const result=await api('/api/submit','POST',data,{'Idempotency-Key':localKey});
      form.hidden=true; $('#contact-success').hidden=false; $('#receipt').textContent='Receipt · '+result.id;
      $('#contact-success').setAttribute('tabindex','-1'); $('#contact-success').focus();
      try { sessionStorage.removeItem(keyName); } catch (_) { /* optional storage */ }
      localKey=null; localPayload=null;
    } catch(error) { $('#contact-error').textContent=error.message; }
    finally { button.disabled=false; }
  });
  $('#another-message').addEventListener('click',()=> { form.reset(); form.hidden=false; $('#contact-success').hidden=true; $('#full-name').focus(); });
}

const state = {view:'overview', search:'', status:'', tier:'', offset:0, metrics:null, config:null, currentLead:null, generation:0};
const viewInfo = {
  overview:['Overview','YOUR PIPELINE, IN FOCUS','A little clarity. A lot of possibility.','Meet your newest opportunities and keep the next steps moving.'],
  inbox:['All leads','EVERY HELLO, ALL IN ONE PLACE','Good opportunities start here.','Search, qualify, and choose the right next step for every inquiry.'],
  followups:['Follow-ups','KEEP THE CONVERSATION GOING','A timely next step goes a long way.','Open opportunities, ordered by follow-up time.'],
  recovery:['Recovery','NOTHING FALLS THROUGH THE CRACKS','Give unfinished work another chance.','Review processing failures and notification delivery.'],
  insights:['Insights','LEARN FROM EVERY CONVERSATION','See what’s working. Make it better.','Qualification feedback, conversion, usage, and workspace configuration.']
};
function badge(value) { return `<span class="badge ${esc(value)}">${esc(label(value))}</span>`; }
function empty(title, description) { return `<div class="empty"><span class="empty-symbol">↗</span><h3>${esc(title)}</h3><p>${esc(description)}</p><a class="button secondary" href="/contact" target="_blank" rel="noopener">Open contact form ↗</a></div>`; }
function stat(labelText, count, symbol, foot) { return `<article class="stat-card"><div class="stat-label">${labelText}<span class="stat-symbol" aria-hidden="true">${symbol}</span></div><div class="stat-number">${count}</div><div class="stat-foot">${foot}</div></article>`; }
function stats(m) {
  const processing = (m.statuses.pending||0)+(m.statuses.retry||0)+(m.statuses.processing||0);
  return `<div class="stats-grid">${stat('Total inquiries',m.total,'▤',`Across <strong>${m.contacts} contacts</strong>`)}${stat('Hot opportunities',m.tiers.hot||0,'↗','A strong fit for your team')}${stat('Follow-ups due',m.overdue,'◷','Open conversations needing attention')}${stat('In progress',processing,'◌',`<strong>${(m.statuses.failed||0)+(m.statuses.needs_configuration||0)}</strong> need recovery`)}</div>`;
}
function leadRows(items) { return items.map(r=>`<tr><td><div class="lead-person"><span class="avatar">${esc(initials(r.full_name))}</span><div><button class="lead-name" data-lead="${esc(r.id)}">${esc(r.full_name)}</button><span class="lead-email">${esc(r.email)}</span></div></div></td><td>${esc(r.company_domain||'Not provided')}</td><td>${badge(r.tier)}</td><td><span class="score">${r.score??'—'}${r.score===null?'':'<small> / 10</small>'}</span></td><td>${badge(r.status)}</td><td>${esc(date(r.created_at))}</td></tr>`).join(''); }
function filters() { return `<div class="table-toolbar"><div class="search-wrap"><label class="honeypot" for="lead-search">Search leads</label><input id="lead-search" type="search" placeholder="Search name, email, or company…" value="${esc(state.search)}" maxlength="200"></div><label class="honeypot" for="status-filter">Processing status</label><select id="status-filter"><option value="">All statuses</option>${['pending','processing','ready','needs_review','retry','failed','needs_configuration'].map(x=>`<option value="${x}" ${x===state.status?'selected':''}>${label(x)}</option>`).join('')}</select><label class="honeypot" for="tier-filter">Lead tier</label><select id="tier-filter"><option value="">All tiers</option>${['hot','warm','cold','review'].map(x=>`<option value="${x}" ${x===state.tier?'selected':''}>${label(x)}</option>`).join('')}</select><button class="button secondary" id="apply-filters">Search</button></div>`; }
function leadTable(result, title='Latest arrivals', filter=false) {
  return `<section class="panel"><div class="panel-heading"><div><h2>${title}</h2><p>${filter?'A clear view of every inquiry.':'A first look at your newest conversations.'}</p></div>${!filter?'<button class="filter-button" data-view="inbox">View all leads ↗</button>':''}</div>${filter?filters():''}${result.items.length?`<div class="table-scroll"><table><thead><tr><th>Lead</th><th>Company</th><th>Fit</th><th>Score</th><th>Status</th><th>Received</th></tr></thead><tbody>${leadRows(result.items)}</tbody></table></div>`:empty('Room for your next opportunity',state.search||state.status||state.tier?'No leads match these filters. Try broadening your search.':'Share your contact form to start collecting inquiries.')}<div class="table-foot"><span>${result.total?state.offset+1:0}–${Math.min(state.offset+result.items.length,result.total)} of ${result.total} inquiries</span>${filter?`<div><button class="button secondary" id="previous-page" ${state.offset===0?'disabled':''}>← Previous</button> <button class="button secondary" id="next-page" ${state.offset+result.items.length>=result.total?'disabled':''}>Next →</button></div>`:'<span>Saved before enrichment ✓</span>'}</div></section>`;
}
function sidePanels(m, items) {
  const tiers = ['hot','warm','cold','review'];
  return `<aside class="right-column"><section class="panel"><div class="panel-heading"><div><h2>Your qualification mix</h2><p>A little perspective on the pipeline.</p></div></div><div class="mini-funnel">${tiers.map(t=>`<div class="funnel-row"><div class="funnel-label"><span>${label(t)}</span><b>${m.tiers[t]||0}</b></div><meter min="0" max="${Math.max(1,m.total)}" value="${m.tiers[t]||0}" aria-label="${label(t)} leads"></meter></div>`).join('')}</div><div class="callout"><strong>A score starts a conversation.</strong><p>Check the evidence, review the suggested reply, and let your judgment lead.</p><button class="text-link filter-button" data-view="insights">Explore insights ↗</button></div></section><section class="panel"><div class="panel-heading"><div><h2>Recent activity</h2><p>Fresh arrivals in your harbor.</p></div></div><div class="activity-list">${items.length?items.slice(0,4).map(r=>`<div class="activity-item"><span class="activity-icon">↗</span><div><p><button class="lead-name" data-lead="${esc(r.id)}">${esc(r.full_name)}</button>${esc(label(r.status))}</p><small>${esc(date(r.updated_at))}</small></div></div>`).join(''):'<p class="muted">Activity will appear as leads arrive.</p>'}</div></section></aside>`;
}
async function loadView() {
  const current = state.view;
  const generation = ++state.generation;
  const [crumb, eyebrow, title, description]=viewInfo[current];
  $('#breadcrumb').textContent=crumb; $('#page-eyebrow').textContent=eyebrow; $('#page-title').textContent=title; $('#page-description').textContent=description;
  document.querySelectorAll('.nav-button').forEach(b=>{ b.classList.toggle('active',b.dataset.view===current); if(b.dataset.view===current)b.setAttribute('aria-current','page');else b.removeAttribute('aria-current'); });
  const target=$('#view-content'); target.innerHTML='<div class="loading" role="status">Bringing your workspace into focus…</div>';
  try {
    const m=await api('/api/metrics'); state.metrics=m; $('#nav-count').textContent=m.total;
    if (state.view!==current || state.generation!==generation) return;
    if (current==='overview' || current==='inbox') {
      const query=current==='overview'?'limit=8':new URLSearchParams({limit:'50',offset:String(state.offset),search:state.search,status:state.status,tier:state.tier});
      const rows=await api('/api/leads?'+query);
      if (state.view!==current || state.generation!==generation) return;
      target.innerHTML=current==='overview'?stats(m)+`<div class="dashboard-columns"><div>${leadTable(rows)}</div>${sidePanels(m,rows.items)}</div>`:leadTable(rows,'Your lead inbox',true);
      bindFilters();
    } else if (current==='followups') {
      // Walk all pages so older overdue leads are never hidden by a recent-arrival limit.
      let rows=[], offset=0, page;
      do { page=await api('/api/leads?limit=100&offset='+offset); rows.push(...page.items); offset+=100; } while(offset<page.total);
      rows=rows.filter(r=>r.follow_up_at && !['won','lost'].includes(r.stage)).sort((a,b)=>a.follow_up_at-b.follow_up_at);
      if (state.view!==current || state.generation!==generation) return;
      target.innerHTML=`<section class="panel"><div class="panel-heading"><div><h2>Next conversations</h2><p>${m.overdue} overdue · ${rows.length} open follow-ups</p></div></div>${rows.length?`<div class="table-scroll"><table><thead><tr><th>Lead</th><th>Owner</th><th>Follow-up</th><th>Stage</th><th>Fit</th></tr></thead><tbody>${rows.map(r=>`<tr><td><button class="lead-name" data-lead="${esc(r.id)}">${esc(r.full_name)}</button><span class="lead-email">${esc(r.email)}</span></td><td>${esc(r.owner)}</td><td>${r.follow_up_at<Date.now()/1000?'<span class="badge failed">Overdue</span> ':''}${esc(date(r.follow_up_at))}</td><td>${badge(r.stage)}</td><td>${badge(r.tier)}</td></tr>`).join('')}</tbody></table></div>`:empty('Nothing waiting on you','Follow-up dates appear after qualification, or when you assign one to a lead.')}</section>`;
    } else if (current==='recovery') {
      const [failed,blocked,events]=await Promise.all([api('/api/leads?status=failed&limit=100'),api('/api/leads?status=needs_configuration&limit=100'),api('/api/events')]);
      if (state.view!==current || state.generation!==generation) return;
      const rows=[...failed.items,...blocked.items];
      target.innerHTML=`<section class="panel"><div class="panel-heading"><div><h2>Enrichment recovery</h2><p>${failed.total+blocked.total} leads need attention · showing up to 100 per status</p></div></div>${rows.length?`<div class="table-scroll"><table class="recovery-table"><thead><tr><th>Lead</th><th>Status</th><th>Last issue</th><th>Attempts</th><th>Action</th></tr></thead><tbody>${rows.map(r=>`<tr><td><button class="lead-name" data-lead="${esc(r.id)}">${esc(r.full_name)}</button></td><td>${badge(r.status)}</td><td>${esc(r.last_error||'—')}</td><td>${r.attempts}</td><td><button class="button secondary" data-retry="${esc(r.id)}">Retry ↻</button></td></tr>`).join('')}</tbody></table></div>`:empty('Your leads are in safe hands','No failed or configuration-blocked enrichments right now.')}</section><section class="panel"><div class="panel-heading"><div><h2>Notification delivery</h2><p>${state.config.webhook_configured?'Delivery is enabled. Receivers should deduplicate by event ID.':'Notifications are paused until an integration is configured.'} Latest 100 events.</p></div></div>${events.items.length?`<div class="table-scroll"><table class="recovery-table"><thead><tr><th>Event</th><th>Status</th><th>Attempts</th><th>Issue</th><th>Action</th></tr></thead><tbody>${events.items.map(r=>`<tr><td>${esc(r.kind)}<span class="lead-email">${esc(date(r.created_at))}</span></td><td>${badge(r.status)}</td><td>${r.attempts}</td><td>${esc(r.last_error||'—')}</td><td>${r.status==='failed'?`<button class="button secondary" data-event-retry="${esc(r.id)}">Retry ↻</button>`:'—'}</td></tr>`).join('')}</tbody></table></div>`:'<div class="info-body">Delivery events appear after qualification or a terminal processing failure.</div>'}</section><section class="panel"><div class="panel-heading"><div><h2>Automation failures</h2><p>Latest 100 reports from the n8n error handler.</p></div></div>${events.automation_errors?.length?`<div class="table-scroll"><table class="recovery-table"><thead><tr><th>Workflow</th><th>Execution</th><th>Issue</th><th>Time</th></tr></thead><tbody>${events.automation_errors.map(r=>`<tr><td>${esc(r.workflow)}</td><td>${esc(r.execution_id)}</td><td>${esc(r.message)}</td><td>${esc(date(r.created_at))}</td></tr>`).join('')}</tbody></table></div>`:'<div class="info-body">No automation failures have been reported.</div>'}</section>`;
    } else renderInsights(m);
    $('#updated-at').textContent='Updated '+new Date().toLocaleTimeString([],{hour:'2-digit',minute:'2-digit'});
  } catch(e) { if(state.view===current && state.generation===generation) target.innerHTML=`<div class="notice" role="alert">${esc(e.message)} Use Refresh to try again.</div>`; }
}
function renderInsights(m) {
  const ai=m.ai, reviewed=m.feedback.reviewed||0, accepted=m.feedback.accepted||0;
  const closed=(m.stages.won||0)+(m.stages.lost||0);
  const cost=ai.cost===null?'Not priced':'$'+Number(ai.cost).toFixed(4);
  const c=state.config;
  $('#view-content').innerHTML=`<div class="stats-grid">${stat('Closed-lead win rate',closed?Math.round((m.stages.won||0)/closed*100)+'%':'—','↗',`${m.stages.won||0} won · ${m.stages.lost||0} lost`)}${stat('Score acceptance',reviewed?Math.round(accepted/reviewed*100)+'%':'—','✓',`${reviewed} scores reviewed by the team`)}${stat('Recorded AI cost',cost,'◈',`${ai.unpriced_calls||0} calls unpriced or unknown`)}${stat('AI calls',ai.calls,'◌',`${ai.input_tokens||0} input · ${ai.output_tokens||0} output tokens`)}</div><div class="insight-grid"><section class="panel"><div class="panel-heading"><div><h2>Qualification policy</h2><p>Your ideal customer, made explicit.</p></div></div><div class="info-body"><p>${esc(c.rules.about_us)}</p><p><strong>Ideal customer</strong><br>${esc(c.rules.ideal_customer)}</p><div class="info-row"><span>Hot threshold</span><strong>${c.rules.hot_threshold}/10</strong></div><div class="info-row"><span>Warm threshold</span><strong>${c.rules.warm_threshold}/10</strong></div><div class="info-row"><span>Low confidence</span><strong>Human review</strong></div><div class="info-row"><span>Daily AI call limit</span><strong>${c.rules.max_daily_ai_calls||500}</strong></div><p>Policy changes are managed in the workspace’s scoring configuration. Restart the service to apply changes.</p></div></section><section class="panel"><div class="panel-heading"><div><h2>Workspace health</h2><p>Know what is ready and what needs a hand.</p></div></div><div class="info-body"><div class="info-row"><span>AI provider</span><strong>${c.demo?'Synthetic demo':c.provider_configured?'Configured':'Needs configuration'}</strong></div><div class="info-row"><span>Model</span><strong>${esc(c.model)}</strong></div><div class="info-row"><span>Notifications</span><strong>${c.webhook_configured?'Configured':'Paused'}</strong></div><div class="info-row"><span>Failed deliveries</span><strong>${m.failed_events}</strong></div><div class="info-row"><span>Retention</span><strong>${c.retention_days} days</strong></div><p>Cost uses your configured token rates. Unreported usage and unpriced calls are shown separately. Provider billing remains the source of truth.</p><p>Reply approval records your decision. Send the approved text through your usual email client.</p></div></section></div>`;
}
function bindFilters() {
  const apply=()=>{state.search=$('#lead-search').value;state.status=$('#status-filter').value;state.tier=$('#tier-filter').value;state.offset=0;loadView();};
  $('#apply-filters')?.addEventListener('click',apply);
  $('#lead-search')?.addEventListener('keydown',e=>{if(e.key==='Enter')apply();});
  $('#status-filter')?.addEventListener('change',apply); $('#tier-filter')?.addEventListener('change',apply);
  $('#previous-page')?.addEventListener('click',()=>{state.offset=Math.max(0,state.offset-50);loadView();});
  $('#next-page')?.addEventListener('click',()=>{state.offset+=50;loadView();});
}
function safeSourceLink(raw) { try { const u=new URL(raw); return ['http:','https:'].includes(u.protocol)?`<a class="source-link" href="${esc(u.href)}" target="_blank" rel="noopener noreferrer">${esc(u.href)} ↗</a>`:''; } catch(_){return '';} }
async function openLead(id) {
  const r=await api('/api/leads/'+encodeURIComponent(id)); state.currentLead=r;
  const e=r.enrichment, web=e?.website;
  const follow = r.follow_up_at ? new Date(r.follow_up_at*1000-new Date(r.follow_up_at*1000).getTimezoneOffset()*60000).toISOString().slice(0,16) : '';
  $('#lead-detail').innerHTML=`<div class="detail-heading"><h2>${esc(r.full_name)}</h2><p>${esc(r.email)} · ${esc(r.company_domain||'Company not provided')}</p>${badge(r.tier)} ${badge(r.status)} ${e?.demo?badge('demo'):''}</div>${r.last_error?`<div class="notice">${esc(r.last_error)}</div>`:''}<section class="detail-section"><h3>Their message</h3><p>${esc(r.message)}</p><small>Received ${esc(date(r.created_at))} · ${esc(r.source)}</small></section>${e?`<section class="detail-section"><h3>Company & qualification <span class="score">${r.score}/10</span></h3><p>${esc(e.company_summary)}</p><p><strong>${esc(e.industry)}</strong> · Size: ${esc(e.company_size_guess)} · ${esc(label(e.confidence))} confidence</p><p>${esc(e.why)}</p>${e.adjustments?.length?`<p>Rule adjustments: ${e.adjustments.map(a=>esc(a.label)+' ('+(a.points>0?'+':'')+a.points+')').join(', ')}</p>`:''}<small>Model score ${e.ai_score}/10 · ${esc(e.model)} · ${esc(date(e.enriched_at))}</small><p class="muted">AI usage: ${r.ai_usage?.calls||0} calls · Recorded cost: ${r.ai_usage?.cost==null?'unpriced':'$'+Number(r.ai_usage.cost).toFixed(4)} · ${r.ai_usage?.unpriced_calls||0} calls unpriced or unknown.</p><div class="detail-actions"><button class="button secondary" data-feedback="true">${r.score_accepted===1?'✓ ':''}Score looks right</button><button class="button secondary" data-feedback="false">${r.score_accepted===0?'✓ ':''}Needs improvement</button></div></section><section class="detail-section"><h3>Evidence & sources</h3>${web?.source_url?safeSourceLink(web.source_url):''}<p>Website: ${esc(label(web?.fetch_status||'not provided'))}${web?.cached?' · Cached result':''}${web?.fetched_at?' · '+esc(date(web.fetched_at)):''}</p>${e.evidence?.length?e.evidence.map(x=>`<blockquote>${esc(x.quote)}<br><small>Source: ${esc(x.source)}</small></blockquote>`).join(''):'<p>No supporting quotes. Review qualification before acting.</p>'}</section><section class="detail-section"><h3>First reply · ${r.reply_status==='approved'?'approved':'draft'}</h3><label class="honeypot" for="reply-draft">Reply draft</label><textarea id="reply-draft" rows="5" maxlength="3000">${esc(r.approved_reply||e.suggested_reply)}</textarea><div class="detail-actions"><button class="button primary" id="approve-reply">Approve for manual sending</button><button class="button secondary" id="copy-reply">Copy reply</button></div><p class="muted">Approval saves your reviewed text. No email is sent.</p></section>`:'<section class="detail-section"><h3>Qualification is on its way</h3><p>The original submission is safely stored. Enrichment will appear here when processing succeeds.</p></section>'}<form id="lead-edit"><section class="detail-section"><h3>Keep the next step moving</h3><div class="detail-grid"><div><label for="lead-owner">Owner</label><input id="lead-owner" name="owner" maxlength="150" value="${esc(r.owner)}"><label for="lead-stage">Sales stage</label><select id="lead-stage" name="stage">${['new','contacted','qualified','won','lost'].map(x=>`<option value="${x}" ${r.stage===x?'selected':''}>${label(x)}</option>`).join('')}</select></div><div><label for="follow-up">Follow-up time</label><input id="follow-up" name="follow_up" type="datetime-local" value="${esc(follow)}" max="2099-12-31T23:59"><label for="lead-notes">Team notes</label><textarea id="lead-notes" name="notes" rows="2" maxlength="2000">${esc(r.notes)}</textarea></div></div><div class="detail-actions"><button class="button primary" type="submit">Save next steps</button>${['failed','needs_review','needs_configuration'].includes(r.status)?`<button class="button secondary" type="button" data-retry="${esc(r.id)}">Retry enrichment ↻</button>`:''}<button class="button danger" type="button" id="delete-lead">Delete lead</button></div></section></form><section class="detail-section"><h3>Activity trail</h3><ul class="audit-list">${r.history.map(h=>`<li>${esc(label(h.kind))}${h.detail?' · '+esc(h.detail):''}<small>${esc(date(h.created_at))}</small></li>`).join('')}</ul></section>`;
  const dialog=$('#lead-dialog'); if(!dialog.open) dialog.showModal();
  $('#lead-edit').addEventListener('submit',event=>{event.preventDefault();const form=event.currentTarget;busy($('button[type="submit"]',form),async()=>{await api('/api/leads/'+r.id,'PATCH',{owner:form.elements.owner.value,stage:form.elements.stage.value,notes:form.elements.notes.value,follow_up_at:form.elements.follow_up.value?new Date(form.elements.follow_up.value).getTime()/1000:null});toast('Next steps saved.');await openLead(r.id);await loadView();});});
  $('#approve-reply')?.addEventListener('click',event=>busy(event.currentTarget,async()=>{await api('/api/leads/'+r.id+'/approve','POST',{reply:$('#reply-draft').value});toast('Reply approved for manual sending.');await openLead(r.id);}));
  $('#copy-reply')?.addEventListener('click',event=>busy(event.currentTarget,async()=>{await navigator.clipboard.writeText($('#reply-draft').value);toast('Reply copied.');}));
  $('#delete-lead').addEventListener('click',event=>busy(event.currentTarget,async()=>{if(!confirm('Delete this inquiry and its enrichment, history, and events? This cannot be undone.'))return;await api('/api/leads/'+r.id,'DELETE');dialog.close();toast('Lead deleted.');await loadView();}));
  document.querySelectorAll('[data-feedback]').forEach(button=>button.addEventListener('click',()=>busy(button,async()=>{await api('/api/leads/'+r.id,'PATCH',{score_accepted:button.dataset.feedback==='true'});toast('Scoring feedback saved.');await openLead(r.id);})));
}
async function startWorkspace() {
  state.config=await api('/api/config');
  $('#login-screen').hidden=true; $('#workspace').hidden=false;
  $('#environment').textContent=state.config.demo?'DEMO · SYNTHETIC DATA':'TEAM WORKSPACE';
  const note=$('#configuration-note');
  note.hidden=!state.config.demo && state.config.provider_configured;
  note.textContent=state.config.demo?'You’re exploring a demo with synthetic inquiries. No websites, AI providers, or notifications are contacted.': 'Your form is ready to receive leads. Add your AI provider configuration to enable enrichment; already received leads remain safely stored.';
  await loadView();
}
async function dashboardPage() {
  const publicConfig=await api('/api/public-config'); $('#demo-login').hidden=!publicConfig.demo;
  $('#login-form').addEventListener('submit',async e=>{e.preventDefault();const button=$('button[type="submit"]',e.currentTarget);button.disabled=true;$('#login-error').textContent='';try{await api('/api/login','POST',{password:$('#password').value});$('#password').value='';await startWorkspace();}catch(error){$('#login-error').textContent=error.message;}finally{button.disabled=false;}});
  document.querySelectorAll('[data-logout]').forEach(button=>button.addEventListener('click',e=>busy(e.currentTarget,async()=>{await api('/api/logout','POST',{});$('#lead-dialog').close();showLogin();})));
  $('#refresh').addEventListener('click',e=>busy(e.currentTarget,loadView));
  $('#close-dialog').addEventListener('click',()=>$('#lead-dialog').close());
  document.addEventListener('click',event=>{
    const view=event.target.closest('[data-view]'); if(view){state.view=view.dataset.view;state.offset=0;loadView();}
    const lead=event.target.closest('[data-lead]'); if(lead)busy(lead,()=>openLead(lead.dataset.lead));
    const retry=event.target.closest('[data-retry]'); if(retry)busy(retry,async()=>{await api('/api/leads/'+retry.dataset.retry+'/retry','POST',{});toast('Lead queued for another attempt.');if($('#lead-dialog').open)await openLead(retry.dataset.retry);await loadView();});
    const ev=event.target.closest('[data-event-retry]'); if(ev)busy(ev,async()=>{await api('/api/events/'+ev.dataset.eventRetry+'/retry','POST',{});toast('Event queued for redelivery.');await loadView();});
  });
  try { await startWorkspace(); } catch(e) { if(e.message!=='Please sign in.')$('#login-error').textContent=e.message; }
}
if($('#contact-form'))contactPage().catch(e=>toast(e.message));else dashboardPage().catch(e=>{const n=$('#login-error');if(n)n.textContent=e.message;});
