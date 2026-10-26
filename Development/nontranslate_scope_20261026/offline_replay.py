"""Read-only corpus replay. No workflow DB, game process, API or credentials."""
from pathlib import Path
import copy,csv,hashlib,json,sys
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'Tools/localization'))
from glossary_corpus_evidence import attach_corpus_evidence
from workflow_nontranslate import validate_nontranslate_scope, mixed_latin_candidates
PROJECT=ROOT/'Projects/avadon-the-black-fortress'

def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()
def main():
    source=PROJECT/'avadon-the-black-fortress_text.csv'
    translated=PROJECT/'avadon-the-black-fortress_text_translated.csv'
    frozen=PROJECT/'TranslateFiles/glossary/r3/freeze_9ac86720/built/glossary_reviewed.json'
    with source.open(encoding='utf-8-sig',newline='') as f:originals=[r[0] for r in csv.reader(f)]
    with translated.open(encoding='utf-8-sig',newline='') as f:targets=[r[0] for r in csv.reader(f)]
    rows=[dict(id=str(i),ordinal=i,source=a,translation=b) for i,(a,b) in enumerate(zip(originals,targets),1)]
    old=json.loads(frozen.read_text(encoding='utf-8-sig'))
    literal=copy.deepcopy(next(r for r in old['non_translate'] if r['source']=='retrain'))
    snapshot=dict(characters=[],terms=[],non_translate=[literal])
    before=dict(source=sha(source),translation=sha(translated),frozen=sha(frozen))
    stats=attach_corpus_evidence(snapshot,rows)
    try:validate_nontranslate_scope(snapshot['non_translate'][0])
    except ValueError as error:blocked=str(error)
    else:raise AssertionError('Old global retrain must require deliberate scope')
    entry=snapshot['non_translate'][0]
    visible=set(entry['evidence_rows'][:2])|set(entry.get('corpus_evidence_rows',[]))
    assert 15333 in visible and any(rows[n-1]['source'].strip()!='retrain' for n in visible)
    scoped={**entry,'applicability':dict(mode='whole_record',case_sensitive=True,reason='Confirmed isolated native literal only')}
    validate_nontranslate_scope(scoped)
    current=json.loads((PROJECT/'TranslateFiles/reports/final_validation.json').read_text(encoding='utf-8-sig'))
    glossary=json.loads((PROJECT/'TranslateFiles'/current['glossary']['path'].replace('\\','/')).read_text(encoding='utf-8-sig'))
    mixed=mixed_latin_candidates(rows,glossary)
    installed=json.loads((PROJECT/'Repack/INSTALLATION.json').read_text(encoding='utf-8-sig'))
    assert all(sha(PROJECT/'Game'/name)==expected for name,expected in installed['payload_files'].items())
    assert before==dict(source=sha(source),translation=sha(translated),frozen=sha(frozen))
    report=dict(status='passed',api_requests=0,api_mock_used=False,production_state_modified=False,game_started=False,
        source_bindings=before,old_unscoped_literal_blocked=blocked,
        delivered_complete_records=[dict(ordinal=n,source=rows[n-1]['source']) for n in sorted(visible)],
        evidence_stats=stats,scoped_literal_accepted=True,installed_payload_hashes_unchanged=True,
        current_mixed_candidate_records=len(mixed),candidate_status='not a semantic approval',
        validation_limit='Deterministic corpus/state checks only; no new live API response behavior was tested.')
    path=Path(__file__).with_name('report.json');path.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False))

if __name__=='__main__':main()
