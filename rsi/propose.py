import argparse
from pathlib import Path
from labcore.io import read_json


def make_request(run, output):
    run = Path(run)
    state = read_json(run / "state.json")
    incumbent = run / state["incumbent_file"]
    train = read_json(run / state["training_feedback"])
    import json
    prompt = f"""请改进物理实验助教的不确定度 Skill。只输出完整 SKILL.md，不输出代码围栏或其他文件。

只修改触发说明、方法判断、工作流和输出要求。保持 YAML name=measurement-uncertainty。
不新增或修改脚本，不编造运行效果，不写针对任务 ID 的特殊答案。
不改变科学边界：原始数据只读；输入分布和相关性有依据；两路差异要解释；不统一固定 k=2 或 5% 通过线。
保持当前Skill的入口/规则/工作流/输出/禁止行为/可选支持资源/来源对应结构；保留资源链接及按需读取条件。保留信息入口、来源、A/B类、相关性、覆盖/自由度与比较的职责。
固定科学方法、后端参考、共享运行时和判卷程序均不可修改；可以引用它们，不能通过削掉适用条件或真实证据要求提高分数。

当前版本：
{incumbent.read_text(encoding='utf-8')}

仅供改进的训练反馈（不含选版和最终测试）：
{json.dumps(train, ensure_ascii=False, indent=2)}
"""
    output = Path(output)
    if output.exists():
        raise FileExistsError(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(prompt, encoding="utf-8")


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="Export a proposal request; never calls an API by itself")
    p.add_argument("--run", required=True)
    p.add_argument("--out", required=True)
    a = p.parse_args()
    make_request(a.run, a.out)
    print(f"proposal request written: {a.out}; save the actual model response as a candidate SKILL.md")
