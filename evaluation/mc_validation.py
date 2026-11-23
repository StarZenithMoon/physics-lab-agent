"""Numerical tolerance calibration on public probability models, not Agent scores."""
import argparse
import math
import numpy as np
from scipy.stats import beta
from labcore.io import read_json, write_json
from labcore.project import ROOT
from .evaluate import assertion


def validate(replicates=1000, seed=2026110401):
    if replicates<1:raise ValueError('replicates must be positive')
    records=[]
    for task in read_json(ROOT/'evaluation/public-cases.json')['tasks']:
        ident=task['id']
        if not (ident.startswith('jcgm101-additive-') or ident=='synthetic-zero-gradient-square'):continue
        spec=read_json(ROOT/task['public']['data_directory']/'inputs.json')
        samples=200000;failures=[];deviations={}
        for i in range(replicates):
            rng=np.random.default_rng(np.random.SeedSequence([seed,i]))
            if ident=='synthetic-zero-gradient-square':
                values=rng.normal(size=samples)**2;status='limited';issues=['first_order_invalid']
            else:
                values=np.zeros(samples);status='complete';issues=[]
                for v in spec['inputs']:
                    values+=rng.normal(0,v['u'],samples) if v['distribution']=='normal' else rng.uniform(-math.sqrt(3)*v['u'],math.sqrt(3)*v['u'],samples)
            low,high=np.quantile(values,[.025,.975])
            report={'status':status,'issues':issues,'result':{'value':float(values.mean()),'u':float(values.std(ddof=1)),
                     'lower':float(low),'upper':float(high),'mc_samples':samples}}
            failed=[c['path'] for c in task['expected']['checks'] if not assertion(report,c)]
            if failed:failures.append({'replicate':i,'checks':failed})
            for c in task['expected']['checks']:
                if 'target' in c:
                    k=c['path'].split('.')[-1];deviations.setdefault(k,[]).append(abs(report['result'][k]-c['target']))
        upper=1. if len(failures)==replicates else float(beta.ppf(.95,len(failures)+1,replicates-len(failures)))
        record={'task_id':ident,'replicates':replicates,'samples':samples,'false_rejections':len(failures),
                'one_sided_95_upper':upper,'target_upper':.02,'passed':upper<=.02,
                'max_absolute_deviation':{k:max(v) for k,v in deviations.items()},'failures':failures}
        records.append(record)
        print(f"{ident}: {len(failures)}/{replicates} false rejections; upper={upper:.6f}",flush=True)
    return {'mode':'numerical_tolerance_validation_not_agent_evaluation','seed_sequence':[seed,'replicate-index'],
            'all_passed':all(r['passed'] for r in records),'records':records,'model_calls':0,
            'limitation':'Public mathematical models, fixed minimum samples, independent sampler; not a validation of arbitrary Agent code or physical model adequacy.'}


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--out',required=True);p.add_argument('--replicates',type=int,default=1000)
    a=p.parse_args();v=validate(a.replicates);write_json(a.out,v)
    if not v['all_passed']:raise SystemExit(1)
