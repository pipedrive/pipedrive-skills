"""Compose Claude layouts around tested components, or check an opted-in custom design."""

import argparse
import hashlib
from html import escape, unescape
from html.parser import HTMLParser
import importlib.util
import json
from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from activity_evidence import DATE_EXPLANATION
from artifact_delivery import delivery_options
from report_identity import report_skill

DESIGN_OPTIONS = [
    {'id':'combined', 'default':True, 'label':'Combined (recommended)',
     'advantages':'Claude arranges tested charts and report sections; numbers, labels and interactions stay consistent.',
     'disadvantages':'Chart structure is fixed; layout is more flexible than the chart design.'},
    {'id':'claude_designed', 'default':False, 'label':'Claude designs the dashboard',
     'advantages':'Claude can tailor the whole layout and chart design to this report.',
     'disadvantages':'Appearance can vary between runs; labels, interactions and displayed facts need extra review.'},
]

# Custom scripts retain chart interactions, but cannot load or send report data.
DASHBOARD_CSP = ("default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; "
                 "img-src data:; connect-src 'none'; font-src 'none'; media-src 'none'; "
                 "object-src 'none'; frame-src 'none'; worker-src 'none'; "
                 "form-action 'none'; base-uri 'none'")
CSP_META = '<meta http-equiv="Content-Security-Policy" content="' + DASHBOARD_CSP + '">'


def restrict_resources(html):
    head = re.search(r'<head\b[^>]*>', html, re.I)
    if head:
        return html[:head.end()] + CSP_META + html[head.end():]
    body = re.search(r'<body\b[^>]*>', html, re.I)
    if body:
        return html[:body.start()] + '<head>' + CSP_META + '</head>' + html[body.start():]
    raise ValueError('Custom dashboard needs a head or body for resource restrictions')


class MainComponents(HTMLParser):
    VOID = {'area','base','br','col','embed','hr','img','input','link','meta','param','source','track','wbr'}

    def __init__(self, html):
        super().__init__(convert_charrefs=False)
        self.html=html; self.offsets=[0]
        for match in re.finditer('\n',html): self.offsets.append(match.end())
        self.depth=0; self.inside=False; self.start=None; self.parts=[]
        self.feed(html)

    def source_offset(self):
        row,col=self.getpos(); return self.offsets[row-1]+col

    def handle_starttag(self,tag,attrs):
        if tag=='main' and not self.inside:
            self.inside=True; return
        if not self.inside: return
        if self.depth==0: self.start=self.source_offset()
        if tag in self.VOID:
            if self.depth==0: self.parts.append(self.html[self.start:self.source_offset()+len(self.get_starttag_text())])
        else: self.depth+=1

    def handle_startendtag(self,tag,attrs):
        if self.inside and self.depth==0:
            self.parts.append(self.get_starttag_text())

    def handle_endtag(self,tag):
        if not self.inside: return
        if tag=='main' and self.depth==0:
            self.inside=False; return
        self.depth-=1
        if self.depth==0:
            self.parts.append(self.html[self.start:self.html.index('>',self.source_offset())+1])


def prepare(result, role='rep', capabilities=None):
    gate = delivery_options(result, capabilities or {})
    if gate.get('status') == 'blocked':
        raise ValueError(gate['reason'])
    if gate.get('status') == 'unsupported':
        # A dashboard built without the delivery profile was published anyway in two 0.7.9/0.7.10 Cowork runs, while a
        # third ran the profile and refused, so the route depended on whether the chat ran the check (0.7.10).
        raise ValueError('No validated delivery route: run artifact_delivery.py profile and pass its output as '
                         '--capabilities; keep the text reply.')
    spec=importlib.util.spec_from_file_location('_pd_dashboard_renderer',Path(__file__).with_name('render_artifact.py'))
    renderer=importlib.util.module_from_spec(spec); spec.loader.exec_module(renderer)
    if report_skill(result)=='deal-triage':
        html=renderer.render_html(result,role,capabilities)
    else: html=renderer.render_html(result)
    check_local(html, allow_scripts=True, allowed_links=allowed_deal_links(result))
    parts=MainComponents(html).parts
    if not parts: raise ValueError('No completed dashboard components were rendered')
    result_bytes=json.dumps(result,sort_keys=True,ensure_ascii=False)
    title = re.search(r'<h1\b[^>]*>(.*?)</h1>', html, re.S)
    title = unescape(re.sub(r'<[^>]+>', '', title.group(1))) if title else report_skill(result)
    if result.get('today') and str(result['today']) not in title:
        title += ' — ' + str(result['today'])
    return {'schema_version':1, 'title':title, 'source_sha256':hashlib.sha256(result_bytes.encode()).hexdigest(),
            'facts':result, 'activity_date_explanation':DATE_EXPLANATION,
            'design_options':DESIGN_OPTIONS,
            'style':'\n'.join(re.findall(r'<style[^>]*>(.*?)</style>',html,re.S)),
            'scripts':re.findall(r'<script\b[^>]*>.*?</script>',html,re.S),
            'components':{str(i):part for i,part in enumerate(parts)},
            'default_layout':'\n'.join('{{pd:'+str(i)+'}}' for i in range(len(parts)))}


class LocalHTML(HTMLParser):
    def __init__(self, allowed_links=()):
        super().__init__()
        self.allowed_links = set(allowed_links)

    def handle_starttag(self,tag,attrs):
        if tag in {'iframe','object','embed','form','base','link'}:
            raise ValueError('Dashboard must be self-contained and local: '+tag)
        for key,value in attrs:
            if key.lower().startswith('on'):
                raise ValueError('Inline event handlers are not allowed')
            local_key = key.rsplit(':', 1)[-1]
            if local_key in {'srcset', 'imagesrcset', 'ping'} and value:
                raise ValueError('Multiple resource references are not allowed')
            if local_key in {'src','href','action','poster','background','data','codebase'} and value and not value.startswith('#') and not (tag == 'a' and key == 'href' and value in self.allowed_links):
                raise ValueError('External references are not allowed')
        if tag == 'a' and any(key == 'href' and value in self.allowed_links for key,value in attrs):
            attributes = dict(attrs)
            if attributes.get('target') != '_blank' or not {'noopener', 'noreferrer'} <= set((attributes.get('rel') or '').split()):
                raise ValueError('Pipedrive links must open without access to this dashboard')
        if tag == 'meta' and any(key == 'http-equiv' and (value or '').lower() == 'refresh' for key,value in attrs):
            raise ValueError('Dashboard redirects are not allowed')


def allowed_deal_links(facts):
    from pipedrive_links import allowed_deal_links as saved_links
    return saved_links(facts)


def check_local(html, allow_scripts=False, allowed_links=()):
    LocalHTML(allowed_links).feed(html)
    if not allow_scripts and re.search(r'<script\b',html,re.I):
        raise ValueError('Combined layouts use only the supplied component scripts')
    if re.search(r'\b(?:fetch|XMLHttpRequest|WebSocket|sendBeacon|eval)\s*\(|@import|url\s*\(|\bimport\s',html,re.I):
        raise ValueError('Network requests and dynamically loaded code are not allowed')
    if re.search(r'\b(?:display\s*:\s*none|visibility\s*:\s*hidden|opacity\s*:\s*0\b)',html,re.I) and not allow_scripts:
        raise ValueError('The layout must not hide report components')


def assemble(bundle, layout):
    check_local(layout)
    tokens=re.findall(r'\{\{pd:([^}]+)\}\}',layout)
    expected=set(bundle['components'])
    if set(tokens)!=expected or len(tokens)!=len(expected):
        raise ValueError('Include every supplied component exactly once')
    content=layout
    for key,part in bundle['components'].items(): content=content.replace('{{pd:'+key+'}}',part)
    check_local(content, allowed_links=allowed_deal_links(bundle['facts']))
    title = escape(bundle.get('title') or 'Saved Pipedrive report')
    return '<!doctype html><html lang="en"><head>'+CSP_META+'<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>'+title+'</title><style>'+bundle['style']+'</style></head><body><main>'+content+'</main>'+''.join(bundle['scripts'])+'</body></html>'


def validate_custom(bundle, html, review):
    check_local(html,allow_scripts=True,allowed_links=allowed_deal_links(bundle['facts']))
    if not re.search(r'<main\b',html,re.I) or not re.search(r'</body>',html,re.I):
        raise ValueError('Custom dashboard needs a main region and a closing body')
    digest=hashlib.sha256(html.encode()).hexdigest()
    required=('facts','coverage','dates','action_states','charts','tooltips','branding','mobile')
    if (review.get('source_sha256')!=bundle['source_sha256'] or review.get('html_sha256')!=digest
            or any(review.get(key) is not True for key in required)):
        raise ValueError('Custom design requires a completed visible-content review for these exact source and HTML bytes')
    audit='<details class="pd-source-audit"><summary>Activity date meanings</summary><p>'+escape(DATE_EXPLANATION)+'</p></details>'
    return restrict_resources(html.replace('</body>',audit+'</body>',1))


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    actions=parser.add_subparsers(dest='action',required=True)
    prep=actions.add_parser('prepare'); prep.add_argument('--input',required=True); prep.add_argument('--role',default='rep'); prep.add_argument('--capabilities'); prep.add_argument('--output',required=True)
    compose=actions.add_parser('assemble'); compose.add_argument('--bundle',required=True); compose.add_argument('--layout',required=True,help="a layout fragment file, or 'default' for the bundle's default_layout (0.7.19 decision 7A)"); compose.add_argument('--output',required=True)
    custom=actions.add_parser('validate-custom'); custom.add_argument('--bundle',required=True); custom.add_argument('--html',required=True); custom.add_argument('--review',required=True); custom.add_argument('--output',required=True)
    args=parser.parse_args(argv)
    path=Path(args.output)
    if path.exists(): parser.error('Choose a new output path')
    if args.action=='prepare':
        capabilities=json.loads(Path(args.capabilities).read_text()) if args.capabilities else None
        bundle=prepare(json.loads(Path(args.input).read_text()),args.role,capabilities)
        path.write_text(json.dumps(bundle,indent=2,ensure_ascii=False)+'\n')
    else:
        bundle=json.loads(Path(args.bundle).read_text())
        layout = bundle['default_layout'] if args.action=='assemble' and args.layout=='default' else Path(args.layout).read_text() if args.action=='assemble' else None
        html=assemble(bundle,layout) if args.action=='assemble' else validate_custom(bundle,Path(args.html).read_text(),json.loads(Path(args.review).read_text()))
        path.write_text(html,encoding='utf-8')
    receipt={'path':str(path.resolve()),'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'design':args.action}
    if args.action=='assemble':
        receipt['layout']='default' if args.layout=='default' else 'custom'
    if args.action=='prepare':
        receipt['title']=bundle['title']
        receipt['default_layout']=bundle['default_layout']
        receipt['components']=[]
        for key,part in bundle['components'].items():
            heading=re.search(r'<h[1-6]\b[^>]*>(.*?)</h[1-6]>',part,re.S|re.I)
            label=unescape(re.sub(r'<[^>]+>','',heading.group(1))).strip() if heading else 'Report section'
            receipt['components'].append({'token':'{{pd:'+key+'}}','heading':label})
    print(json.dumps(receipt))


if __name__=='__main__':
    main()
