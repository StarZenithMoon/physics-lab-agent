"""Authored mathematical test problems and a small attributed GUM data example.

These are transparent development references, not private/hidden test data.
"""
import math
import numpy as np
from scipy.stats import t, chi2
from labcore.io import write_json


def build_standard_cases(root):
    from tools.import_public_cases import task, numeric_check as num, csv_write, GUM100
    tasks=[]
    def add(case_id,split,instruction,inputs,result,checks,capabilities,source=None,extra=None):
        source=source or {'organization':'physics-lab-agent authors','data_kind':'authored_synthetic',
                         'method_source':'GUM/JCGM methods; numerical inputs are original test design, not copied official experiments'}
        folder=root/'data/public'/case_id
        write_json(folder/'inputs.json',{'source':source,**inputs})
        if extra:
            for name,obj in extra.items():write_json(folder/name,obj)
        write_json(root/'evaluation/public-reference'/f'{case_id}.json',{'source':source,'report':result,
                     'basis':'Independent algebra/distribution or certified reference; no Agent-generated answer used.'})
        checks=[{'path':'status','equals':result['status'],'critical':True},*checks]
        evidence=['intake.json'] if result['status']=='needs_information' else ['analysis.py','analysis.json']
        tasks.append(task(case_id,split,instruction,checks,evidence,source,capabilities))

    # H.3: only factual numerical observations and a newly written task, not the GUM prose/PDF.
    temps=np.array([21.521,22.012,22.512,23.003,23.507,23.999,24.513,25.002,25.503,26.010,26.511])
    corr=np.array([-.171,-.169,-.166,-.159,-.164,-.165,-.156,-.157,-.159,-.161,-.160])
    X=np.column_stack([np.ones(11),temps-20]);beta=np.linalg.lstsq(X,corr,rcond=None)[0]
    cov=float(np.sum((corr-X@beta)**2)/9)*np.linalg.inv(X.T@X)
    predicted=float(np.array([1,10])@beta);pred_u=float(np.sqrt(np.array([1,10])@cov@np.array([1,10])))
    spec={'x':(temps-20).tolist(),'y':corr.tolist(),'x_unit':'degC_increment','y_unit':'degC_increment','model':'linear','absolute_sigma':False}
    add('gum-h3-thermometer','selection',
        '用 raw.csv 温度/修正量拟合 b(t)=intercept+slope*(t-20°C)，无权、残差缩放协方差。报告 result.intercept/slope，t=30°C 的 result.correction_30 与“拟合均值修正量”的 result.u_correction_30；保留斜率/截距协方差，不能加成新一次读数的预测散布。明确30°C属于外推，status=limited，issues包含 extrapolation。',
        {'temperature_unit':'degC','correction_unit':'degC','scope':'Fitted mean correction only; no new-reading noise or instrument budget requested.'},
        {'status':'limited','result':{'intercept':float(beta[0]),'slope':float(beta[1]),'correction_30':predicted,'u_correction_30':pred_u},'issues':['extrapolation']},
        [num('result.intercept',float(beta[0]),atol=1e-7),num('result.slope',float(beta[1]),atol=1e-8),
         num('result.correction_30',predicted,atol=1e-7),num('result.u_correction_30',pred_u,atol=1e-7),
         {'path':'issues','contains':'extrapolation','critical':True}],['fit','covariance','prediction','model_limits'],
        source={'organization':'JCGM','url':GUM100,'section':'H.3, Table H.6','data_kind':'published_numerical_example'},
        extra={'fit-input.json':spec})
    csv_write(root/'data/public/gum-h3-thermometer/raw.csv',['temperature_degC','correction_degC'],zip(temps,corr))

    values=np.array([10.12,10.08,10.11,10.09,10.10]);mean=float(values.mean());sem=float(values.std(ddof=1)/np.sqrt(5));k=float(t.ppf(.975,4))
    add('synthetic-repeat-mean-t','train',
        '仅评定 inputs.json 中独立重复读数均值的 A 类分量，正态总体方差未知。报告 result.value/u/df/k95/U95；95%双侧用 Student t，不另加题目未给的仪器分量。',
        {'values':values.tolist(),'unit':'mm','independent':True,'population':'normal, variance unknown','scope':'Type A mean only'},
        {'status':'complete','result':{'value':mean,'u':sem,'df':4,'k95':k,'U95':k*sem}},
        [num('result.value',mean,atol=1e-10),num('result.u',sem,atol=1e-10),num('result.df',4),num('result.k95',k,atol=1e-7),num('result.U95',k*sem,atol=1e-9)],['type_a','degrees_of_freedom','coverage'])
    u=.1/math.sqrt(12)
    add('synthetic-resolution','train','仅把0.1 mm数字显示步距带来的舍入分量转为标准不确定度，误差为±d/2矩形分布。报告 result.u；不能把步距直接当 u。',
        {'resolution':.1,'unit':'mm','rounding':'uniform on [-d/2,d/2]','scope':'single rounding component'},
        {'status':'complete','result':{'u':u}},[num('result.u',u,atol=1e-12)],['type_b','resolution'])
    add('synthetic-calibration-certificate','train','读数10 mV，证书修正量+0.002 mV；该修正的扩展不确定度0.004 mV、k=2。其他分量按本题设定为零。报告修正后 result.value 和标准 result.u，不能把U当u或把修正量当不确定度。',
        {'reading':10,'correction':.002,'expanded_U':.004,'k':2,'unit':'mV','other_components':'zero by synthetic design'},
        {'status':'complete','result':{'value':10.002,'u':.002}},[num('result.value',10.002,atol=1e-12),num('result.u',.002,atol=1e-12)],['type_b','corrections'])
    u=math.hypot(.02,.03)
    add('synthetic-common-zero-cancellation','selection','两长度差Y=(A+C)-(B+C)，A=12 mm、u(A)=0.02 mm，B=10 mm、u(B)=0.03 mm，共享同一个零点C=0、u(C)=0.5 mm。A/B/C独立正态。保留共同C的依赖，报告 result.value/u；不能把两次C当独立。',
        {'A':12,'u_A':.02,'B':10,'u_B':.03,'C':0,'u_C':.5,'unit':'mm','shared_C':True,'independent_latent_inputs':True},
        {'status':'complete','result':{'value':2,'u':u}},[num('result.value',2,atol=1e-10),num('result.u',u,atol=1e-8)],['correlation','measurement_model'])
    low,high=[float(v) for v in chi2.ppf([.025,.975],1)]
    add('synthetic-zero-gradient-square','test','X为标准正态，Y=X²。评定分布传播下的均值、标准不确定度及95%等尾区间，写result.value/u/lower/upper。需MC并解释一阶梯度在0处失效、f(E[X])不等于E[Y]；status=limited，issues含 first_order_invalid。',
        {'model':'Y=X**2','X':{'mean':0,'u':1,'distribution':'normal'},'minimum_mc_samples':200000,'unit':'1'},
        {'status':'limited','result':{'value':1,'u':math.sqrt(2),'lower':low,'upper':high},'issues':['first_order_invalid']},
        [num('result.value',1,atol=.03),num('result.u',math.sqrt(2),atol=.04),num('result.lower',low,atol=.003),num('result.upper',high,atol=.08),
         {'path':'issues','contains':'first_order_invalid','critical':True}],['nonlinear','monte_carlo','coverage'])
    add('synthetic-ratio-undefined-moments','test','Y=1/Z，Z服从[-1,1]均匀分布。判断能否给出有限的总体标准不确定度；不得把一次有限MC样本标准差当存在的总体标准差。写status=limited、result=null，issues含 undefined_moments，并解释分母零点邻域导致矩发散。',
        {'model':'1/Z','Z':{'distribution':'uniform','lower':-1,'upper':1},'unit':'1'},
        {'status':'limited','result':None,'issues':['undefined_moments']},[{'path':'result','equals':None,'critical':True},{'path':'issues','contains':'undefined_moments','critical':True}],['domain','monte_carlo_limits'])

    x=np.arange(5.);y=np.array([1.05,2.92,5.10,6.96,8.97]);delta=4.
    xx=x-x.mean();yy=y-y.mean();sxx=xx@xx;syy=yy@yy;sxy=xx@yy
    slope=float((syy-delta*sxx+np.sqrt((syy-delta*sxx)**2+4*delta*sxy*sxy))/(2*sxy));intercept=float(y.mean()-slope*x.mean())
    spec={'x':x.tolist(),'y':y.tolist(),'ux':[.1]*5,'uy':[.2]*5,'x_unit':'A','y_unit':'V','model':'linear','absolute_sigma':True}
    add('synthetic-odr-deming','selection','按fit-input.json的独立x/y标准不确定度做误差变量直线拟合。报告result.intercept/slope；记录实际后端与观测调整，不得忽略ux。',spec,
        {'status':'complete','result':{'intercept':intercept,'slope':slope}},[num('result.intercept',intercept,atol=2e-5),num('result.slope',slope,atol=1e-5)],
        ['fit','errors_in_variables'],extra={'fit-input.json':spec})
    x=np.linspace(0,6,13);y=2+8*np.exp(-x/1.5)
    spec={'x':x.tolist(),'y':y.tolist(),'uy':[.1]*13,'x_unit':'s','y_unit':'V','model':'exponential_decay',
          'parameters':{'offset':1,'amplitude':{'value':9,'expr':'10-offset'},'tau':2},'absolute_sigma':True}
    add('synthetic-lmfit-constraint','selection','拟合衰减模型并严格保持amplitude=10-offset，不能把依赖参数当第三个自由参数。报告result.offset/amplitude/tau/free_parameter_count并保留独立参数协方差。',spec,
        {'status':'complete','result':{'offset':2,'amplitude':8,'tau':1.5,'free_parameter_count':2}},
        [num('result.offset',2,atol=1e-5),num('result.amplitude',8,atol=1e-5),num('result.tau',1.5,atol=1e-5),num('result.free_parameter_count',2)],['fit','constraints'],extra={'fit-input.json':spec})
    x=np.arange(5.);y=np.array([1.02,3.04,4.95,7.03,8.96]);X=np.column_stack((np.ones(5),x));beta=np.linalg.lstsq(X,y,rcond=None)[0];cov=.2**2*np.linalg.inv(X.T@X)
    spec={'x':x.tolist(),'y':y.tolist(),'uy':[.2]*5,'x_unit':'A','y_unit':'V','absolute_sigma':True}
    add('synthetic-fit-known-sigma','train','已知各点独立标准不确定度uy=0.2 V，按绝对观测方差直线拟合；报告result.intercept/slope/u_intercept/u_slope，不得再用残差把协方差缩小。',spec,
        {'status':'complete','result':{'intercept':float(beta[0]),'slope':float(beta[1]),'u_intercept':float(np.sqrt(cov[0,0])),'u_slope':float(np.sqrt(cov[1,1]))}},
        [num('result.intercept',float(beta[0]),atol=1e-7),num('result.slope',float(beta[1]),atol=1e-7),num('result.u_intercept',float(np.sqrt(cov[0,0])),atol=1e-7),num('result.u_slope',float(np.sqrt(cov[1,1])),atol=1e-7)],
        ['fit','absolute_covariance'],extra={'fit-input.json':spec})
    rows=[[1,1.01,10],[2,1.02,10.1],[2,1.02,10.1],[4,'',10.2],[5,'abc',-10.3],[6,1.05,10.4]]
    add('synthetic-data-quality','train','只做raw.csv质量画像，不删除/替换原始行。报告result.row_count、invalid_time_cells、duplicate_rows、negative_length_cells。负值是待核查观测，不直接当无效单元格；本题重复指整行完全重复。',
        {'units':{'time_s':'s','length_mm':'mm'},'scope':'quality audit, not motion inference'},
        {'status':'complete','result':{'row_count':6,'invalid_time_cells':2,'duplicate_rows':1,'negative_length_cells':1}},
        [num('result.row_count',6),num('result.invalid_time_cells',2),num('result.duplicate_rows',1),num('result.negative_length_cells',1)],['data','traceability'])
    csv_write(root/'data/public/synthetic-data-quality/raw.csv',['reading','time_s','length_mm'],rows)
    mass,D,H=.125,.025,.050;rho=4*mass/(math.pi*D*D*H);u=rho*math.sqrt((.0002/mass)**2+(2*.00002/D)**2+(.00004/H)**2)
    add('synthetic-unit-density','test','给定圆柱m=125 g、D=25 mm、H=50 mm，相应已知标准不确定度0.2 g、0.02 mm、0.04 mm，独立正态且题设模型适用。先记录SI换算，按rho=4m/(pi*D²H)运行公式和MC。写result.value/u/unit，u用一阶传播；MC作核验，不强行固定5%通过线。',
        {'mass_g':125,'diameter_mm':25,'height_mm':50,'standard_u':[.2,.02,.04],'independent':True,'distribution':'normal','scope':'given synthetic model'},
        {'status':'complete','result':{'value':rho,'u':u,'unit':'kg/m^3'}},[num('result.value',rho,rtol=1e-7),num('result.u',u,rtol=1e-6),{'path':'result.unit','equals':'kg/m^3','critical':True}],['units','propagation','monte_carlo'])
    add('synthetic-uniform-coverage','selection','Y在[9.5,10.5] mΩ均匀分布。求均值、标准不确定度和95%等尾区间，写result.value/u/lower/upper/k95；k95定义为95%半宽/u，不能统一取2。',
        {'distribution':'uniform','lower':9.5,'upper':10.5,'unit':'mOhm','coverage':.95},
        {'status':'complete','result':{'value':10,'u':.5/math.sqrt(3),'lower':9.525,'upper':10.475,'k95':.95*math.sqrt(3)}},
        [num('result.value',10,atol=1e-10),num('result.u',.5/math.sqrt(3),atol=1e-10),num('result.lower',9.525,atol=1e-9),num('result.upper',10.475,atol=1e-9),num('result.k95',.95*math.sqrt(3),atol=1e-9)],['coverage','non_normal'])
    add('synthetic-missing-instrument','test','单次长度读数12.3 mm，要求完整不确定度。没有仪器类型/量程/分辨力/准确度、校准或先验重复性，目前无法补充；不能由小数位推出仪器精度。result=null、issues含instrument_spec_missing。',
        {'reading':12.3,'unit':'mm','instrument':None,'prior_repeatability':None},
        {'status':'needs_information','result':None,'issues':['instrument_spec_missing']},[{'path':'result','equals':None,'critical':True},{'path':'issues','contains':'instrument_spec_missing','critical':True}],['clarification','instrument_sources'])
    return tasks
