"""Report identity is independent of layout or probability mode."""


def report_skill(result):
    explicit = result.get('report_skill')
    if explicit in ('pipeline-brief', 'deal-triage', 'forecast', 'meeting-follow-up', 'deal-progression', 'sales-outreach'):
        return explicit
    if 'currency_sections' in result or 'period' in result:
        return 'forecast'
    if 'proposals' in result:
        return 'deal-triage'
    if 'aggregates' in result or 'ranked' in result or result.get('mode') in ('brief', 'recall', 'narrow'):
        return 'pipeline-brief'
    if result.get('mode') == 'triage':
        return 'deal-triage'
    raise ValueError('Saved result has no recognized report identity')
