const test = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const path = require('node:path');

function page(script, replies, search='?session_id=cs_live_Qa123') {
  const elements = new Map();
  const calls = [];
  const node = id => {
    if (!elements.has(id)) elements.set(id, {textContent:'',hidden:false,value:'',attributes:{},
      setAttribute(name,value){this.attributes[name]=value;},getAttribute(name){return this.attributes[name];}});
    return elements.get(id);
  };
  const context = {document:{getElementById:node},URLSearchParams,URL,console,
    location:{search,pathname:'/dashboard.html'},history:{replaceState(...args){calls.push({history:args});}},
    fetch:async (url,options)=>{calls.push({url,options});const next=replies.shift();return {ok:next.status<400,status:next.status,json:async()=>next.data};}};
  vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../site',script),'utf8'),context);
  return {elements,calls,node};
}
const settle=()=>new Promise(resolve=>setImmediate(resolve));
test('checkout masks credentials and passes only the checkout ID to dashboard',async()=>{
  const p=page('thanks.js',[{status:200,data:{key:'fixture-private-key',plan:'free',credits_left:1000}}]);
  await settle();
  assert.equal(p.node('access').hidden,false);
  assert(!p.node('api-key').textContent.includes('fixture-private-key'));
  assert.equal(p.node('dashboard-link').href,'/dashboard.html?session_id=cs_live_Qa123');
  p.node('reveal').onclick();assert.equal(p.node('api-key').textContent,'fixture-private-key');
  p.node('reveal').onclick();assert(!p.node('api-key').textContent.includes('fixture-private-key'));
});
test('dashboard claims with browser cookie then authenticates API without exposing key',async()=>{
  const p=page('dashboard.js',[{status:200,data:{key:'fixture-private-key'}},
    {status:200,data:{plan:'free',active:true,credits_left:1000}}]);
  await settle();
  assert.equal(p.node('login').hidden,true);
  assert.equal(p.node('balance').textContent,'1,000 credits');
  assert.equal(p.calls.find(x=>x.url==='/api/account').options.headers.Authorization,'Bearer fixture-private-key');
  assert(!JSON.stringify([...p.elements]).includes('fixture-private-key'));
  assert.deepEqual(p.calls[0].history.slice(1),['','/dashboard.html']);
});
test('unpaid or wrong-browser checkout does not unlock dashboard',async()=>{
  const p=page('dashboard.js',[{status:403,data:{error:'Wrong browser'}}]);
  await settle();
  assert.equal(p.node('login').hidden,false);
  assert.equal(p.node('message').textContent,'Wrong browser');
  assert.equal(p.calls.filter(x=>x.url==='/api/account').length,0);
});
