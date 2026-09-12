"""Execute the actual frontend probe controller without paid requests."""
import shutil
import subprocess
from pathlib import Path

import pytest


def test_manual_model_and_supplier_tests_are_scoped_and_bounded() -> None:
    node = shutil.which("node")
    if not node:
        pytest.skip("Node unavailable")
    script = r'''
const fs=require('fs'),vm=require('vm'),assert=require('assert');
const source=fs.readFileSync('web/app.js','utf8');
const route=(provider,model,capability='text',extra={})=>({provider,model,capability,protocol:'responses',account_fingerprint:'key',route_id:provider+model+capability,configured:true,approved:true,...extra});
const a=route('a','same'), b=route('b','same'), image=route('a_image','image','image_generation');
const ctx={providerControlData:{routes:[a,b,image,route('a','same','vision'),route('a','tool','tool_call'),route('a','candidate','text',{approved:false}),route('a','no-key','text',{configured:false}),a]},
 providerControlFamily:p=>p.startsWith('a')?'A':'B',document:{querySelectorAll:()=>[]},platformConfirm:async()=>true,
 showProviderControlNotice:()=>{},loadProviderControl:async()=>{},setTimeout:()=>1,window:{innerHeight:800}};
vm.createContext(ctx);
vm.runInContext(source.slice(source.indexOf('const providerManualTestReserved'),source.indexOf('function compactProviderModels')),ctx);
(async()=>{
 const single=ctx.providerManualTestRoutes(['a','key','same','responses']);
 assert.equal(single.length,1);assert.equal(single[0].provider,'a');
 const all=ctx.providerManualTestRoutes(null,'A');assert.equal(all.length,2);
 const calls=[];let refreshes=0;
 ctx.api=async(url,options)=>{const body=JSON.parse(options.body);calls.push(body);if(body.provider==='a')throw Error('probe failed');return {ok:true}};
 ctx.loadProviderControl=async()=>{refreshes++};
 await ctx.runProviderManualTests(all,'A',true);
 assert.equal(calls.length,2);assert.equal(refreshes,2);assert.equal(calls[1].capability,'image_generation');
 assert(calls.every(c=>c.provider!=='b'&&c.capability!=='vision'));
 ctx.platformConfirm=async()=>false;
 await ctx.runProviderManualTests(all,'A',true);assert.equal(calls.length,2);
 const releases=[];ctx.api=()=>new Promise(resolve=>releases.push(resolve));
 const pending=ctx.runProviderManualTests(single,'A');
 await ctx.runProviderManualTests(single,'A'); // must not dispatch duplicate
 const other=ctx.runProviderManualTests(ctx.providerManualTestRoutes(['b','key','same','responses']),'B');
 assert.equal(releases.length,2); // unrelated model remains independently testable
 releases.forEach(resolve=>resolve({ok:true}));await Promise.all([pending,other]);
 assert.equal(refreshes,4);
 let clock=1000,top=900;ctx.Date={now:()=>clock};
 const button={dataset:{testRoutes:JSON.stringify([a.route_id]),testLabel:'测试',testBatch:'false'},setAttribute(){},closest:()=>null,
   getBoundingClientRect:()=>({height:30,top,bottom:top+30})};
 ctx.document.querySelectorAll=()=>[button];
 ctx.finishProviderManualTest(a.route_id,true);clock=20000;ctx.syncProviderManualTestButtons();
 assert.equal(button.textContent,'✓ 已通过'); // unseen completion must not expire
 top=10;ctx.syncProviderManualTestButtons();clock+=3100;ctx.syncProviderManualTestButtons();
 assert.equal(button.textContent,'✓ 已通过');
 clock+=4000;ctx.syncProviderManualTestButtons();
 assert.equal(button.textContent,'测试');assert.equal(button.disabled,false);
})().catch(e=>{console.error(e);process.exitCode=1});
'''
    subprocess.run([node, "-e", script], cwd=Path(__file__).resolve().parents[1], check=True, capture_output=True, text=True)
