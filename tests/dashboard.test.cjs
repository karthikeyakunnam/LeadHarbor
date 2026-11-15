const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const {JSDOM}=require('jsdom');
const source=fs.readFileSync('leadharbor/static/app.js','utf8');
const rules=JSON.parse(fs.readFileSync('config/scoring.json','utf8'));
const now=Date.now()/1000;
const lead={id:'lead-001',full_name:'Jane Doe',email:'jane@example.com',company_domain:'example.com',message:'We need product engineering help.',
  source:'public_form',tier:'hot',score:9,status:'ready',owner:'Sales',created_at:now,updated_at:now,follow_up_at:now+3600,stage:'new',reply_status:'draft',notes:'',attempts:1,
  enrichment:{company_summary:'A software company.',industry:'Software',company_size_guess:'unknown',confidence:'medium',why:'A clear product need.',suggested_reply:'Hi Jane, could we arrange a short call?',
    ai_score:8,model:'test-model',enriched_at:now,adjustments:[],website:{website_fetched:true,fetch_status:'ok',source_url:'https://example.com/',fetched_at:now},evidence:[{source:'message',quote:'product engineering help'}]},history:[{kind:'received',detail:'public_form',created_at:now}]};
const metrics={total:1,contacts:1,statuses:{ready:1},tiers:{hot:1},stages:{new:1},overdue:0,feedback:{reviewed:0,accepted:0},ai:{calls:0,cost:null,input_tokens:null,output_tokens:null,unpriced_calls:0},failed_events:0};
async function waitFor(condition,message='condition') { for(let i=0;i<150;i++){if(condition())return;await new Promise(r=>setImmediate(r));}throw new Error('Timed out: '+message); }
function boot({contact=false,authenticated=true,items=[structuredClone(lead)],handler}={}) {
  const dom=new JSDOM(fs.readFileSync('leadharbor/static/'+(contact?'contact.html':'index.html'),'utf8'),{url:'http://localhost:8080'+(contact?'/contact':''),runScripts:'outside-only'});
  const w=dom.window,calls=[];let signedIn=authenticated;
  w.HTMLDialogElement.prototype.showModal=function(){this.open=true;};w.HTMLDialogElement.prototype.close=function(){this.open=false;};
  Object.defineProperty(w.navigator,'clipboard',{value:{writeText:async()=>{}}});w.confirm=()=>true;
  w.fetch=async(path,options)=>{
    calls.push({path,...options,data:options.body?JSON.parse(options.body):undefined});
    if(handler){const result=await handler(path,options,calls);if(result)return result;}
    const response=(data,status=200)=>({ok:status<400,status,json:async()=>structuredClone(data)});
    if(path==='/api/public-config')return response({demo:true,privacy_contact:'our team',retention_days:90});
    if(path==='/api/login'){signedIn=true;return response({ok:true});}
    if(path==='/api/logout'){signedIn=false;return response({ok:true});}
    if(!signedIn)return response({error:'Please sign in.'},401);
    if(path==='/api/config')return response({demo:true,provider_configured:false,webhook_configured:false,model:'test-model',rules,retention_days:90});
    if(path==='/api/metrics')return response(metrics);
    if(path==='/api/events')return response({items:[],automation_errors:[{workflow:'LeadHarbor capture',execution_id:'42',message:'Execution failed',created_at:now}]});
    if(path.startsWith('/api/leads?')){
      const q=new URL(path,'http://localhost').searchParams;
      const rows=q.get('status')?items.filter(x=>x.status===q.get('status')):items;
      return response({items:rows,total:rows.length,limit:50,offset:0});
    }
    if(path.startsWith('/api/leads/'))return response(options.method==='GET'?items[0]:{ok:true});
    throw new Error('Unmocked request: '+path);
  };
  w.eval(source);
  return {dom,w,calls,$:s=>w.document.querySelector(s),close:()=>w.close()};
}

test('dashboard renders real records, metrics, and a demo banner',async()=>{
  const b=boot();try{await waitFor(()=>b.$('.lead-name'),'lead inbox');assert.equal(b.$('#workspace').hidden,false);assert.match(b.$('#view-content').textContent,/Jane Doe/);assert.match(b.$('#configuration-note').textContent,/synthetic/);assert.equal(b.$('#nav-count').textContent,'1');assert.equal(b.$('.stat-number').textContent,'1');}finally{b.close();}
});
test('login and logout use same-origin APIs and clear the workspace',async()=>{
  const b=boot({authenticated:false});try{await waitFor(()=>!b.$('#demo-login').hidden,'login');b.$('#password').value='test-password';b.$('#login-form').dispatchEvent(new b.w.Event('submit',{bubbles:true,cancelable:true}));await waitFor(()=>!b.$('#workspace').hidden,'signed in');assert.equal(b.$('#password').value,'');const call=b.calls.find(c=>c.path==='/api/login');assert.equal(call.headers['X-LeadHarbor'],'1');b.$('#logout').click();await waitFor(()=>b.$('#workspace').hidden,'signed out');}finally{b.close();}
});
test('lead text is escaped and cannot create HTML elements',async()=>{
  const b=boot({items:[{...structuredClone(lead),full_name:'<img src=x onerror=alert(1)>',company_domain:'<script>bad()</script>'}]});try{await waitFor(()=>b.$('.lead-name'));assert.equal(b.$('#view-content img'),null);assert.equal(b.$('#view-content script'),null);assert.match(b.$('.lead-name').textContent,/<img/);}finally{b.close();}
});
test('inbox search passes the selected filters to the API',async()=>{
  const b=boot();try{await waitFor(()=>b.$('.lead-name'));b.$('[data-view="inbox"]').click();await waitFor(()=>b.$('#lead-search'));b.$('#lead-search').value='Jane';b.$('#tier-filter').value='hot';b.$('#apply-filters').click();await waitFor(()=>b.calls.some(c=>c.path.includes('search=Jane')&&c.path.includes('tier=hot')),'filtered API call');}finally{b.close();}
});
test('lead detail approval, feedback, and next steps call the correct endpoints',async()=>{
  const b=boot();try{
    await waitFor(()=>b.$('.lead-name'));b.$('.lead-name').click();await waitFor(()=>b.$('#approve-reply'));
    assert.equal(b.$('#lead-dialog').open,true);assert.match(b.$('#lead-detail').textContent,/product engineering help/);
    b.$('#reply-draft').value='A carefully reviewed reply.';b.$('#approve-reply').click();
    await waitFor(()=>b.calls.some(c=>c.path==='/api/leads/lead-001/approve'),'approval');
    assert.equal(b.calls.find(c=>c.path.endsWith('/approve')).data.reply,'A carefully reviewed reply.');
    await waitFor(()=>!b.$('#approve-reply').disabled);
    b.$('[data-feedback="true"]').click();await waitFor(()=>b.calls.some(c=>c.method==='PATCH'&&c.data.score_accepted===true),'score feedback');
    await waitFor(()=>b.$('#lead-edit'));
    b.$('#lead-owner').value='Priya';b.$('#lead-stage').value='qualified';b.$('#lead-edit').dispatchEvent(new b.w.Event('submit',{bubbles:true,cancelable:true}));
    await waitFor(()=>b.calls.some(c=>c.method==='PATCH'&&c.data.owner==='Priya'),'saved owner');
    assert.equal(b.calls.filter(c=>/send|email/.test(c.path)).length,0);
  }finally{b.close();}
});
test('recovery and insights render delivery errors and unknown costs',async()=>{
  const b=boot();try{await waitFor(()=>b.$('.lead-name'));b.$('[data-view="recovery"]').click();await waitFor(()=>b.$('#view-content').textContent.includes('Automation failures'),'recovery');assert.match(b.$('#view-content').textContent,/Execution failed/);b.$('[data-view="insights"]').click();await waitFor(()=>b.$('#view-content').textContent.includes('Qualification policy'),'insights');assert.match(b.$('#view-content').textContent,/Not priced/);assert.match(b.$('#view-content').textContent,/Synthetic demo/);}finally{b.close();}
});
test('empty workspace explains the next action without fabricated leads',async()=>{
  const b=boot({items:[]});try{await waitFor(()=>b.$('.empty'));assert.match(b.$('.empty').textContent,/Share your contact form/);assert.equal(b.$('.lead-name'),null);}finally{b.close();}
});
test('public form keeps its idempotency key on network retries and renews it for edited content',async()=>{
  let submits=0;
  const b=boot({contact:true,handler:async(path,options)=>{if(path==='/api/submit'){submits++;if(submits<3)throw new Error('Connection interrupted');return {ok:true,status:202,json:async()=>({id:'receipt-001',received:true})};}}});
  try{
    await waitFor(()=>b.$('#contact-form').textContent.includes('Demo form'));
    b.$('#full-name').value='Jane Doe';b.$('#email').value='jane@example.com';b.$('#message').value='A synthetic project inquiry';b.$('[name="consent"]').checked=true;
    const submit=()=>b.$('#contact-form').dispatchEvent(new b.w.Event('submit',{bubbles:true,cancelable:true}));
    submit();await waitFor(()=>b.$('#contact-error').textContent==='Connection interrupted');
    submit();await waitFor(()=>submits===2&&!b.$('button[type="submit"]').disabled);
    b.$('#message').value='An edited synthetic project inquiry';submit();await waitFor(()=>!b.$('#contact-success').hidden,'receipt');
    const calls=b.calls.filter(c=>c.path==='/api/submit');assert.equal(calls[0].headers['Idempotency-Key'],calls[1].headers['Idempotency-Key']);assert.notEqual(calls[1].headers['Idempotency-Key'],calls[2].headers['Idempotency-Key']);
    assert.match(b.$('#receipt').textContent,/receipt-001/);assert.equal(calls[2].data.consent,true);
  }finally{b.close();}
});
