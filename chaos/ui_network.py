"""Pure graph contracts and deterministic portable SVG; no execution or network."""
import html,math

STATUSES=('not-observed','queued','running','completed','failed','cancelled','interrupted','uncertain','open','closed','waiting','unknown','idle','blocked')

def validate_graph(props):
    from .ui_artifacts import _error
    nodes,edges=props['nodes'],props['edges'];ids=set();links=set()
    def identity(value):return type(value)is str and 1<=len(value)<=128 and all(ord(c)>=32 and ord(c)!=127 for c in value)
    for node in nodes:
        if not identity(node['id'])or node['id']in ids:_error('NetworkGraph requires unique bounded node ids')
        ids.add(node['id'])
    for edge in edges:
        if not identity(edge['id'])or edge['id']in links:_error('NetworkGraph requires unique bounded edge ids')
        links.add(edge['id'])
        if edge['source']not in ids or edge['target']not in ids:_error('NetworkGraph edge references unknown node')
    return props


def network_html(props):
    esc=lambda v:html.escape(str(v),quote=True)
    nodes,edges=props['nodes'],props['edges'];ordered=sorted(nodes,key=lambda n:(n['group'],n['kind'],n['id']));positions={}
    groups=sorted({n['group']or n['kind']for n in ordered})
    for i,node in enumerate(ordered):
        if props['layout']=='layered':
            group=node['group']or node['kind'];members=[n for n in ordered if(n['group']or n['kind'])==group]
            x=70+860*(groups.index(group)+.5)/max(1,len(groups));y=35+520*(members.index(node)+.5)/max(1,len(members))
        else:
            angle=2*math.pi*i/max(1,len(ordered))-math.pi/2;x=500+420*math.cos(angle);y=300+245*math.sin(angle)
        positions[node['id']]=(round(x,3),round(y,3))
    paths=[]
    for edge in edges:
        x,y=positions[edge['source']];a,b=positions[edge['target']]
        paths.append('<path d="M'+str(x)+' '+str(y)+' Q500 300 '+str(a)+' '+str(b)+'" fill="none" stroke="currentColor" opacity=".18"><title>'+esc(edge['kind'])+'</title></path>')
    circles=[]
    for node in ordered:
        x,y=positions[node['id']];circles.append('<circle cx="'+str(x)+'" cy="'+str(y)+'" r="4" fill="currentColor" tabindex="0"><title>'+esc(node['label']+' · '+node['status'])+'</title></circle>')
    return '<section class="ui-network-export"><h2>'+esc(props['title'])+'</h2><p>程序提供的静态数据 · '+str(len(nodes))+' 节点 / '+str(len(edges))+' 关系</p><svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1000 600" role="img" aria-label="'+esc(props['title'])+'">'+''.join(paths+circles)+'</svg></section>'
