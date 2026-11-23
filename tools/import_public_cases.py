"""Fetch NIST numerical observations and build original, attributed benchmark tasks.

No complete JCGM publication or private course material is redistributed.
Network is used only when this maintenance command is explicitly invoked.
"""
import argparse
import csv
import hashlib
import re
from urllib.request import urlopen, Request
from labcore.io import write_json
from labcore.project import ROOT

NIST = {
    "norris": {"file":"Norris", "section":"lls", "n":36, "model":"linear", "scale":1,
               "initial":{"intercept":0,"slope":1},
               "certified":{"intercept":-.262323073774029,"slope":1.00211681802045},
               "parameter_sd":[.232818234301152,.000429796848199937],"residual_std":.884796396144373},
    "pontius": {"file":"Pontius", "section":"lls", "n":40, "model":"quadratic", "scale":1e6,
                "initial":{"intercept":0,"linear":.7,"quadratic":-.003},
                "certified":{"intercept":.000673565789473684,"linear":.732059160401003e-6,
                             "quadratic":-.316081871345029e-14},
                "parameter_sd":[.107938612033077e-3,.157817399981659e-9,.486652849992036e-16],
                "residual_std":.205177424076185e-3},
    "misra1a": {"file":"Misra1a", "section":"nls", "n":14, "model":"expression", "scale":1000,
                "initial":{"amplitude":500,"rate":.1},
                "expression":"amplitude*(1-exp(-rate*x))",
                "certified":{"amplitude":2.3894212918e2,"rate":5.5015643181e-4},
                "parameter_sd":[2.7070075241,7.2668688436e-6],"residual_std":1.0187876330e-1}
}
NIST.update({
    "rat42":{"file":"Rat42","section":"nls","n":9,"model":"expression","scale":1,
        "expression":"amplitude/(1+exp(center-rate*x))","initial":{"amplitude":100,"center":1,"rate":.1},
        "certified":{"amplitude":72.462237576,"center":2.6180768402,"rate":.067359200066},
        "parameter_sd":[1.7340283401,.088295217536,.0034465663377],"residual_std":1.1587725499},
    "chwirut2":{"file":"Chwirut2","section":"nls","n":54,"model":"expression","scale":1,
        "expression":"exp(-decay*x)/(offset+linear*x)","initial":{"decay":.1,"offset":.01,"linear":.02},
        "certified":{"decay":.16657666537,"offset":.0051653291286,"linear":.012150007096},
        "parameter_sd":[.038303286810,.00066621605126,.0015304234767],"residual_std":3.1717133040},
    "boxbod":{"file":"BoxBOD","section":"nls","n":6,"model":"expression","scale":1,
        "expression":"amplitude*(1-exp(-rate*x))","initial":{"amplitude":100,"rate":.75},
        "certified":{"amplitude":213.80940889,"rate":.54723748542},
        "parameter_sd":[12.354515176,.10455993237],"residual_std":17.088072423}
})
GUM100="https://www.bipm.org/documents/20126/2071204/JCGM_100_2008_E.pdf"
GUM101="https://www.bipm.org/documents/20126/2071204/JCGM_101_2008_E.pdf"


def csv_write(path, header, rows):
    path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('w',encoding='utf-8',newline='') as stream:
        writer=csv.writer(stream);writer.writerow(header);writer.writerows(rows)


def numeric_check(path, target, atol=0, rtol=0):
    return {"path":path,"target":target,"atol":atol,"rtol":rtol,"critical":True}


def task(case_id, split, instruction, checks, evidence, source, capabilities):
    from evaluation.rubric import specification
    item = {"id":case_id,"split":split,"group":case_id,"capabilities":capabilities,
            "source":source,"reference_status":"independently_checkable_development_reference",
            "public":{"data_directory":f"data/public/{case_id}","required_evidence":list(evidence),"instruction":instruction+
                " 输出直接放 results/，包含 report.json/report.md；数值结果保留保护位数，实际执行并引用证据。"},
            "expected":{"checks":checks,"evidence":evidence}}
    item['expected']['rubric'] = specification(item)
    return item


def build(root=ROOT):
    tasks=[]
    for key,info in NIST.items():
        url=f"https://www.itl.nist.gov/div898/strd/{info['section']}/data/LINKS/DATA/{info['file']}.dat"
        with urlopen(Request(url,headers={"User-Agent":"physics-lab-agent reference importer"}),timeout=30) as response:
            raw=response.read()
        text=raw.decode('ascii')
        lines=text.splitlines()
        start=next(i for i,line in enumerate(lines) if re.match(r'^Data:\s+y\s+x',line))
        observations=[]
        for line in lines[start+1:]:
            values=line.split()
            if len(values)==2:
                try:observations.append([float(values[0]),float(values[1])])
                except ValueError:pass
        if len(observations)!=info['n']:
            raise ValueError(f"unexpected observation count for {key}; review the source before updating")
        case_id='nist-'+key
        folder=root/'data/public'/case_id
        csv_write(folder/'raw.csv',['y','x'],observations)
        x=[r[1]/info['scale'] for r in observations];y=[r[0] for r in observations]
        spec={"x":x,"y":y,"x_unit":"scaled_source_coordinate","y_unit":"source_coordinate",
              "model":info['model'],"parameters":info['initial'],"backend":"scipy","absolute_sigma":False}
        if 'expression' in info:spec['expression']=info['expression']
        write_json(folder/'fit-input.json',spec)
        normalized=dict(info['certified'])
        sd=list(info['parameter_sd'])
        if key=='pontius':
            normalized['linear']*=info['scale'];normalized['quadratic']*=info['scale']**2
            sd[1]*=info['scale'];sd[2]*=info['scale']**2
        elif key=='misra1a':normalized['rate']*=info['scale'];sd[1]*=info['scale']
        source={"organization":"NIST/ITL StRD","url":url,"retrieved":"2026-11-02",
                "source_sha256":hashlib.sha256(raw).hexdigest(),"data_kind":"public_reference_observations"}
        write_json(folder/'meta.json',{**source,"n":info['n'],"raw_columns":["y","x"],
            "x_transform":f"x_model = x_source / {info['scale']:g}",
            "units":"The source numerical coordinates are retained; no SI unit is inferred.",
            "uncertainty_convention":"unweighted least squares, covariance scaled by residual variance; not known instrument uncertainty",
            "model":info.get('expression',info['model']),"source_answer_removed_from_inputs":True})
        reference={**source,"original_parameters":info['certified'],"normalized_parameters":normalized,
                   "normalized_parameter_sd":sd,"residual_std":info['residual_std']}
        write_json(root/'evaluation/public-reference'/f'{case_id}.json',reference)
        checks=[{"path":"status","equals":"complete","critical":True}]
        checks += [numeric_check('result.parameters.'+name,value,atol=2e-7 if key=='norris' else 1e-8,rtol=2e-5)
                   for name,value in normalized.items()]
        checks += [numeric_check('result.residual_std',info['residual_std'],rtol=2e-5)]
        tasks.append(task(case_id,{'norris':'train','pontius':'selection','misra1a':'test','rat42':'test','chwirut2':'selection','boxbod':'train'}[key],
            "读取 raw.csv、meta.json 与 fit-input.json，按给定缩放坐标和模型做无权最小二乘，不能把残差尺度当已知仪器精度。"
            "参数使用 fit-input.json 中名称/坐标，写 result.parameters 与 result.residual_std；保留参数协方差和残差。"
            "本任务只检验给定数值模型，不推断仪器或物理真实性。",checks,['fit.json'],source,
            ['data','fit','covariance_convention']))

    case_id='gum-h2-impedance'
    folder=root/'data/public'/case_id
    rows=[[5.007,19.663,1.0456],[4.994,19.639,1.0438],[5.005,19.640,1.0468],
          [4.990,19.685,1.0428],[4.999,19.678,1.0433]]
    csv_write(folder/'raw.csv',['V_V','I_mA','phi_rad'],rows)
    source={"organization":"JCGM","url":GUM100,"section":"H.2, Tables H.2-H.4",
            "data_kind":"published_numerical_example"}
    write_json(folder/'meta.json',{**source,"procedure":"Five simultaneous triples; keep pairing. I is in mA.",
        "model":{"R":"(V/I)*cos(phi)","X":"(V/I)*sin(phi)","Z":"V/I"},
        "scope":"Type A repeatability only, use the mean of per-triple outputs (H.2 approach 2). No extra instrument components are specified.",
        "mc":"Not requested: assigning an extra joint PDF is outside this raw-observation task."})
    refs={"R":{"value":127.732,"u":.071},"X":{"value":219.847,"u":.295},"Z":{"value":254.260,"u":.236}}
    write_json(root/'evaluation/public-reference'/f'{case_id}.json',{**source,"printed_results":refs,
               "tolerance_basis":"0.002 ohm allows printed rounding; compute from original five triples independently."})
    checks=[{"path":"status","equals":"complete","critical":True}]
    for name,ref in refs.items():
        checks.extend([numeric_check(f'result.{name}.value',ref['value'],atol=.002),
                       numeric_check(f'result.{name}.u',ref['u'],atol=.002)])
    tasks.append(task(case_id,'selection',
        "按 meta.json 的限定任务，用五组同时观测逐组计算 R/X/Z（电流先换为 A），然后评定各组结果均值及均值标准不确定度；"
        "保持配对关系，报告输出相关性。结果写 result.R/X/Z 的 value、u。不要增加本题未给出的仪器分量。",
        checks,['propagation.json','analysis.py'],source,['data','correlation','type_a','units']))

    for name,distribution,uncertainties,split,section in [
        ('normal','normal',[1,1,1,1],'train','9.2.2'),
        ('uniform-equal','uniform',[1,1,1,1],'train','9.2.3'),
        ('uniform-dominant','uniform',[1,1,1,10],'train','9.2.4')]:
        case_id='jcgm101-additive-'+name
        source={"organization":"JCGM","url":GUM101,"section":section,
                "data_kind":"published_model_reimplemented"}
        write_json(root/'data/public'/case_id/'inputs.json',{**source,"model":"Y=X1+X2+X3+X4",
            "unit":"1","independent":True,"inputs":[{"name":f'X{i+1}',"value":0,"u":u,
                "distribution":distribution} for i,u in enumerate(uncertainties)],
            "scope":"Given mathematical inputs, no missing experimental instrument facts.",
            "mc":{"minimum_samples":200000,"batches":5,"coverage":.95,"seed":42}})
        # Independent closed-form convolution is maintained in evaluation/oracles.py.
        from evaluation.oracles import additive_reference
        ref=additive_reference(distribution,uncertainties)
        write_json(root/'evaluation/public-reference'/f'{case_id}.json',{**source,**ref,
             "tolerance_basis":"0.05 standard-u and 0.08 endpoint tolerance for >=200000 MC samples; not a universal GUM validation threshold."})
        checks=[{"path":"status","equals":"complete","critical":True},
                numeric_check('result.value',0,atol=.08),numeric_check('result.u',ref['u'],atol=.05),
                numeric_check('result.lower',ref['lower'],atol=.08),numeric_check('result.upper',ref['upper'],atol=.08),
                {"path":"result.mc_samples","min":200000,"critical":True}]
        tasks.append(task(case_id,split,
            "按 inputs.json 的独立分布评定无量纲四项和，运行公式与 MC，记录种子、样本量、批次和稳定性；"
            "标准不确定度相同不代表覆盖区间相同。写 result.value/u/lower/upper/mc_samples，lower/upper 为95%等尾覆盖端点。"
            "当前固定 helper 若不支持四输入，可在 results/ 写并实际执行 analysis.py。",
            checks,['propagation.json','analysis.py'],source,['propagation','coverage','monte_carlo']))
    for t in tasks:
        if t['id'].startswith('jcgm101-additive-'):t['group']='jcgm101-additive'
    from evaluation.standard_cases import build_standard_cases
    tasks.extend(build_standard_cases(root))
    write_json(root/'evaluation/public-cases.json',{"schema_version":"0.2","visibility":"public",
        "notice":"Public development references, not hidden/generalization evidence. Related variants must be grouped before formal experiments.",
        "tasks":tasks})
    print(f"Built {len(tasks)} public cases (six NIST datasets, five JCGM cases, thirteen authored synthetic cases).")


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--fetch',action='store_true',required=True)
    parser.parse_args();build()
