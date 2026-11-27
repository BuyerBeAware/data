"""Conservative physical-work timing from dedicated capital-project phase fields.
No permit, funding, hearing or map-edit dates become construction years.
"""
from datetime import datetime,timezone
import math


def date(value):
    if value is None or value=='':return None
    if not isinstance(value,(int,float)) or isinstance(value,bool) or not math.isfinite(value):raise ValueError('Invalid GIS date')
    result=datetime.fromtimestamp(value/1000,timezone.utc).date()
    if not 1990<=result.year<=2100:raise ValueError('GIS date outside supported range')
    return result


def enrich(record,fields,now=None):
    today=(now or datetime.now(timezone.utc)).date();out=dict(record)
    for key in ('constructionStatus','projectedStartDate','projectedCompletionDate','dateEvidence','recordStage','timingEvidenceConflicts'):out.pop(key,None)
    phase=str(fields.get('projphase') or '').strip();status=str(fields.get('phasestatus') or '').strip()
    out['agencyProjectPhase']=phase or 'Not provided';out['agencyPhaseStatus']=status or 'Not provided'
    errors=[]
    try:pstart,pend,astart,aend=[date(fields.get(k)) for k in ('planstart','planend','actstart','actend')]
    except (ValueError,OverflowError,OSError):
        errors=['Invalid dedicated project date'];pstart=pend=astart=aend=None
    if pstart and pend and pend<pstart:errors.append('Planned end precedes planned start')
    if astart and astart>today:errors.append('Actual start is in the future')
    if aend and aend>today:errors.append('Actual end is in the future')
    if astart and aend and aend<astart:errors.append('Actual end precedes actual start')
    if phase=='Complete' and status in ('Under Construction','In Progress','In Design'):errors.append('Complete phase conflicts with ongoing phase status')
    active=phase=='Construction' and status in ('Under Construction','In Progress')
    if active and aend:errors.append('Ongoing construction status conflicts with actual end date')
    if active and pend and pend<today:errors.append('Ongoing construction has an expired planned work window; current activity needs corroboration')
    if errors:
        out['timingEvidenceConflicts']=errors
        out['details']+='\nTiming review required: '+'; '.join(errors)+'. No work dates inferred.'
        return out
    if phase=='Complete' and status=='Complete' and astart and aend:
        out.update(status='Agency reports project complete',constructionStatus='Completed',recordStage='agency_reported_complete',
            projectedStartDate=astart.isoformat(),projectedCompletionDate=aend.isoformat(),
            schedule=f'Agency actual project work window: {astart.isoformat()} to {aend.isoformat()}. Phase: Complete; phase status: Complete.')
    elif active and astart and not aend:
        out.update(status='Agency reports construction in progress',constructionStatus='Under Construction',recordStage='agency_reported_construction',projectedStartDate=astart.isoformat(),
            schedule=f'Agency construction phase, {status}; actual start: {astart.isoformat()}. '+(f'Planned end: {pend.isoformat()} (tentative).' if pend else 'Completion date not provided.'))
        if pend:out['projectedCompletionDate']=pend.isoformat()
    else:return out
    out['dateEvidence']={'sourceKind':'official_capital_project_phase_fields','phase':phase,'phaseStatus':status,
        'plannedStart':pstart.isoformat() if pstart else None,'plannedEnd':pend.isoformat() if pend else None,
        'actualStart':astart.isoformat() if astart else None,'actualEnd':aend.isoformat() if aend else None,
        'sourceURL':record['sourceURL'],'sourceGeometryRef':record.get('locationSourceRef'),
        'checkedAt':record['locationReviewedAt'],'independentFieldInspection':False}
    out['details']+='\nTiming is from the agency’s dedicated project phase and work-date fields, not approval or fiscal-year dates. Planned dates are tentative; no independent field inspection is claimed.'
    return out
