# 环境与可移植性

## 仓库布局

仓库根目录包含 README、AGENTS.md、.agents、.opencode、labcore、evaluation、references 和 tests。内部资源以项目相对位置定位，不依赖机器用户名、盘符或外部工作区。

## 环境要求

- Python 3.12 或 3.14；
- 依赖见 requirements.txt；Punpy 蒙特卡洛后端为可选依赖；
- 真实 Agent 会话需要自行安装 OpenCode CLI 并配置兼容的模型服务商；
- Windows 上真实任务资源限制使用 Job Object；其他系统可运行数值模块和已支持的检查，但隔离能力需依据平台说明判断。

## 本机数据与配置

模型凭据、.env、*.local.json、本机运行结果、缓存、虚拟环境、RSI runs、dist、data/local 和 data/private 不属于公开源码包。仓库中的 .env.example 仅列出环境变量名称，不含凭据。提交前仍需检查暂存文件清单和差异。

## 移植注意事项

所有命令从仓库根目录运行。项目路径可包含空格；CLI 参数作为独立参数传递。配置解析成功不等于模型服务可用。数值结果应记录依赖版本、模型标识、运行条件和适用容差。

本仓库是研究源码工作区，不是独立 PyPI 分发包。仅复制某个 Skill 文件不能替代 labcore、脚本和参考资料。