# ChessX

基于局部观测、角色私人记忆和文本战斗的 AI 涌现叙事实验。
仓库根目录就是本项目的唯一 Git 主仓库；规范已保存在 docs，参考项目材料保留于仓库外，原目录文件保持不动。

## 运行

需要 Python 3.12+、uv、Node.js 22+、pnpm。依赖版本分别锁在 uv.lock 和 frontend/pnpm-lock.yaml。

```sh
uv sync --locked
pnpm --dir frontend install --frozen-lockfile
./scripts/dev.sh
```

打开 http://127.0.0.1:5180 ，后端 http://127.0.0.1:8001 ，API文档 http://127.0.0.1:8001/docs 。
默认开发服务仅绑定本机地址，单进程运行；不要增加uvicorn worker或对公网暴露。

新增网页预设生成器：系统字段与私人Markdown分开填写，保存后自动落盘，支持跨预设组组装对局。内置示例只读；自己的预设保存在Git忽略的 `data/presets/`。

没有模型配置也可以**明确选择工程固定响应模式**验证界面／规则。该模式使用可识别的测试替身，
不是AI，不作为涌现结论；不会访问任何模型服务。真实模式必须通过公开CoT模型准入与tokenizer校验。

## 密钥

唯一填写位置：根目录 `.env.local`，参见 [本地模型配置](docs/LOCAL_MODEL_SETUP.md)。
若文件不存在，从 `.env.example` 复制；不要把真实key写进示例、README、场景、VITE变量或对话。
该文件和data／exports／logs均Git忽略，预提交守卫另外检查误暂存与凭证模式。

```sh
cp .env.example .env.local   # 仅首次；已经填写时绝不要覆盖
chmod 600 .env.local
```

填写后重启后端。真实首次run在本段调用预算内探测能力，失败不降级。请先1回合／最多10调用，
不要直接长局。预算包括重试；计费金额不可精确封顶，界面token统计不保证等于提供方账单。

## 实现与维护

- [已确认设计规范v2.1](docs/ChessX-Spec-v2.1.md)：R01–R16的执行基线。
- [人物与预设编写](docs/CHARACTER_AUTHORING.md)：完整私人Markdown、共用纪律与跨组引用。
- [交谈与行动规范](docs/AGENT_BEHAVIOR.md)：自然台词、内部引用拦截、移动意识与吟游诗人成稿长度。
- [Changelog](CHANGELOG.md)：每版变更与验收记录。
- [架构与扩展](docs/ARCHITECTURE.md)：模块责任、数据与事件事务、权限边界。
- [验收记录](docs/VALIDATION.md)：实际验证范围、真实模型结果和限制。
- [贡献约定](AGENTS.md)：子代理／开发者的目录责任和质量门禁。

运行、暂停、续跑、逐提交／逐回合单步、当前槽重试、会话、50句通行交涉、物件拒收、
到场参战、多方胜败与全票释放、死亡掉落、记忆／目标版本、分段故事、导出／回放均有独立协议。
没有独立吃子、数值奖惩、远程参战、兵升变、阵营胜利或自动终局。

## 检查

```sh
uv run pytest
uv run ruff check backend tests scripts
uv run ruff format --check backend tests scripts
pnpm --dir frontend typecheck
pnpm --dir frontend test
pnpm --dir frontend build
pnpm --dir frontend format:check
python3 scripts/check_secrets.py --all
```

本地启用提交门禁：`git config core.hooksPath .githooks`。
仓库不自动添加Git remote，不上传密钥、运行数据库、prompt、CoT或研究导出。
