# 本地模型配置与密钥保护

填写位置：仓库根目录 `.env.local`，不是任何 Markdown 文档、场景或前端配置。
这个文件已创建、Git忽略、权限0600。不要把密钥发到对话或截图里。

```dotenv
CHESSX_API_KEY=
CHESSX_BASE_URL=
CHESSX_MODEL=
CHESSX_RESPONSE_FORMAT=json_schema
CHESSX_TOKENIZER_NAME=cl100k_base
CHESSX_TOKENIZER_AGREEMENT=false
```

- `CHESSX_BASE_URL` 是模型服务的基础API地址，一般截至 `/v1`；后端追加 `/chat/completions`。不要在URL查询参数／用户名／密码中放密钥。
- `CHESSX_MODEL` 必须是该服务实际支持、能返回公开CoT文本的模型ID。
- `CHESSX_RESPONSE_FORMAT` 按服务实际能力选择 `json_schema`、`json_object` 或 `text`。无严格schema的服务仍经相同业务校验；没有CoT则直接报错，不能降级。
- `CHESSX_TOKENIZER_NAME` 指与该配置实际匹配的tiktoken编码名称。默认cl100k_base只用于工程fixture，**不能假定它对应任意模型**。核实服务／模型使用该编码后才将 `CHESSX_TOKENIZER_AGREEMENT=true`；当前支持显式tiktoken编码和下文DeepSeek官方V4 JSON；其他模型不匹配时禁止真实运行，不猜测计数。
- `CHESSX_CONTEXT_CAPACITY` 对应提供方文档中的总上下文容量，默认16384。请求先校验阶段上限及正式输出／公开CoT预留，超限时在调用前暂停。
- 修改文件后重启后端，不把配置写进Vite `VITE_*`变量。浏览器只收到是否配置完成的布尔值。

真实模式首次明确启动run时，在本段调用预算内做一个无角色秘密的公开CoT能力探测；创建世界本身不调用API。每个世界回合会有行动、协议、记忆和叙事请求；不要先跑长局。建议先2人、1回合、最大9个正式操作，加首次探测最多10个请求。工程fixture不调用任何外部模型；它是测试替身，不是AI涌现结果。

后端不会记录Authorization头，也不向UI返回上游原始错误。公开CoT、角色prompt、收件箱、数据库、研究导出均属于本地私人研究资料，位于被忽略的data／exports路径；普通导出不含私人档案／CoT。不要自行上传研究导出。

程序读取API key为SecretStr，只在HTTP Authorization中使用。预提交检查阻止环境文件、私有运行目录和常见密钥进入Git；不能仅依赖忽略规则防误提交。仓库没有自动添加remote、push或上传机制。

## 错误定位

- MODEL_NOT_CONFIGURED：本地key／base URL／model缺项。
- TOKENIZER_UNAVAILABLE：尚未核实编码；不要为跳过检查盲目设置true。
- MODEL_COT_UNSUPPORTED／MODEL_COT_MISSING：所用服务配置未返回可识别推理文本。
- MODEL_TIMEOUT：请求在配置总时限内未完成；保活字节不会延长总时限，不视为能力探测成功。
- COT_BUDGET_EXCEEDED／SPEECH_BUDGET_EXCEEDED：响应超规格上限，未生效。
- PAUSED_BUDGET：预算用尽，保存当前cursor；增加预算后明确续跑。

不要粘贴完整上游日志排错，只报告错误代码。密钥疑似进入任何公开位置时在服务端撤销重发，不把原密钥再发送给协作者。

## DeepSeek / deepseek-flash 已核实接入配置

依据[官方思考模式](https://api-docs.deepseek.com/guides/thinking_mode/)及
[官方JSON输出](https://api-docs.deepseek.com/guides/json_mode/)，本项目显式开启thinking，
使用reasoning_effort=low与json_object；没有工具调用，因此不回传历史CoT。
模型具体版本随服务更新，实验必须保留配置与日期。

[官方token说明](https://api-docs.deepseek.com/quick_start/token_usage/)提供V4 tokenizer。
仅安全提取tokenizer.json并验证SHA-256，不下载权重或执行远程Python代码：

```sh
uv run python scripts/setup_deepseek_tokenizer.py
```

相应 `.env.local` 非密钥字段：

```dotenv
CHESSX_PROVIDER_PROFILE=deepseek
CHESSX_MODEL=deepseek-flash
CHESSX_RESPONSE_FORMAT=json_object
CHESSX_TOKENIZER_NAME=deepseek_v4
CHESSX_TOKENIZER_AGREEMENT=true
```

已根据本次确认更新这些非密钥字段，保留你原来填写的key与地址。TOKENIZER_AGREEMENT=true
在这里有官方资产作为依据；其他模型不能照抄。公开API本身以usage计费，分词字符串计数与
服务端消息框架开销不同，不宣称本地估算等于账单。

真实短局脚本 `scripts/live_smoke.py` 固定2角色、1回合、最多10调用（包含探测／重试）；
汇总只输出计数、token及错误代码，详细prompt与CoT保存在被忽略的本地data数据库。
2026-10-02用户已明确授权试验所需的角色资料、记忆、局部观测与对话发送至配置的DeepSeek API；先前外发限制已解除。
该授权不包含向模型正文、界面或Git发送密钥，也不包含上传至其他服务。能力校验、输入预算和密钥守卫继续有效。

网页中选择预设组→“真实模型实验”→创建世界→设定小额调用预算→运行1回合。
创建世界不消耗模型调用；首次运行会先做公开CoT能力探测。已有工程fixture世界不能直接变成真实模式，需要新建世界。
命令行也可使用 `uv run python scripts/live_smoke.py --max-calls 3` 缩小请求上限。

本次鉴权及模型列表检查通过，两次生成探测未完成，已停止追加付费请求；具体结果见 `VALIDATION.md`。
