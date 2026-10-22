"""Portable chart components with visible scales and local accessible details."""

from collections import Counter, defaultdict
from html import escape
import math
import json
from activity_evidence import DATE_EXPLANATION
from display_time import display_timestamp, display_timezone
from pipedrive_links import html_deal, deal_url
from pdcore import money, plural

RULE_LABELS = {'R1':'No recent CRM touch', 'R2':'No next activity scheduled',
               'R3':'Longer in stage than the rotting threshold', 'R4':'Expected close date passed',
               'R5':'Only one contact involved', 'R6':'Qualification gap', 'R7':'Note and stage mismatch',
               'R8':'Overdue activity', 'R9':'No deal value', 'R10':'Distant close date', 'R11':'Missing contact or company link'}
MAIL_DATE_CELL = 'Not used for a mail touch'
COLORS = ['#017737', '#6861F2', '#014722', '#3B3A97', '#CFD0F9']
CSS = '''
.pd-charts{margin:24px 0}.pd-chart-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,380px),1fr));gap:18px}.pd-chart{padding:20px;background:#fff;border:1px solid #d9e5dd;border-radius:12px;min-width:0}.pd-chart h3{margin:0 0 8px;color:#014722;font-size:16px}.pd-chart p{font-size:13px;color:#5d7066}.pd-chart svg{width:100%;height:auto;display:block;overflow:visible}.pd-chart svg text{pointer-events:none;font:12px system-ui;fill:#182b22}.pd-grid{stroke:#d9e5dd;stroke-width:1}.pd-axis{stroke:#5d7066;stroke-width:1;fill:none}.pd-threshold{stroke:#5d7066;stroke-dasharray:5 5}.pd-legend{display:flex;flex-wrap:wrap;gap:14px;font-size:13px;margin:12px 0}.pd-legend span{display:inline-flex;align-items:center;gap:6px}.pd-legend i{display:inline-block;width:12px;height:12px;border-radius:3px}.pd-chart [data-tooltip]{cursor:pointer}[data-tooltip]:focus-visible{outline:3px solid #6861F2;outline-offset:3px}.pd-tooltip{position:fixed;z-index:1000;max-width:min(340px,90vw);padding:10px 12px;background:#014722;color:#fff;border-radius:6px;font:13px/1.5 system-ui;white-space:pre-line;max-height:60vh;overflow:auto;pointer-events:auto;box-shadow:0 3px 12px #0003}.pd-tooltip[hidden]{display:none}.pd-chart table{border-collapse:collapse;width:100%;font-size:12px}.pd-chart th,.pd-chart td{text-align:left;padding:6px;border-bottom:1px solid #d9e5dd;overflow-wrap:anywhere}.pd-table-scroll{overflow:auto}.pd-bar-row{display:grid;grid-template-columns:minmax(90px,1fr) 2fr auto;gap:10px;align-items:center;margin:12px 0;font-size:13px}.pd-bar-track{height:18px;background:#f0f5f2;border-radius:4px}.pd-bar-track i{height:100%;display:block;background:#017737;border-radius:4px}.pd-count-scale{display:flex;justify-content:space-between;margin-left:35%;font-size:11px;color:#5d7066}.pd-date-note{font-size:13px;color:#5d7066}.notice,.warning{background:#f3f0ff!important;border-left-color:#3B3A97!important;color:#3B3A97!important}.eyebrow{color:#014722}.masthead .eyebrow{color:#D7F9D3}.pd-chart details{margin-top:10px}@media print{.pd-tooltip{display:none}.pd-chart{break-inside:avoid}.pd-chart details>*{display:block}}@media(max-width:480px){.pd-chart{padding:12px}.pd-bar-row{grid-template-columns:1fr}.pd-count-scale{margin-left:0}}
'''
SCRIPT = r'''<script>(()=>{const tip=document.createElement('div');tip.className='pd-tooltip';tip.id='pd-chart-tooltip';tip.setAttribute('role','region');tip.setAttribute('aria-label','Chart details');tip.tabIndex=0;tip.hidden=true;document.body.appendChild(tip);let active;let pinned=false;
function show(el,x,y){if(active&&active!==el)active.removeAttribute('aria-describedby');active=el;tip.replaceChildren();if(el.dataset.dealLinks){for(const record of JSON.parse(el.dataset.dealLinks)){const line=document.createElement('div');if(record.url&&/^https:\/\/[a-z0-9-]+\.pipedrive\.com\/deal\/[1-9][0-9]*$/.test(record.url)){const a=document.createElement('a');a.href=record.url;a.textContent=record.name;a.target='_blank';a.rel='noopener noreferrer';a.style.color='white';line.append(a,document.createTextNode(record.detail));}else{line.textContent=record.name+' · Link unavailable'+record.detail;}tip.append(line);}}else{tip.textContent=el.dataset.tooltip;}tip.hidden=false;el.setAttribute('aria-describedby',tip.id);const r=el.getBoundingClientRect();tip.style.left=Math.max(8,Math.min(x??r.left,innerWidth-tip.offsetWidth-8))+'px';tip.style.top=Math.max(8,Math.min((y??r.bottom)+12,innerHeight-tip.offsetHeight-8))+'px';}
function hide(){if(active)active.removeAttribute('aria-describedby');active=null;pinned=false;tip.hidden=true;}
document.querySelectorAll('[data-tooltip]').forEach(el=>{el.addEventListener('pointerenter',e=>{pinned=false;show(el,e.clientX,e.clientY);});el.addEventListener('pointermove',e=>show(el,e.clientX,e.clientY));el.addEventListener('pointerleave',()=>{if(!pinned)hide();});el.addEventListener('focus',()=>show(el));el.addEventListener('blur',()=>{if(!pinned)hide();});el.addEventListener('click',e=>{e.stopPropagation();pinned=true;show(el,e.clientX||undefined,e.clientY||undefined);});el.addEventListener('keydown',e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();pinned=true;show(el);tip.focus();}});});tip.addEventListener('click',e=>e.stopPropagation());document.addEventListener('click',hide);document.addEventListener('keydown',e=>{if(e.key==='Escape')hide();});})();</script>'''


PROBABILITY_MODE_WORDS = {'deal': 'deal-level chances recorded in Pipedrive', 'stage': 'stage chances from the pipeline',
                          'ladder': 'accepted local stage ladder', 'mixed': 'a mix of deal, stage and local chances',
                          'unweighted': 'unweighted, no usable chances'}


def interactive(label):
    label = escape(str(label), quote=True)
    return f'tabindex="0" data-tooltip="{label}" aria-label="{label}"'


def legend(groups):
    return '<div class="pd-legend" aria-label="Chart legend">' + ''.join(
        f'<span><i style="background:{color}"></i>{escape(label)}</span>' for label,color in groups) + '</div>'


def table(headers, rows, linked_deals=None, summary='Chart data'):
    linked_deals = linked_deals or {}
    rendered_rows = []
    for index, row in enumerate(rows):
        cells = []
        for column, value in enumerate(row):
            content = linked_deals[index] if column == 0 and index in linked_deals else escape(
                str(value if value is not None else "Not recorded"))
            cells.append(f'<td>{content}</td>')
        rendered_rows.append('<tr>' + ''.join(cells) + '</tr>')
    return ('<details><summary>' + escape(summary) + '</summary><div class="pd-table-scroll">'
            '<table><thead><tr>' + ''.join(f'<th scope="col">{escape(h)}</th>' for h in headers)
            + '</tr></thead><tbody>' + ''.join(rendered_rows) + '</tbody></table></div></details>')


def nice_max(value):
    if value <= 0: return 1
    magnitude = 10 ** math.floor(math.log10(value))
    return math.ceil(value/magnitude/0.5)*0.5*magnitude


def scatter(deals, currency, groups, days_key='days_silent', group_key=None, labels=None, timezone_name=None, result=None):
    points = [d for d in deals if type(d.get(days_key)) in (int,float) and type(d.get('value')) in (int,float) and d['value']>=0]
    unknown = len(deals)-len(points)
    if not points:
        return '<article class="pd-chart"><h3>Value and activity recency</h3><p>No dated activity proxy is available for plotting.</p></article>'
    xmax = math.ceil(max(60,max(d[days_key] for d in points))/60)*60
    ymax = nice_max(max(d['value'] for d in points))
    grid=[]
    for i in range(5):
        x=76+i*540/4; y=270-i*220/4
        grid.append(f'<line x1="76" y1="{y}" x2="616" y2="{y}" class="pd-grid"/><text x="68" y="{y+4}" text-anchor="end">{escape(money(ymax*i/4,currency))}</text>')
        grid.append(f'<text x="{x}" y="292" text-anchor="middle">{xmax*i/4:g}</text>')
    for days in (30,60):
        x=76+days*540/xmax
        grid.append(f'<line x1="{x}" y1="50" x2="{x}" y2="270" class="pd-threshold"/><text x="{x+4}" y="43">{days} days</text>')
    dots=[]; rows=[]; clusters=defaultdict(list)
    for d in points:
        key=group_key(d) if group_key else 0
        name,color=groups[key]
        x=76+max(0,d[days_key])*540/xmax; y=270-d['value']*220/ymax
        scheduled=d.get('last_activity_scheduled_date')
        recorded=display_timestamp(d.get('last_activity_marked_as_done_time'), timezone_name)
        source=d.get('last_touch_source') or d.get('last_activity_date_source') or 'source unavailable'
        mail_touch='mail' in str(source).lower()
        if mail_touch:
            # A mail touch is not a completed activity; the completed call's dates would read as its dates.
            scheduled,recorded=MAIL_DATE_CELL,MAIL_DATE_CELL
            dates=f' · activity dates not used for {source}'
        else:
            dates=f" · scheduled {scheduled or 'not recorded'} · marked done {recorded or 'not recorded'}"
        label=f"{d.get('title') or 'Deal name unavailable'} · {name} · {money(d['value'],currency)} · {plural(d[days_key], 'day')} · {source}{dates}"
        position=next((position for position in clusters if (position[0]-x)**2+(position[1]-y)**2 < 20**2), (round(x,1),round(y,1)))
        clusters[position].append((label,color,source,key,d))
        point_label=(labels or {}).get(d.get('id'))
        if point_label: dots.append(f'<text x="{min(x+9,598):.1f}" y="{max(y-7,54):.1f}" font-weight="700">{escape(str(point_label))}</text>')
        rows.append([d.get('title') or 'Deal name unavailable',name,money(d['value'],currency),d[days_key],source,scheduled,recorded])
    for (x,y), members in clusters.items():
        label='\n'.join(member[0] for member in members)
        if len(members)>1: label=f'{len(members)} deals at this position or nearby\n'+label
        color=max(members,key=lambda member:member[3])[1]
        fallback=any('fallback' in member[2] for member in members)
        stroke='stroke="#014722" stroke-width="2" stroke-dasharray="2 2"' if fallback else 'stroke="white" stroke-width="1"'
        attributes, title = '', label
        if (result or {}).get('report_schema_version') == 2:
            links = [{'name': member[4].get('title') or 'Deal name unavailable',
                      'url': deal_url(result.get('output_domain'), member[4].get('id')),
                      'detail': member[0][len(member[4].get('title') or 'Deal name unavailable'):]} for member in members]
            attributes = ' data-deal-links="' + escape(json.dumps(links, ensure_ascii=False), quote=True) + '"'
            title = plural(len(members), 'recorded deal') + '; focus or tap for linked details'
        dots.insert(0,f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{8 if len(members)>1 else 6}" fill="{color}" {stroke} {interactive(label)}{attributes}><title>{escape(title)}</title></circle>')
    source_note='<p>Dashed outlines include a fallback date. Larger dots group nearby deals at the first deal’s position; details retain every exact value and age. Hover or focus for details; tap or press Enter to pin and scroll the full list.</p>'
    return '<article class="pd-chart"><h3>Value and activity recency · '+escape(currency)+'</h3><p>Each dot is a deal or a nearby group. '+plural(unknown, 'deal')+' cannot be plotted. Recency is a CRM activity proxy, not confirmed buyer contact. Timestamps shown in '+escape(display_timezone(timezone_name))+'.</p>'+legend(groups)+source_note+ '<svg viewBox="0 0 650 330" role="group" aria-label="Deal value by days since CRM activity proxy">'+''.join(grid)+'<path d="M76 50V270H616" class="pd-axis"/><text x="346" y="320" text-anchor="middle">Days since CRM activity proxy</text>'+''.join(dots)+'</svg>'+table(['Deal','Group','Value','Days','Date source','Scheduled date','Marked done timestamp'],rows,{index:html_deal(result or {}, deal, rows[index][0]) for index,deal in enumerate(points)},summary='Chart data and activity dates')+'</article>'


def rule_bars(deals):
    counts=Counter(f.get('id') for d in deals for f in d.get('flags',[]) if f.get('id'))
    scale=max(counts.values(),default=1)
    rows=[]
    for rule,count in sorted(counts.items(),key=lambda x:(-x[1],x[0])):
        label=RULE_LABELS.get(rule, rule)
        rows.append(f'<div class="pd-bar-row"><span>{escape(label)}</span><div class="pd-bar-track" {interactive(f"{label}: {plural(count, 'checked deal')}")}><i style="width:{count*100/scale}%"></i></div><b>{count}</b></div>')
    return '<article class="pd-chart"><h3>Deals triggering each rule</h3><p>Checked deals only; one deal can trigger several rules.</p>'+''.join(rows)+f'<div class="pd-count-scale"><span>0</span><span>{plural(scale, "deal")}</span></div>'+table(['Rule','Deals'],[(RULE_LABELS.get(k,k),v) for k,v in counts.items()])+'</article>'


def risk_charts(result, labels=None):
    deals=result.get('deals') or []
    if not deals: return ''
    queued=set((labels or {}))
    groups=[('Other checked deals','#017737'),('Contains an action-queue deal','#6861F2')]
    charts=rule_bars(deals)
    currencies=sorted({d.get('currency') or 'UNSPECIFIED' for d in deals})
    for currency in currencies:
        charts+=scatter([d for d in deals if (d.get('currency') or 'UNSPECIFIED')==currency],currency,groups,group_key=lambda d:int(d.get('id') in queued),labels=labels,timezone_name=result.get('display_timezone') or (result.get('locale') or {}).get('timezone'),result=result)
    return '<section class="pd-charts"><h2>Risk patterns in checked deals</h2><p class="pd-date-note">'+escape(DATE_EXPLANATION)+'</p><div class="pd-chart-grid">'+charts+'</div></section>'


def grouped_bars(title, currency, rows, groups, explanation, data_table=None):
    if not rows: return ''
    ymax=nice_max(max(sum(values) if title.startswith('Activity') else max(values,default=0) for _,values in rows))
    width=max(650,len(rows)*140+100); plot=width-120; baseline=280
    parts=[]
    for i in range(5):
        y=baseline-i*220/4
        parts.append(f'<line x1="85" y1="{y}" x2="{width-25}" y2="{y}" class="pd-grid"/><text x="77" y="{y+4}" text-anchor="end">{escape(money(ymax*i/4,currency))}</text>')
    for pos,(name,values) in enumerate(rows):
        slot=plot/len(rows); bar=min(38,slot/(len(groups)+1)); start=85+slot*pos+(slot-bar*len(groups))/2
        for index,value in enumerate(values):
            h=value*220/ymax; x=start+index*bar; y=baseline-h
            label=f'{name} · {groups[index][0]} · {money(value,currency)}'
            parts.append(f'<rect x="{x:.1f}" y="{y:.1f}" width="{bar-4:.1f}" height="{h:.1f}" rx="3" fill="{groups[index][1]}" {interactive(label)}/><text x="{x+(bar-4)/2:.1f}" y="{y-7:.1f}" text-anchor="middle">{escape(money(value,currency))}</text>')
        parts.append(f'<text x="{85+slot*(pos+.5):.1f}" y="306" text-anchor="middle">{escape(name)}</text>')
    return '<article class="pd-chart"><h3>'+escape(title)+'</h3><p>'+escape(explanation)+'</p>'+legend(groups)+f'<svg viewBox="0 0 {width} 330" role="group" aria-label="{escape(title)}">'+''.join(parts)+'</svg>'+(data_table if data_table is not None else table(['Group']+[g[0] for g in groups],[(name,*[money(v,currency) for v in values]) for name,values in rows]))+'</article>'


def forecast_charts(currency,values,unweighted=False,result=None):
    deals=values.get('deals') or []
    known=[d for d in deals if type(d.get('days_since_activity')) in (int,float)]
    buckets=[0.,0.,0.]
    for d in known:
        days=d['days_since_activity']; buckets[0 if days<=30 else 1 if days<=60 else 2]+=d.get('value') or 0
    total=sum(buckets) or 1
    groups=[('≤30 days','#017737'),('31–60 days','#6861F2'),('Over 60 days','#3B3A97')]
    parts=[]; left=85
    for (name,color),value in zip(groups,buckets):
        width=value*530/total
        parts.append(f'<rect x="{left:.1f}" y="55" width="{width:.1f}" height="48" fill="{color}" {interactive(f"{name}: {money(value,currency)}")}/>')
        if width>80: parts.append(f'<text x="{left+width/2:.1f}" y="83" text-anchor="middle" style="fill:white">{escape(money(value,currency))}</text>')
        left+=width
    parts += [f'<text x="{85+530*i/4}" y="130" text-anchor="middle">{escape(money(sum(buckets)*i/4,currency))}</text>' for i in range(5)]
    recency='<article class="pd-chart"><h3>Activity recency by open deal value</h3><p>Included open deals only; '+str(len(deals)-len(known))+' deals have unknown recency and are excluded.</p>'+legend(groups)+'<svg viewBox="0 0 650 160" role="group" aria-label="Open deal value by activity recency">'+''.join(parts)+'</svg>'+table(['Recency','Open value'],[(g[0],money(v,currency)) for g,v in zip(groups,buckets)])+'</article>'
    months=defaultdict(lambda:[0.,0.])
    for d in deals:
        key=str(d.get('expected_close_date') or 'Date not set')[:7]; value=float(d.get('value') or 0)
        months[key][0]+=value
        months[key][1]+=value*float(d.get('adjusted_probability') or 0)
    known_probabilities=all(type(d.get('adjusted_probability')) in (int,float) for d in deals)
    contribution_label='Open value after risk adjustments' if unweighted else 'Likely revenue'
    monthly_groups=[('Open deal value','#017737'),(contribution_label,'#6861F2')] if known_probabilities else [('Open deal value','#017737')]
    monthly_rows=sorted(months.items()) if known_probabilities else [(k,[v[0]]) for k,v in sorted(months.items())]
    monthly_note=('Open value and value after risk adjustments; not a likelihood estimate. Won revenue is excluded. These are not Manager commit.' if unweighted else 'Open value and calculated likely revenue. Won revenue is excluded. These are not Manager commit.')
    monthly=grouped_bars('Expected close month',currency,monthly_rows,monthly_groups,monthly_note if known_probabilities else 'Open value only; monthly contributions are unavailable because saved deal probabilities are missing.')
    points=scatter(deals,currency,[('Included open deals','#017737')],days_key='days_since_activity',timezone_name=(result or {}).get('display_timezone') or (result or {}).get('timezone'),result=result)
    return '<section class="pd-charts"><p class="pd-date-note">'+escape(DATE_EXPLANATION)+'</p><div class="pd-chart-grid">'+recency+points+monthly+'</div></section>'


def stage_position(key, pipelines):
    """Pipeline order, then the stage's place in that pipeline's saved stage order; unknown stages go last."""
    pipeline_id, stage = key
    names = (pipelines.get(pipeline_id) or {}).get('stages') or []
    pipeline_rank = list(pipelines).index(pipeline_id) if pipeline_id in pipelines else len(pipelines)
    return (pipeline_rank, names.index(stage) if stage in names else len(names), stage)


def ordered_stage_keys(keys, pipelines):
    """(pipeline_id, stage) keys in the one order the stage chart and the stage list both show."""
    return sorted(keys, key=lambda key: stage_position(key, pipelines))


def stage_charts(result):
    deals=result.get('deals') or []
    pipelines=result.get('pipelines') or {}
    labelled=len({str(d.get('pipeline_id')) for d in deals})>1
    charts=''
    for currency in sorted({d.get('currency') or 'UNSPECIFIED' for d in deals}):
        stages=defaultdict(lambda:[0.,0])
        for d in deals:
            if (d.get('currency') or 'UNSPECIFIED')==currency:
                key=(str(d.get('pipeline_id')), d.get('stage') or 'Unknown stage')
                stages[key][0]+=d.get('value') or 0
                stages[key][1]+=1
        ordered=[(key, stages[key]) for key in ordered_stage_keys(stages, pipelines)]
        labels={key: ((pipelines.get(key[0]) or {}).get('label') or 'Pipeline '+key[0])+' · '+key[1] if labelled else key[1] for key in stages}
        charts+='<section class="pd-charts">'+grouped_bars('Open value by stage',currency,[(labels[k],[v[0]]) for k,v in ordered],[('Open deal value','#017737')],'Stage counts and values are listed in Chart data.',data_table=table(['Stage','Deal count','Open value'],[(labels[k],v[1],money(v[0],currency)) for k,v in ordered]))+'</section>'
    return charts


def brief_charts(result):
    return risk_charts(result)
