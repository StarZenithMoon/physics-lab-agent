# 验证说明

验证包括数值程序检查、公开案例参考值自检、任务执行和候选版本比较。

~~~powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe -m evaluation.oracles --out results/reference-check.json
.\.venv\Scripts\python.exe -m labcore.demo --out results/demo
~~~

参考值自检针对仓库随附的24个公开案例，不调用模型。公开案例按train、selection、test划分；该test split随仓库发布。候选生成使用train反馈，selection用于版本比较，test在选版后检查。
