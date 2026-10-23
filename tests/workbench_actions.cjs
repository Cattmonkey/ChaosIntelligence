// Browser-only navigation helpers: operate visible workbench controls.
const assert=require('assert');
async function command(page,id){
 await page.waitForFunction(()=>!!window.ChaosWorkbench);
 const label=await page.evaluate(id=>ChaosWorkbench.commands.find(c=>c.id===id)?.label,id);assert(label,'Unregistered workbench command: '+id);
 await page.locator('#wbCommandButton').click();await page.locator('#wbCommandInput').fill(id);
 await page.locator('#wbCommandList [role="option"]').filter({hasText:label}).first().click();await page.locator('#wbCommands').waitFor({state:'hidden'});
}
async function button(page,id){const target=page.locator('#'+id);if(await target.isVisible())return target.click();
 await page.waitForFunction(()=>!!window.ChaosWorkbench);const cid=await page.evaluate(id=>ChaosWorkbench.commands.find(c=>c.target===id)?.id,id);assert(cid,'Unregistered workbench control: '+id);await command(page,cid);}
async function view(page,name){await command(page,name==='graph'?'graph':'view.'+name);await page.locator('#'+name+'View').waitFor({state:'visible'});}
module.exports={command,button,view};
