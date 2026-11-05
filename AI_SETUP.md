# DeepSeek 模型接入

软件 0.12.0。默认离线，安全配置专用密钥并显式启用后，提供两项按需能力：

- 企业助手：先本地检索，再选择“AI 整理回答”；仅发送本次问题与命中的最多5段资料摘录。
- 写作：从来源章节准备采纳预览，再选择“生成 AI 写作建议”；只发送目标段落与所选章节摘录。生成不修改正文，明确采纳后才保存新修订，仍需同事审核。

当前检索仍是关键词检索，不包含向量数据库、Embedding、语义重排或自主工具执行。接入云API也不等于模型完全私有化。

## 安全配置

不要将密钥贴到聊天、Git、网页源码或命令参数。已暴露的密钥应在[DeepSeek密钥控制台](https://platform.deepseek.com/api_keys)撤销并重新创建。

在运行应用的账号下，使用交互式终端录入替换密钥：

```bash
python configure_model.py --key-file /absolute/private-directory/deepseek.key
```

工具无回显，目录必须私有（0700），文件0600；密钥不会出现在参数或标准输出。服务端拒绝符号链接、其他用户所有的文件或组/其他人可读文件。生产环境应使用适当的秘密管理服务，此处仅为小型演示实例的文件方案。

将以下配置放入受保护的服务端环境配置，重启仅本应用：

```ini
WORKBENCH_AI_ENABLED=1
WORKBENCH_AI_KEY_FILE=/absolute/private-directory/deepseek.key
WORKBENCH_AI_MODEL=deepseek-flash
WORKBENCH_AI_SOURCE_IDS=product,delivery,faq
```

默认仅允许三份虚构资料发送到模型，上传、发布或拥有一份资料并不会自动获得外发授权。新资料需要运营方核实授权后显式加入服务端白名单。员工每次调用前仍要勾选外发确认，管理员也不能越权发送员工私有资料。白名单不是匿名化、敏感信息检测或企业数据出境合规审查的替代品。

密钥文件不应与公开静态文件共目录。Docker示例读取持久数据卷的`/data/model-private/deepseek.key`，录入时应确保UID10001可读且其他用户不可读。Compose默认不启用模型。

## 用量与回退

默认全局每天40次、120000 Token；单员工每天10次、40000 Token，北京时间自然日。单次最多1024输出Token、16KB输入、12秒总网络期限；全局单通道。可调整项见`deploy/env.example`，服务启动时校验范围。

这些是本地保护值，不是平台免费额度、账户余额或最终费用。发送前按输入UTF-8字节数加安全余量和最大输出量保守预占；平台回传合法usage后按实际Token记账。超时、崩溃或缺usage时保留预占，不自动重试，不把未回传记为0。跨天的在途请求记入最初预占的日期。

同一问答/建议只生成一次，重复按钮或同一操作重试回读已有结果。异常结果不重新扣量调用；可重新提问创建新问答。未知在途结果超过60秒后标记结果未知，继续保留预占；原进程迟到完成时仍允许写回已知用量。此机制是单进程演示的保护，不是分布式计费系统。

模型失败、鉴权/余额/限流、JSON不合格、无效引用或资料不足时，保留原关键词摘录。网络失败时只展示固定错误码，不转发厂商正文、密钥或请求原文。数据库只额外保存生成内容、上下文指纹、状态与用量，不保存外发提示词副本或密钥。

模型前后与采纳时都检查账号、资料版本和访问权限；来源撤回/变更后隐藏生成回答或阻止采纳。已发给供应商的数据不能因后续撤回而收回，因此必须在发送前确认许可。引用编号校验不能证明每句内容真实，关键内容仍需人工核对。

## 验证与接口

- `GET /api/workbench/ai/status`：登录用户查看配置状态，不返回密钥或文件路径。
- `POST /api/workbench/messages/{id}/ai`：`{"consent":true}`，会话鉴权、CSRF、Origin、Idempotency-Key。
- `POST /api/workbench/artifacts/{id}/suggestions/{sid}/ai`：相同确认与保护，只能作者调用。
- `GET /api/workbench/admin`的`ai`：管理员看今日总量/限额/最近状态，不返回员工问答或生成正文。
- 采纳模型建议：原`.../suggestions/{sid}/apply`另传`generation_id`。未传仍采纳原摘录；无效或依据不足的模型结果不能采纳。

`configured/ready`仅表示可以尝试调用，不证明密钥有效、账户有余额、供应商可达或内容质量已验。真实接通必须用虚构资料成功调用后单独确认。单元测试使用假传输，不消耗真实额度。

2026-11-06核对的[官方首次调用](https://api-docs.deepseek.com/)、[Chat Completions](https://api-docs.deepseek.com/api/create-chat-completion)与[JSON Output](https://api-docs.deepseek.com/guides/json_mode)：默认`deepseek-flash`、HTTPS固定主机、JSON输出、关闭thinking、不启用工具。后续模型名称/供应商条款变化需重新核实。
