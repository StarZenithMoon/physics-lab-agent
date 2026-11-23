# 方法来源与版本登记

核查日期：2026-11-03。供三个 Skill 按问题读取；只保存导航和本项目的应用说明，不转载标准全文。标准版本、适用范围和软件约定分别记录，不能把推荐性指南说成所有实验的强制法规。

| 来源 | 核验范围与用途 |
| --- | --- |
| [BIPM/JCGM 官方指南目录](https://www.bipm.org/en/web/guest/publications/guides) | 入口及版本；GUM-1:2023 为导论，GUM-6:2020 用于模型构建；具体计算仍核对相应章节 |
| [JCGM 100:2008](https://doi.org/10.59161/JCGM100-2008E) | A/B评定、标准不确定度、相关传播、覆盖和结果表达；与2026修订一起使用。原PDF本次抓取超时，具体补充核验采用下列已读的NIST章节，不假称已重新通读全文 |
| [JCGM 100:2008/Amd.1:2026](https://doi.org/10.59161/PPDI3267) | 已核验修订正文：重要非线性可能同时影响输出估计与不确定度，不能仅修正方差；不是一律用MC均值替代模型点值 |
| [JCGM 101:2008](https://doi.org/10.59161/JCGM101-2008) | 已核验目录及相关方法：第5章适用条件、第6章输入分布、第7章MC与自适应精度、第8章验证比较；分布传播、有限矩、区间与数值容差需分别判断 |
| [GUM-6:2020](https://doi.org/10.59161/JCGMGUM-6-2020) | 模型构建/使用的参考，物理模型依据和输入依赖不能由求解器或量纲检查替代 |
| [NIST TN 1297](https://www.nist.gov/pml/nist-technical-note-1297) | 1994版方法说明；是NIST指南而非中国国标。部分网页提示停止维护，不能据网页更新时间称内容为2026新版 |

NIST 已核验章节：[第2节](https://www.nist.gov/pml/nist-technical-note-1297/nist-tn-1297-2-classification-components-uncertainty)区分评定方法与随机/系统效应；[第5节](https://www.nist.gov/pml/nist-technical-note-1297/nist-tn-1297-5-combined-standard-uncertainty)涵盖修正及协方差合成；[第6节](https://www.nist.gov/pml/nist-technical-note-1297/nist-tn-1297-6-expanded-uncertainty)说明覆盖因子取决于概率与分布条件；[第7节](https://www.nist.gov/pml/nist-technical-note-1297/nist-tn-1297-7-reporting-uncertainty)用于结果表达与复现信息。

## 中国标准：官方状态与内容核验分开

| 标准及官方页 | 当前状态 | 本项目用途 |
| --- | --- | --- |
| [GB/T 27418—2017 测量不确定度评定和表示](https://std.samr.gov.cn/gb/search/gbDetailed?id=71F772D8230BD3A7E05397BE0A0AB82A) | 推荐性、现行；2025-04-30复审继续有效；修改采用ISO/IEC Guide 98-3:2008 | 中国标准版本入口，作为不确定度方法对照 |
| [GB/T 27419—2018 蒙特卡洛补充文件](https://std.samr.gov.cn/gb/search/gbDetailed?id=71F772D82C8FD3A7E05397BE0A0AB82A) | 推荐性、现行；修订计划20254931-T-469正在批准，计划不是替代后的正式标准 | MC对照，不能把修订计划当新版规范 |
| [GB/T 8170—2008 数值修约规则与极限数值的表示和判定](https://std.samr.gov.cn/gb/search/gbDetailed?id=71F772D7F507D3A7E05397BE0A0AB82A) | 推荐性、现行；2025-03-27复审继续有效 | 修约、全数值/修约值判定的标准入口 |

本次已确认以上官方元数据，尚未逐条核验国标全文；因此不虚构国标条款编号或认证“完全符合国标”。指定中国标准的任务，需依据可查的标准正文锁定具体条款和修约约定。数值算法容差与最终报告位数分开；本项目不把“所有不确定度一律两位有效数字”当作国标原文。JJF 属于计量技术规范，不能与 GB/T 混称；未核验现行状态的不纳入冻结依据。

## 使用规则

按本次问题选来源：数据解释先看原始记录/仪器，拟合接口看库官方文档，不确定度看相应指南。记录所用版本、章节、读取范围和本项目解释。有冲突时明确采用哪一套约定；缺少实验特定事实时查通用指南不能替代追问。检索不可用时可以用已知依据完成限定分析，但不声称已核验现行标准。
