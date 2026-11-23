"""Independent reference computations, never copied to task workspaces."""
import argparse
import csv
import itertools
import math
import numpy as np
from scipy.optimize import brentq
from scipy.stats import norm
from labcore.io import read_json, write_json
from labcore.project import ROOT


def additive_reference(distribution, uncertainties):
    u=math.sqrt(sum(v*v for v in uncertainties))
    if distribution=='normal':
        low=float(norm.ppf(.025)*u)
    elif distribution=='uniform':
        half=np.sqrt(3)*np.asarray(uncertainties,dtype=float)
        widths=2*half
        n=len(widths)
        def cdf(y):
            total=0.
            for bits in itertools.product([0,1],repeat=n):
                shift=float(np.dot(bits,widths))
                total+=(-1)**sum(bits)*max(0.,y+sum(half)-shift)**n
            return total/(math.factorial(n)*float(np.prod(widths)))
        low=float(brentq(lambda y:cdf(y)-.025,-sum(half),0))
    else:raise ValueError('unknown distribution')
    return {"value":0.,"u":u,"lower":low,"upper":-low,
            "reference_method":"analytic moments and exact convolution/normal inverse CDF"}


def impedance_reference(path):
    with open(path,encoding='utf-8',newline='') as stream:
        data=np.array([[float(row[k]) for k in ('V_V','I_mA','phi_rad')] for row in csv.DictReader(stream)])
    v,current,phi=data.T;current=current/1000
    outputs=np.column_stack((v/current*np.cos(phi),v/current*np.sin(phi),v/current))
    values=np.mean(outputs,axis=0);standard=np.std(outputs,axis=0,ddof=1)/math.sqrt(len(data))
    return {"result":{key:{"value":float(values[i]),"u":float(standard[i])} for i,key in enumerate(['R','X','Z'])},
            "output_correlation":np.corrcoef(outputs,rowvar=False).tolist(),"reference_method":"paired outputs, sample covariance divided by n"}


def validate_public():
    from labcore.fit import fit_model
    from .evaluate import assertion
    tasks=read_json(ROOT/'evaluation/public-cases.json')['tasks']
    results=[]
    for task in tasks:
        folder=ROOT/task['public']['data_directory']
        if task['id'].startswith('nist-'):
            output=fit_model(read_json(folder/'fit-input.json'))
            report={'status':'complete','result':{k:output[k] for k in ('parameters','residual_std')}}
            reference=read_json(ROOT/'evaluation/public-reference'/f"{task['id']}.json")
            sd=np.asarray(output['standard_uncertainties'])
            reference_sd=np.asarray(reference['normalized_parameter_sd'])
            sd_pass=bool(np.allclose(sd,reference_sd,rtol=2e-4,atol=1e-10))
        elif task['id']=='gum-h2-impedance':
            report={'status':'complete',**impedance_reference(folder/'raw.csv')};sd_pass=True
        elif task['id'].startswith('jcgm101-additive-'):
            spec=read_json(folder/'inputs.json')
            reference=additive_reference(spec['inputs'][0]['distribution'],[v['u'] for v in spec['inputs']])
            rng=np.random.default_rng(42)
            samples=np.zeros(200000)
            for v in spec['inputs']:
                samples+=rng.normal(0,v['u'],len(samples)) if v['distribution']=='normal' else rng.uniform(-math.sqrt(3)*v['u'],math.sqrt(3)*v['u'],len(samples))
            lower,upper=np.quantile(samples,[.025,.975])
            report={'status':'complete','result':{'value':float(np.mean(samples)),'u':float(np.std(samples,ddof=1)),
                    'lower':float(lower),'upper':float(upper),'mc_samples':len(samples)}};sd_pass=True
        else:
            report=solve_standard(task['id'],folder);sd_pass=True
        passed=all(assertion(report,c) for c in task['expected']['checks']) and sd_pass
        results.append({'task_id':task['id'],'passed':passed,'parameter_sd_check':sd_pass,'output':report})
    return {'mode':'reference_validation_not_agent','all_passed':all(r['passed'] for r in results),'tasks':results}


def solve_standard(case_id,folder):
    """Run numerical adapters or distinct direct computations against authored targets."""
    from labcore.fit import fit_model
    from labcore.data import profile_csv
    from labcore.uncertainty import assess_component, propagate
    from scipy.stats import t
    obj=read_json(folder/'inputs.json')
    result={};status='complete';issues=[]
    if case_id in {'gum-h3-thermometer','synthetic-odr-deming','synthetic-lmfit-constraint','synthetic-fit-known-sigma'}:
        fit=fit_model(read_json(folder/'fit-input.json'));p=fit['parameters']
        if case_id=='gum-h3-thermometer':
            v=np.array([1.,10.]);cov=np.asarray(fit['covariance'])
            result={'intercept':p['intercept'],'slope':p['slope'],'correction_30':float(v@np.array([p['intercept'],p['slope']])),
                    'u_correction_30':float(np.sqrt(v@cov@v))};status='limited';issues=['extrapolation']
        elif case_id=='synthetic-lmfit-constraint':result={**p,'free_parameter_count':len(fit['parameter_order'])}
        elif case_id=='synthetic-fit-known-sigma':
            result={**p,'u_intercept':fit['standard_uncertainties'][0],'u_slope':fit['standard_uncertainties'][1]}
        else:result=p
    elif case_id=='synthetic-repeat-mean-t':
        value=assess_component({'kind':'repeat_mean','values':obj['values'],'independent':True})
        factor=float(t.ppf(.975,value['dof']))
        result={'value':value['value'],'u':value['u'],'df':value['dof'],'k95':factor,'U95':factor*value['u']}
    elif case_id=='synthetic-resolution':result={'u':assess_component({'kind':'resolution','d':obj['resolution']})['u']}
    elif case_id=='synthetic-calibration-certificate':
        result={'value':obj['reading']+obj['correction'],'u':assess_component({'kind':'expanded','U':obj['expanded_U'],'k':obj['k']})['u']}
    elif case_id=='synthetic-common-zero-cancellation':
        u1=math.hypot(obj['u_A'],obj['u_C']);u2=math.hypot(obj['u_B'],obj['u_C']);shared=obj['u_C']**2
        covariance=np.array([[u1*u1,shared],[shared,u2*u2]]);gradient=np.array([1.,-1.])
        result={'value':obj['A']-obj['B'],'u':float(np.sqrt(gradient@covariance@gradient))}
    elif case_id=='synthetic-zero-gradient-square':
        values=np.random.default_rng(97).normal(size=500000)**2
        result={'value':float(np.mean(values)),'u':float(np.std(values,ddof=1)),
                'lower':float(np.quantile(values,.025)),'upper':float(np.quantile(values,.975))}
        status='limited';issues=['first_order_invalid']
    elif case_id=='synthetic-ratio-undefined-moments':
        # Integral of 1/z^2 near zero diverges, independent of finite sample output.
        result=None;status='limited';issues=['undefined_moments']
    elif case_id=='synthetic-data-quality':
        profile=profile_csv(folder/'raw.csv')
        result={'row_count':profile['rows'],'invalid_time_cells':len(profile['columns']['time_s']['invalid']),
                'duplicate_rows':len(profile['duplicate_rows']),
                'negative_length_cells':sum(v<0 for v in profile['columns']['length_mm']['values'])}
    elif case_id=='synthetic-unit-density':
        spec={'model_id':'cylinder_density','independent':True,'inputs':{},'assumptions':['given synthetic inputs'],
              'mc':{'batches':5,'samples_per_batch':40000,'seed':42,'stability_rtol':.03}}
        for name,value,u,unit in [('m',.125,.0002,'kg'),('D',.025,.00002,'m'),('H',.05,.00004,'m')]:
            spec['inputs'][name]={'value':value,'u':u,'unit':unit,'distribution':'normal','source':'authored synthetic benchmark'}
        output=propagate(spec);result={'value':output['analytic']['estimate'],'u':output['analytic']['u'],'unit':'kg/m^3'}
    elif case_id=='synthetic-uniform-coverage':
        lower,upper=obj['lower'],obj['upper'];mean=(lower+upper)/2;u=(upper-lower)/math.sqrt(12);half=.95*(upper-lower)/2
        result={'value':mean,'u':u,'lower':mean-half,'upper':mean+half,'k95':half/u}
    elif case_id=='synthetic-missing-instrument':
        result=None;status='needs_information';issues=['instrument_spec_missing']
    else:raise ValueError(f'no independent reference check for {case_id}')
    return {'status':status,'result':result,'issues':issues}


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--out',required=True);a=parser.parse_args()
    value=validate_public();write_json(a.out,value)
    print(f"Public reference validation: {sum(t['passed'] for t in value['tasks'])}/{len(value['tasks'])}; no LLM calls")
    if not value['all_passed']:raise SystemExit(1)
