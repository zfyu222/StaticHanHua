// Headless test of generated UI. Does not open or control any user browser.
const {pathToFileURL}=require('node:url');
const path=require('node:path'),fs=require('node:fs'),assert=require('node:assert/strict');
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||path.join(process.env.USERPROFILE,'.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright'));
(async()=>{
 const root=path.resolve(process.argv[2]);
 const browser=await chromium.launch({executablePath:process.env.EDGE_PATH||path.join(process.env['ProgramFiles(x86)'],'Microsoft/Edge/Application/msedge.exe'),headless:true});
 const page=await browser.newPage({viewport:{width:1500,height:1000},acceptDownloads:true});
 const errors=[];page.on('pageerror',error=>errors.push(String(error)));
 await page.goto(pathToFileURL(path.join(root,'review.html')).href);
 await page.locator('#search').fill('1078.png');
 await page.locator('#images button').first().click();
 await page.locator('#picture').evaluate(im=>im.decode());
 await page.locator('#zoom').selectOption('2');
 assert.ok(await page.locator('.region').count()>2);
 await page.locator('.region').filter({hasText:'INFANT'}).first().click();
 await page.locator('#accept').click();
 await page.locator('#imageDecision').selectOption('has_text');
 await page.locator('#draw').click();
 const box=await page.locator('#canvas').boundingBox();
 await page.mouse.move(box.x+10,box.y+10);await page.mouse.down();
 await page.mouse.move(box.x+80,box.y+45);await page.mouse.up();
 await page.locator('#text').fill('Manual fixture');
 await page.locator('#accept').click();
 const event=page.waitForEvent('download');await page.locator('#save').click();
 const download=await event;await download.saveAs(path.join(root,'browser-test-review.json'));
 const result=JSON.parse(fs.readFileSync(path.join(root,'browser-test-review.json'),'utf8'));
 assert.equal(result.regions.filter(r=>r.decision==='accept').length,2);
 assert.equal(result.regions.filter(r=>r.manual).length,1);
 await page.locator('#import').setInputFiles(path.join(root,'browser-test-review.json'));
 await page.screenshot({path:path.join(root,'review-ui.png'),fullPage:true});
 assert.deepEqual(errors,[]);
 const checks={status:'passed',browser:'headless Edge',user_browser_control:false,
  checks:['load','resource search','select text region','confirm','image conclusion','draw missed region','download','reload review'],page_errors:errors};
 fs.writeFileSync(path.join(root,'browser-test.json'),JSON.stringify(checks,null,2));
 console.log(JSON.stringify(checks));await browser.close();
})().catch(e=>{console.error(e);process.exitCode=1;});
