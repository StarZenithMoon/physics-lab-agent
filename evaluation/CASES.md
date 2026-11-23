# 公开案例索引

默认24题：6个NIST数据集、5个GUM/JCGM数值例题、13个自编合成题。所有题均为公开开发题，不是隐藏测试。

2026-11-03全部题补入[科学六维量表](../docs/EVALUATION-RUBRIC.md)及五项逐题复核要求；数据、数值目标、容差和split不变。缺复核的自动暂计分不能作为正式v0或选版依据。

| ID | 分组 | 类型/重点 | 来源 |
| --- | --- | --- | --- |
| [nist-norris](../data/public/nist-norris/) | train | data, fit, covariance_convention | [NIST/ITL StRD](https://www.itl.nist.gov/div898/strd/lls/data/LINKS/DATA/Norris.dat) |
| [nist-pontius](../data/public/nist-pontius/) | selection | data, fit, covariance_convention | [NIST/ITL StRD](https://www.itl.nist.gov/div898/strd/lls/data/LINKS/DATA/Pontius.dat) |
| [nist-misra1a](../data/public/nist-misra1a/) | test | data, fit, covariance_convention | [NIST/ITL StRD](https://www.itl.nist.gov/div898/strd/nls/data/LINKS/DATA/Misra1a.dat) |
| [nist-rat42](../data/public/nist-rat42/) | test | data, fit, covariance_convention | [NIST/ITL StRD](https://www.itl.nist.gov/div898/strd/nls/data/LINKS/DATA/Rat42.dat) |
| [nist-chwirut2](../data/public/nist-chwirut2/) | selection | data, fit, covariance_convention | [NIST/ITL StRD](https://www.itl.nist.gov/div898/strd/nls/data/LINKS/DATA/Chwirut2.dat) |
| [nist-boxbod](../data/public/nist-boxbod/) | train | data, fit, covariance_convention | [NIST/ITL StRD](https://www.itl.nist.gov/div898/strd/nls/data/LINKS/DATA/BoxBOD.dat) |
| [gum-h2-impedance](../data/public/gum-h2-impedance/) | selection | data, correlation, type_a, units | [H.2, Tables H.2-H.4](https://www.bipm.org/documents/20126/2071204/JCGM_100_2008_E.pdf) |
| [jcgm101-additive-normal](../data/public/jcgm101-additive-normal/) | train | propagation, coverage, monte_carlo | [9.2.2](https://www.bipm.org/documents/20126/2071204/JCGM_101_2008_E.pdf) |
| [jcgm101-additive-uniform-equal](../data/public/jcgm101-additive-uniform-equal/) | train | propagation, coverage, monte_carlo | [9.2.3](https://www.bipm.org/documents/20126/2071204/JCGM_101_2008_E.pdf) |
| [jcgm101-additive-uniform-dominant](../data/public/jcgm101-additive-uniform-dominant/) | train | propagation, coverage, monte_carlo | [9.2.4](https://www.bipm.org/documents/20126/2071204/JCGM_101_2008_E.pdf) |
| [gum-h3-thermometer](../data/public/gum-h3-thermometer/) | selection | fit, covariance, prediction, model_limits | [H.3, Table H.6](https://www.bipm.org/documents/20126/2071204/JCGM_100_2008_E.pdf) |
| [synthetic-repeat-mean-t](../data/public/synthetic-repeat-mean-t/) | train | type_a, degrees_of_freedom, coverage | 本项目自编合成（非真实实验） |
| [synthetic-resolution](../data/public/synthetic-resolution/) | train | type_b, resolution | 本项目自编合成（非真实实验） |
| [synthetic-calibration-certificate](../data/public/synthetic-calibration-certificate/) | train | type_b, corrections | 本项目自编合成（非真实实验） |
| [synthetic-common-zero-cancellation](../data/public/synthetic-common-zero-cancellation/) | selection | correlation, measurement_model | 本项目自编合成（非真实实验） |
| [synthetic-zero-gradient-square](../data/public/synthetic-zero-gradient-square/) | test | nonlinear, monte_carlo, coverage | 本项目自编合成（非真实实验） |
| [synthetic-ratio-undefined-moments](../data/public/synthetic-ratio-undefined-moments/) | test | domain, monte_carlo_limits | 本项目自编合成（非真实实验） |
| [synthetic-odr-deming](../data/public/synthetic-odr-deming/) | selection | fit, errors_in_variables | 本项目自编合成（非真实实验） |
| [synthetic-lmfit-constraint](../data/public/synthetic-lmfit-constraint/) | selection | fit, constraints | 本项目自编合成（非真实实验） |
| [synthetic-fit-known-sigma](../data/public/synthetic-fit-known-sigma/) | train | fit, absolute_covariance | 本项目自编合成（非真实实验） |
| [synthetic-data-quality](../data/public/synthetic-data-quality/) | train | data, traceability | 本项目自编合成（非真实实验） |
| [synthetic-unit-density](../data/public/synthetic-unit-density/) | test | units, propagation, monte_carlo | 本项目自编合成（非真实实验） |
| [synthetic-uniform-coverage](../data/public/synthetic-uniform-coverage/) | selection | coverage, non_normal | 本项目自编合成（非真实实验） |
| [synthetic-missing-instrument](../data/public/synthetic-missing-instrument/) | test | clarification, instrument_sources | 本项目自编合成（非真实实验） |

每题在 public-cases.json 中给出完整要求、数值容差、关键失败条件和证据文件。input数据位于 data/public/<id>/；参考答案在 evaluation/public-reference/，不会被 runner 复制进题目工作区。

NIST保留原始数值观测；Pontius的x/1e6和Misra1a的x/1000是明确记录的数值条件化变换，评分使用相应参数坐标。未知原始SI单位不凭空补齐。

GUM/JCGM仅摘取少量事实数值、链接原文并重新编写任务，未打包官方全文、标识或翻译。合成题只借鉴方法，不冒称官方例题。

```text
python -m evaluation.oracles --out results/reference-check.json
```

该命令检验参考值与数值适配，不调用模型，不产生Agent成功率。正式测评仍需固定模型/预算、留存实际运行轨迹并抽检科学解释。
