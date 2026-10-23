"""Browser expected colors follow public semantic theme roles, not an old palette."""
def theme_color(page, role):
    if role not in ('function', 'property'):
        raise AssertionError('Unknown semantic color assertion role')
    return page.evaluate('''name => {
        const token = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
        if (!token) throw new Error('Missing semantic theme color: ' + name);
        const probe = document.createElement('span');
        probe.style.color = token;
        if (!probe.style.color) throw new Error('Invalid semantic theme color: ' + name);
        document.body.append(probe);
        try { return getComputedStyle(probe).color; }
        finally { probe.remove(); }
    }''', '--syntax-' + role)
