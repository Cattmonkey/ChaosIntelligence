"""Drive the public workbench controls, retaining legacy feature test assertions."""

def command(page, command_id):
    page.wait_for_function('()=>!!window.ChaosWorkbench')
    label=page.evaluate('(id)=>ChaosWorkbench.commands.find(c=>c.id===id)?.label',command_id)
    assert label, 'Unregistered workbench command: '+command_id
    page.locator('#wbCommandButton').click()
    page.locator('#wbCommandInput').fill(command_id)
    page.locator('#wbCommandList [role="option"]').filter(has_text=label).first.click()
    page.locator('#wbCommands').wait_for(state='hidden')

def button(page, target_id):
    target=page.locator('#'+target_id)
    if target.is_visible():
        target.click()
        return
    page.wait_for_function('()=>!!window.ChaosWorkbench')
    command_id=page.evaluate('(id)=>ChaosWorkbench.commands.find(c=>c.target===id)?.id',target_id)
    assert command_id, 'Unregistered workbench control: '+target_id
    command(page,command_id)

def view(page, name):
    command(page,'graph' if name=='graph' else 'view.'+name)
    page.locator('#'+name+'View').wait_for(state='visible')

def run_options(page):
    if not page.locator('#taskInput').is_visible():
        command(page,'run.options')


def run_option(page, target_id):
    run_options(page)
    return page.locator('#'+target_id)
