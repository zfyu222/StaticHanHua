// Headless, synthetic offline-proposal fixture; not a real model response or game text.
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const {pathToFileURL}=require('node:url');
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||path.join(process.env.USERPROFILE,'.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright'));
(async()=>{
 const root=path.resolve(process.argv[2]);
 const browser=await chromium.launch({executablePath:process.env.EDGE_PATH||path.join(process.env['ProgramFiles(x86)'],'Microsoft/Edge/Application/msedge.exe'),headless:true});
 const page=await browser.newPage({viewport:{width:1500,height:1000},acceptDownloads:true});
 const errors=[];page.on('pageerror',e=>errors.push(String(e)));
 await page.goto(pathToFileURL(path.join(root,'review.html')).href);
 await page.locator('#filter').selectOption('vision');
 await page.locator('#images button').first().click();
 await page.locator('.region').filter({hasText:'Vision fixture'}).click();
 assert.equal(await page.locator('#text').inputValue(),'Vision fixture');
 await page.locator('#accept').click();
 const event=page.waitForEvent('download');await page.locator('#save').click();
 await (await event).saveAs(path.join(root,'proposal-browser-review.json'));
 const review=JSON.parse(fs.readFileSync(path.join(root,'proposal-browser-review.json'),'utf8'));
 const accepted=review.regions.filter(x=>x.decision==='accept');
 assert.equal(accepted.length,1);assert.equal(accepted[0].manual,true);
 assert.deepEqual(errors,[]);
 const result={status:'passed',checks:['offline proposal displayed','vision filter','manual confirmation','manual provenance retained on download'],synthetic_fixture:true,user_browser_control:false};
 fs.writeFileSync(path.join(root,'proposal-browser-test.json'),JSON.stringify(result,null,2));
 console.log(JSON.stringify(result));await browser.close();
})().catch(e=>{console.error(e);process.exitCode=1;});
