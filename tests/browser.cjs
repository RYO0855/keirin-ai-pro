// Browser tests use explicitly artificial schema fixtures, never live test results.
const { chromium }=require('playwright');
const http=require('node:http'),fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const day='2026-10-18',key='test-20261018-1';
const index={schema_version:'1.0',domain:'keirin',races_count:1,races:[{race_key:key,race_date:day,race_no:1,place:'四日市',grade:'F2',url:'https://keirin.yomisuji.jp/'+key+'/',top:{id:1,racer_id:'999001',score:88}}]};
const detail={schema_version:'1.0',domain:'keirin',race_key:key,race_date:day,race_no:1,place:'四日市',predictions:[{id:1,racer_id:'999001',score:88},{id:2,racer_id:'999002',score:66},{id:3,racer_id:'999003',score:55}],meta:{lines:[{label:'テスト用ライン',members:['999001','999002','999003']}]}};
(async()=>{
 const server=http.createServer((req,res)=>{res.setHeader('Content-Type','text/html; charset=utf-8');res.end(fs.readFileSync(process.env.APP_HTML||path.join(__dirname,'../index.html')))});await new Promise(r=>server.listen(0,'127.0.0.1',r));
 let browser;try{
 browser=await chromium.launch({headless:true});const ctx=await browser.newContext({viewport:{width:390,height:844},isMobile:true,hasTouch:true});const page=await ctx.newPage(),errors=[];page.on('pageerror',e=>errors.push(e.message));let indexCalls=0,detailCalls=0;
 await page.route('https://keirin.yomisuji.jp/**',route=>{const isIndex=route.request().url().endsWith('/index.json');if(isIndex)indexCalls++;else detailCalls++;return route.fulfill({status:200,contentType:'application/json',headers:{'access-control-allow-origin':'*'},body:JSON.stringify(isIndex?index:detail)})});
 const url='http://127.0.0.1:'+server.address().port;await page.goto(url);
 assert.equal(await page.locator('#source').inputValue(),'sample');
 // Player notes are never probability increments; verification requires evidence.
 await page.locator('[data-tab="notes"]').click();
 await page.locator('#noteTitle').fill('連係メモのテスト');await page.locator('#noteContent').fill('未検証の観測内容');await page.locator('#noteForm button[type="submit"]').click();
 assert.equal(await page.evaluate(()=>personal.notes[0].verified),false);
 await page.locator('[data-tab="scenario"]').click();assert.equal(await page.locator('#panel').getByText('連係メモのテスト',{exact:false}).count(),0);
 await page.locator('[data-tab="notes"]').click();await page.locator('[data-note-edit]').click();
 await page.locator('#noteVerified').check();await page.locator('#noteForm button[type="submit"]').click();assert.equal(await page.evaluate(()=>personal.notes[0].verified),false);
 await page.locator('#noteEvidence').fill('テスト用映像');await page.locator('#noteURL').fill('https://example.com/evidence');await page.locator('#noteQuote').fill('テスト映像の1分10秒');await page.locator('#noteVerified').check();await page.locator('#noteForm button[type="submit"]').click();
 assert.equal(await page.evaluate(()=>personal.notes[0].verified),true);
 await page.locator('[data-tab="scenario"]').click();assert.match(await page.locator('#panel').innerText(),/連係メモのテスト/);assert.match(await page.locator('#panel').innerText(),/的中確率へ加算しません/);
 await page.locator('[data-tab="notes"]').click();await page.locator('[data-note-edit]').click();await page.locator('#noteContent').fill('編集した内容');assert.equal(await page.locator('#noteVerified').isChecked(),false);await page.locator('#noteForm button[type="submit"]').click();assert.equal(await page.evaluate(()=>personal.notes[0].verified),false);
 await page.locator('[data-tab="value"]').click();assert.equal(await page.locator('[data-group="exacta"]').count(),4);assert.equal(await page.locator('[data-group="trifecta"]').count(),10);assert.equal(await page.locator('[data-group="holes"]').count(),8);
 await page.locator('[data-group="exacta"]').first().fill('1-2');await page.locator('[data-group="trifecta"]').first().fill('1-2-3');await page.locator('[data-group="holes"]').first().fill('1-2-3');await page.locator('#ticketForm button[type="submit"]').click();assert.equal(await page.evaluate(()=>Object.keys(personal.plans).length),0);
 await page.locator('[data-group="holes"]').first().fill('1-3-2');await page.locator('#ticketForm button[type="submit"]').click();assert.equal(await page.evaluate(()=>personal.plans['sample|s1'].holes[0]),'1-3-2');assert.match(await page.locator('#ticketSummary').innerText(),/判定：ケン/);
 await page.reload();assert.equal(await page.evaluate(()=>personal.notes.length),1);await page.locator('[data-tab="value"]').click();assert.equal(await page.locator('[data-group="exacta"]').first().inputValue(),'1-2');

 await page.locator('[data-tab="analysis"]').click();await page.locator('#loadLive').click();await page.locator('#saveLineup').waitFor();assert.equal(await page.locator('#source').inputValue(),'live');assert.match(await page.locator('#stats').innerText(),/提供元のデータ更新日時\s*不明/);assert.match(await page.locator('#panel').innerText(),/999001/);
 assert.equal(await page.evaluate(()=>race().start),null);
 await page.locator('[data-tab="notes"]').click();assert.match(await page.locator('#panel').innerText(),/保存済みメモ 0件/);

 await page.locator('[data-tab="value"]').click();assert.match(await page.locator('#panel').innerText(),/オッズ期待値：ケン（データ不足）/);assert.equal(await page.locator('#prob').count(),0);
 await page.locator('[data-tab="analysis"]').click();await page.locator('#saveLineup').click();await page.locator('#saveLineup').click();assert.equal(await page.evaluate(()=>evidence.observations.length),2);assert.equal(await page.evaluate(()=>rateKnown(evidence.observations,'followed').n),0);
 await page.locator('[data-tab="observations"]').click();await page.locator('#profileName').fill('テスト先頭');await page.locator('#profilePref').selectOption('三重');await page.locator('#profileSource').fill('テスト資料');await page.locator('#saveProfile').click();
 await page.locator('#profileId').selectOption('999002');await page.locator('#profileName').fill('テスト番手');await page.locator('#profilePref').selectOption('愛知');await page.locator('#profileSource').fill('テスト資料');await page.locator('#saveProfile').click();assert.equal(await page.evaluate(()=>sameRegion(profile('999001').prefecture,profile('999002').prefecture)),true);
 await page.locator('#followed').selectOption('true');await page.locator('#dropped').selectOption('true');await page.locator('#resultSource').fill('テスト映像');await page.locator('#saveResult').click();assert.equal(await page.evaluate(()=>evidence.observations[0].followed),null);
 await page.locator('#dropped').selectOption('false');await page.locator('#saveResult').click();assert.equal(await page.evaluate(()=>rateKnown(evidence.observations,'followed').value),1);assert.equal(await page.evaluate(()=>rateKnown(evidence.observations,'dropped').value),0);
 const downloadPromise=page.waitForEvent('download');await page.locator('#exportEvidence').click();assert.equal((await downloadPromise).suggestedFilename(),'keirin-observations.json');
 await page.reload();await page.locator('#source').selectOption('live');await page.locator('[data-tab="observations"]').click();assert.equal(await page.evaluate(()=>evidence.observations.length),2);assert.equal(await page.evaluate(()=>evidence.profiles.length),2);
 // Bad import must preserve saved records.
 await page.locator('#evidenceFile').setInputFiles({name:'bad.json',mimeType:'application/json',buffer:Buffer.from('{"version":1,"profiles":[],"observations":[{}]}')});assert.equal(await page.evaluate(()=>evidence.observations.length),2);
 // Out-of-schema index and fetch failure must not fabricate races.
 await page.unroute('https://keirin.yomisuji.jp/**');await page.route('https://keirin.yomisuji.jp/**',r=>r.fulfill({status:200,contentType:'application/json',body:'{"schema_version":"2.0"}'}));await page.locator('#loadLive').click();await page.waitForFunction(()=>!indexBusy);assert.equal(await page.evaluate(()=>live.races.length),0);assert.match(await page.locator('#liveStatus').innerText(),/取得失敗/);
 await page.unroute('https://keirin.yomisuji.jp/**');await page.route('https://keirin.yomisuji.jp/**',r=>r.fulfill({status:503,body:'unavailable'}));await page.locator('#loadLive').click();await page.waitForFunction(()=>!indexBusy);assert.match(await page.locator('#liveStatus').innerText(),/503/);
 assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
 assert.deepEqual(errors,[]);assert.equal(indexCalls,1);assert.equal(detailCalls,1);console.log('PASS: mobile Chromium interactions, unknown results, score/probability separation, deduplication, evidence persistence/import/export, HTTP/schema failures, viewport');
 }finally{if(browser)await browser.close();await new Promise(r=>server.close(r))}
})().catch(e=>{console.error(e);process.exitCode=1});
