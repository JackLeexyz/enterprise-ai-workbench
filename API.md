# 文库导入与智能协作 API 契约

本文件描述本批已实现接口。软件修订 0.12.0；完整富文本编辑器、完整工单与完整V1.0.0尚未完成，项目根V1_ACCEPTANCE.md保留全部差距。前缀 `/api/workbench`，写入用 POST 或下表指定的 PATCH；除文件上传使用 multipart 外，均用 JSON。每次返回 `X-Request-Id`；普通成功为 `{data,request_id}`，错误为 `{error:{code,message},request_id}`。新增按需模型接口/状态/安全限制见[AI_SETUP.md](AI_SETUP.md)，默认不启用模型。

## 会话与安全

- GET `/health`：不含私人内容的模式/版本存活信息；不代替数据库写入、模型、负载或备份验收。
- GET `/login-options`：公开的`demo/accounts/password_min_length/remember_seconds`配置，不含密码、散列、会话或自定义账号；默认accounts为空。明确服务器配置`WORKBENCH_DEMO_LOGIN=1`时，仅返回启用且角色仍匹配的writer/reviewer/admin三个既有演示身份及固定标签，不创建账号。响应no-store。
- POST `/session`：`{username,password,remember?:boolean}`。返回本人公开信息和 CSRF，随机会话存 HttpOnly/SameSite=Strict host-only Cookie，HTTPS 使用 `__Host-`/Secure。默认八小时；仅启用演示配置且remember=true时为七天，Cookie及数据库到期一致，不滚动续期。登录仍12次/五分钟/IP限制，remember不能代替密码或开启演示策略。浏览器自行管理密码自动填充，应用不缓存密码。
- GET `/session`；POST `/session/logout`：本人会话读取和注销。
- 其他接口必须有会话；写入另需准确 `Origin`、`X-CSRF-Token` 和约定 Content-Type。业务写入需 `Idempotency-Key`，8–100 位字母数字或 `_`/`-`；同用户同 key 绑定路由和请求体，重复成功不重复落库，不同内容返回 409。上传以文件名/服务端计算的SHA256/大小绑定内容，不绑定随机multipart边界。
- 普通写请求最大512 KiB；仅 POST `/imports` 放宽到2 MiB + 64 KiB封装，实际文件仍最多2 MiB。没有任意路径/URL抓取、脚本执行或任意 SQL 入口。调用者身份/权限只从服务端会话取得；后台账号接口的role是被管理账号的目标类型，不是调用者提权参数。

## 资料与发布

| 方法 | 路由 | 主要字段或返回 |
|---|---|---|
| GET | `/users` | 演示空间启用账号的 id/name/username/role，无密码或散列 |
| GET | `/sources?q=` | 先按权限过滤，再按标题/分类筛选，最多100份；含分节文本 |
| GET | `/sources/:id` | 当前有权查看的来源、不可变 version_id、元数据 version、sections；attachment 为文件元数据或null |
| GET | `/sources/:id/file` | 再检查来源权限；返回原件二进制，attachment下载，不开放静态文件路径 |
| POST | `/sources` | title≤120,text≤40000,category_id（默认cat_other）,label≤30；强制本人私有草稿 |
| POST | `/sources/:id/versions` | base_version=family_version，title/category_id/label；二选一text或已解析preview的import_id；仅本人最新版本，另存私有草稿 |
| PATCH | `/sources/:id/sharing` | base_version=family_version，scope/shared_user_ids；仅所有者；回读当前状态 |
| POST | `/sources/:id/publication-requests` | base_version,admin_id,scope,shared_user_ids |
| GET | `/admin/publications` | 仅分配给本管理员的待处理申请 |
| POST | `/admin/publications/:id/decision` | decision=approved/rejected；通过时发布并递增元数据version |
| POST | `/sources/:id/withdraw` | 管理员且资料本身可读，base_version一致；撤回并递增version |

scope 为 private/selected/internal。selected 要求至少一名有效共享账号；资料正文一经保存不可改写，0.7新增不可变来源版本链，规则见末节。资料管理员不是所有私有资料的超级读者；仅在作者指定的待发布申请期间拥有审阅授权。撤回后正文与原件仅所有者或新待发布申请中明确授权的管理员可读，问答/新引用/导出不得继续使用；不销毁原文或已下载的副本。

## 文件导入

| 方法 | 路由 | 字段与结果 |
|---|---|---|
| POST | `/imports` | multipart/form-data，仅一个名为file的文件；新版上传可加X-Source-Parent/X-Source-Version成对头，返回202与本人导入记录、duplicate标记 |
| GET | `/imports` | 本人最近20条状态与文件元数据，无文件bytes或正文sections |
| GET | `/imports/usage` | 本人所有保留记录的used_bytes/limit_bytes/remaining_bytes/retained_count/pending_count/pending_limit/remaining_slots，无其他用户或原件内容 |
| GET | `/imports/:id` | 仅上传者可读，含sections/warnings/characters/attempts/error_code/error_message/source_id |
| POST | `/imports/:id/confirm` | JSON title≤120,category_id（默认cat_other）,label≤30；仅preview可确认，返回source_id/duplicate，强制本人私有未发布草稿 |
| POST | `/imports/:id/retry` | JSON `{}`；仅failed/interrupted/canceled且attempts<3、当前pending<5，重排原任务，不重新上传 |
| POST | `/imports/:id/cancel` | JSON `{}`；仅上传者、未入库，返回当前job；运行中先canceling，其他可取消状态转canceled，保留原件 |
| POST | `/imports/:id/delete` | JSON confirm必须为true，file_sha256须匹配；仅上传者、未入库且非running/canceling；返回id/deleted/released_bytes |

支持DOCX/TXT/MD/文本PDF，单文件≤2 MiB，正文≤40,000字符、预览≤120段。TXT/MD为UTF-8或带BOM的UTF-16；DOCX只提取正文段落与顶层表格行，不承诺分页/版式/OCR。PDF最多100页，规则见末节。纯文本回传，客户端转义后展示。没有获取远程文件的URL参数。

```text
上传 -> queued -> running -> preview -> 人工确认 -> committed（产生私有资料草稿）
                     |           不确认则不创建资料、不参与检索
                     +-> failed -> 手动重试 -> queued
进程启动发现旧 running -> interrupted -> 手动重试 -> queued
queued/preview/failed/interrupted -> 取消 -> canceled（保留原件）
running -> 请求取消 -> canceling -> 后台结束丢弃结果 -> canceled
canceled -> 显式恢复并检查额度/次数 -> queued
未入库且非running/canceling -> 显式确认删除 -> 记录移除、回收应用额度
```

202只表示原件和任务已保存，不表示解析、入库或发布完成。后台同一时刻只解析一份文件；文件锁位于DB目录，另一进程不重复启动消费者。最多3次实际解析，启动时的中断恢复需要人工重试。

同一账号同SHA256返回已有job，不创建第二份原件；不同账号不共享命中信息。每账号原件总额≤20 MiB，queued/running/canceling/preview合计最多5条，超额409 IMPORT_QUOTA；恢复解析也检查当前队列上限。canceled仍计原件额度，但不计pending。确认一次后不同key重放仍返回相同source_id，不改写首次确认的标题/正文。

取消不强杀解析线程：canceling仍占单消费者与pending，不能恢复/删除；成功和失败回写均先检查取消状态，丢弃迟到结果。启动时旧canceling转canceled，旧running仍转interrupted。取消与入库、删除与入库在同一写事务边界互斥，不允许孤立已建立来源。已入库（含私有草稿）取消/删除均409 IMPORT_COMMITTED。

删除操作必须带明确confirm与当前job的SHA256，不能仅靠猜测ID。删除会移除当前库的原件BLOB/正文/记录，操作日志保留actor/action/对象ID/time；无恢复入口。同key重放返回同一删除结果，不重复回收；UI必须重新GET usage，不对缓存结果反复减法。用旧上传key重放已删除记录返回410 IMPORT_REMOVED，不隐式重建；用户显式新上传取得新ID。原件保留到明确操作，没有自动过期清理；回收额度不是数据库压缩、安全擦除、删除备份或撤回下载副本。

新增错误：422 DELETE_CONFIRM_REQUIRED；409 IMPORT_BUSY（解析/取消未确认）、IMPORT_CHANGED（文件校验不符）、IMPORT_COMMITTED；410 IMPORT_REMOVED。取消幂等重放回读当前状态，不重新取消后来已恢复的任务；删除后的取消请求可能404，不能据此恢复已删记录。

原件存SQLite，不能用导入ID构造无鉴权下载路径。只有已确认资料的 `/sources/:id/file` 可下载；响应为 application/octet-stream + Content-Disposition attachment/UTF-8文件名 + no-store/nosniff。没有原件、未知ID和无权访问均404。

解析拒绝宏/嵌入对象/加密ZIP、DOCTYPE/实体声明、异常路径、非标准压缩以及过大的ZIP展开内容。防御不是杀毒或进程沙箱；正式业务仍需文件扫描、独立解析进程和生命周期策略。典型错误：422 INVALID_UPLOAD/UNSUPPORTED_FILE/INVALID_DOCX/NO_TEXT/TEXT_ENCODING/TEXT_TOO_LARGE；413 FILE_TOO_LARGE；409 IMPORT_NOT_READY/IMPORT_RETRY_BLOCKED/IMPORT_QUOTA。异步解析错误写入job，不回显堆栈或主机路径。

## 文档与审阅

| 方法 | 路由 | 主要字段或返回 |
|---|---|---|
| GET | `/artifacts` | 本人文档或分配给本人的当前审核修订，最多100份 |
| POST | `/artifacts` | title,template_version_id,fields,notes,source_ids（0–8）；可只用已填字段起稿，不编造引用。兼容旧template=solution/delivery，但仍绑定当前模板版本 |
| GET | `/artifacts/:id` | 当前正文、citations、reference_details、reviews、comments、history、can_edit |
| GET | `/artifacts/:id/revisions/:n` | 有权查看的历史只读修订；状态historical、current_revision供对比 |
| POST | `/artifacts/:id/revisions` | title,base_revision,content_json,citations,fields可选（省略则继承）；成功新建r+1，不覆盖旧稿 |
| POST | `/artifacts/:id/reviews` | base_revision,reviewer_id（不能本人，需有全部引用权限） |
| GET | `/reviews` | 当前分配给本人且尚未失效的待审记录 |
| POST | `/reviews/:id/decision` | revision,decision=approved/rejected,note；退回必须有意见 |
| POST | `/artifacts/:id/comments` | text≤2000,base_revision；留言绑定具体修订 |
| POST | `/artifacts/:id/exports` | revision；返回导出记录id/status=ready，不表示下载完成 |
| GET | `/exports/:id/download` | 当前用户创建的导出；再次校验审批/当前修订/引用，返回DOCX二进制 |

结构化正文示例：

```json
{
  "base_revision": 1,
  "title": "交付说明",
  "content_json": {
    "schema_version": 1,
    "blocks": [{"id": "b_01", "type": "paragraph", "text": "待确认实施时间。"}]
  },
  "citations": [{"source_id": "delivery", "version_id": "ver_delivery"}]
}
```

schema_version=1的blocks 1–100个，type为heading/paragraph/bullet；id唯一且受限；单块≤12000、正文合计≤50000字。新schema_version=2见下节；引用最多20份，需已发布、当前可读、version_id匹配。

只有作者能编辑，审核人只读被分派的修订；不会仅凭审核分派获得新修订或未来留言。0.8另设独立稿件共享，授权当前及后续修订只读、历史及内部协作记录除外。正文修改使待审旧记录失效并回到草稿。base_revision不符返回409，不覆盖。导出需当前修订通过另一同事审核且调用者为作者/该修订指定审核人，普通共享读者不可导出；编辑、来源撤回或权限变化会阻止旧下载链接。引用标号移除后的正文由作者人工核对，本批不自动重写所有标号。

## 稿件独立只读共享 0.8.0

PATCH `/artifacts/:id/sharing`（API-24）：仅所有者，JSON如下，仍需会话、CSRF、精确Origin和Idempotency-Key。

```json
{"scope":"selected","shared_user_ids":["u_reviewer"],"base_version":1,"base_revision":1,"confirm_body":true}
```

scope仅private/selected/internal。selected必须1–20个有效已启用的其他同事，去重排序；非selected仍校验所传同事列表是否合法，但不存储接收者。base_version为稿件共享并发版本，base_revision为本次确认正文修订；两者必须为正整数，布尔值不接受。范围冲突409 VERSION_CONFLICT，正文变化409 REVISION_CONFLICT；不覆盖。非private必须confirm_body严格为true，否则422 BODY_CONFIRMATION_REQUIRED；收回private不要求披露确认。未知字段422 INVALID_INPUT，非法范围/账号422 INVALID_SCOPE。无权或不存在统一404，不因admin身份开放，已获只读权限但非作者修改范围403 OWNER_REQUIRED。

额外artifact_sharing表默认private/version=1，不改旧正文/修订/引用；范围更新版本递增、记最小审计事件，不触发正文修订或使审核失效。授权包括标题、文档信息、正文和**未来保存的新修订**；每次只读当前修订，猜历史URL不扩大访问。正文内已经摘录的文字会随稿件披露，来源后来撤权也无法自动撤回这些副本，作者必须明确确认。来源标题/原文/原件/检索独立鉴权；不可用来源只回传受限占位，不自动开放来源。

GET `/artifacts`按本人所有权、当前修订的审核分派或共享范围过滤后截取最多100条；新增access=owner/reviewer/shared、scope、is_shared，不返回他人接收者列表。GET稿件新增access/is_shared/can_edit/can_share/can_comment/can_export/sharing。sharing仅作者含shared_user_ids/version，其他读者只有scope；can_export同时检查当前审核及引用。普通shared读者不返回history/reviews/comments，不授予留言、修改、建议、审核或Word导出；已有旧审核授权仍可直接访问对应历史修订，但不会在共享当前视图枚举。当前审核人优先按reviewer能力显示，与is_shared独立。

共享不代替审核分派；收回共享不撤销已经分派的审阅权。普通共享收回后列表、详情、指定修订、检查接口立即在下次请求重验，返回no-store；页面重新读取404时清除旧正文并回到列表，不声称实时推送擦除。已复制/已下载/已读取内容无法远程收回。

成功返回当前可读稿件和sharing_result={applied_version,is_current}。幂等缓存只保存id/applied_version，重试重新鉴权回读当前范围，不把旧共享状态或旧正文重放；is_current=false表示该操作已执行但随后范围又被修改，客户端须展示当前事实，不能宣称重新授权。停用或角色变化后旧身份需重新登录。读权限与正文在同一SQLite只读快照中查询；写入并发版本在BEGIN IMMEDIATE内校验。

## 工作草稿与自动保存 0.10.0

仅稿件作者可访问，管理员/共享读者/指定审核人无额外授权。GET正式稿件仅对作者当前修订附加`draft_version`与`has_working_copy`，不混入草稿内容；历史和非作者不含这些字段。

| 方法 | 路由 | 输入与输出 |
|---|---|---|
| GET | `/artifacts/:id/draft` | id,draft_version（未建立为0）,base_revision,current_revision,has_draft,stale,updated_at,draft；有草稿时附reference_details，引用名称重新鉴权 |
| POST | `/artifacts/:id/draft` | title/content_json/citations/fields/base_revision/base_draft_version；成功仅返回id/draft_version/base_revision/updated_at，不在幂等结果缓存全文 |
| POST | `/artifacts/:id/draft/discard` | base_draft_version≥0整数、confirm严格true；返回id/draft_version/has_draft=false/is_current。旧确认重放只回读是否仍为该次清空，不删除后续新草稿 |
| POST | `/artifacts/:id/revisions` | 现有保存字段加base_draft_version；事务校验正式修订和草稿版本，追加新修订并清空草稿，草稿版本继续递增。旧客户端省略该字段仅在无未提交草稿时允许 |

`draft={title,content_json,citations,fields}`。正文限制、结构规范、模板字段与来源授权沿用保存接口；拒绝把正式稿或待提交工作草稿中的schema2降级成schema1。服务器工作草稿只有一份，不按每次按键增加历史。单独自动同步不更新artifacts的title/revision/status/updated_at，不改变审核、共享或导出；显式修订才使原待审稿失效。存在未提交草稿时送审、准备摘录和其他新修订操作需先明确保存或放弃。授权审核人可继续处理原正式修订，不读取作者草稿。

双重乐观并发保护：正式修订不一致为409 REVISION_CONFLICT；工作草稿版本或过时保存回执为409 DRAFT_CONFLICT。版本值拒绝布尔/负值/字符串（422 INVALID_DRAFT_VERSION）；缺少放弃确认422 CONFIRM_REQUIRED；旧入口遇未提交草稿409 DRAFT_PENDING；格式降级409 FORMAT_DOWNGRADE。写事务互斥，清空后不复用旧版本号。幂等重放先重验有效身份及作者身份；已被后续同步/清空/修订取代的旧保存不会复活内容。

网页schema1空闲1秒后提交，同一时刻单请求；回执后若有新修改再同步最新快照。网络/5xx/无法解析回执时保留冻结请求和key，手动重试先确认原请求，再发送新改动；确定性4xx可修正后另发新请求，但冲突/撤权不自动覆盖。刷新或重新进入仅在正式修订未变化、格式支持且草稿基准相符时恢复。跨两次读取出现修订竞态时暂停恢复；页面跳转读取失败时原队列保留。

没有浏览器全文持久缓存、后台定时离线同步或关闭页面后的持续保存。beforeunload提示仅为尽力保护；未确认同步的最后内容可能丢失。中文composition事件暂缓和错误状态已实现，不等同真实浏览器/IME/网络验收。schema2网页仍只读，本接口的schema2校验能力不等于完整富文本输入能力。

## 结构化文稿底座 0.9.0

沿用POST `/artifacts/:id/revisions`，content_json可显式提交schema_version=2；默认起稿仍为1，旧稿不自动升级，新格式使用新修订，历史不改。正文结构化支持不等于富文本输入组件已接入；当前网页对2只读展示、保护原结构，作者仍可共享/提交审核/导出已审版本，不能用旧表单保存、重排或插入文字。

```json
{
  "schema_version":2,
  "blocks":[
    {"id":"h1","type":"heading","level":1,"runs":[{"text":"交付说明"}]},
    {"id":"p1","type":"paragraph","runs":[{"text":"关键事项","marks":[{"type":"bold"},{"type":"italic"}]},{"text":"参考资料","marks":[{"type":"link","href":"https://example.com/docs"}]}]},
    {"id":"t1","type":"table","header":true,"rows":[[{"runs":[{"text":"材料"}]},{"runs":[{"text":"状态"}]}],[{"runs":[{"text":"培训记录"}]},{"runs":[{"text":"[待确认]"}]}]]}
  ]
}
```

服务端规范化后文字块/单元格均有text镜像，表格text用制表符/换行连接；镜像由runs/rows推导。客户端可省略text，若提供必须与结构完全一致，否则422 INVALID_CONTENT，不能用不同摘要绕过检查。标题level严格1/2/3；paragraph/bullet不接受level。marks为最多3个不重复的bold/italic/link，除link.href无额外属性，顺序规范化。文字不执行HTML，只作转义显示；无效XML控制字符、代理码点及U+FFFE/U+FFFF拒绝。

链接仅完整HTTP/HTTPS、有效主机/端口，不接受脚本/data/file/相对地址/账号口令/空白控制字符/反斜杠；最多2048字符，后端不访问目标。网页以新标签及noopener/noreferrer打开，静态预览去掉外部跳转；Word以外部hyperlink关系保存，不抓取网页。

表格1–20行、1–8列、每行同宽，每文档最多200单元格；无合并/嵌套/公式/任意样式。每文字段或单元格最多200个runs，每文档最多2000个输入runs；文字块或表格text≤12000字、全篇≤50000字、最多100块，HTTP JSON仍≤512KiB。未知节点/属性、重复ID及越界一律拒绝，不静默丢字段。图片/有序或嵌套列表/完整编辑操作仍未接入。

原修订为2时，用1保存返回409 FORMAT_DOWNGRADE，不能扁平化覆盖。服务端受控摘录插入创建带runs的新段落；replace不允许标题或表格，允许文字段时明确返回target_format_reset=true，整段样式/链接重置为新摘录，不影响其他块。预览/采纳/撤销继续重验完整目标指纹、修订/来源/ACL并记录新修订，撤销恢复完整结构。表格text也参与待确认检查与审核长度检查；格式改变需要新审核。

Word导出支持上述标题/段落/项目符号、粗斜体、链接和矩形表格/重复表头；所有权、当前审核、引用及下载实时校验不变。新样稿仅XML/结构/关系自动检查，未渲染目检、未验证分页/字体/Word实机，不声称完整版式兼容。无新增表，实际库不迁移；禁止旧管理端并行写新格式。

## 模板、文档信息与采纳 0.5.0

| 方法 | 路由 | 主要字段或返回 |
|---|---|---|
| GET | `/templates` | 当前启用模板，id/template_id/number/name/fields/sections/active |
| GET | `/templates/versions/:id` | 指定不可变定义；已认证空间内可读，无他人稿件内容 |
| GET | `/admin/templates` | 管理员可读含停用模板及版本历史摘要 |
| POST | `/admin/templates/:template_id/versions` | base_version,name,active,fields,sections；生成新id/number，不改旧定义 |
| GET | `/artifacts/:id/checks` | 已授权的当前已保存稿件缺项/待确认块/不可用引用；历史修订检查由其快照计算 |
| POST | `/artifacts/:id/suggestions` | base_revision,target_block_id,source_id,section_index（从0起）,operation=insert_after/replace；只保存建议指针，返回prepared预览 |
| GET | `/artifacts/:id/suggestions/:sid` | 仅作者；重新校验版本/目标/来源/权限/时效后返回excerpt和位置，不用旧缓存绕过撤回 |
| POST | `/artifacts/:id/suggestions/:sid/apply` | base_revision；同事务重验并保存新修订，重复同建议不再次插入 |
| POST | `/artifacts/:id/suggestions/:sid/cancel` | `{}`；取消未采纳建议，不改稿件；不是撤销已采纳 |
| POST | `/artifacts/:id/suggestions/:sid/undo` | base_revision；仅最近且未再改动的采纳可撤销，保存新修订而非删除历史 |

模板只维护solution/delivery两个固定标识。fields为1–12项`{key,label,required,multiline}`，key为1–32位小写字母开头、字母数字下划线，label≤30，布尔字段严格校验；sections为1–8项`{key,title,field_keys}`，key不重复，title≤60，只能引用已定义字段，每字段至少分配一次。name≤60，active为布尔值。定义均为文本和字段映射，不支持代码、任意HTML、Jinja或执行表达式。基于同一版本并发修改只有一个成功，其余409 TEMPLATE_CONFLICT。

创建时只能选择当前启用版本；旧/停用版本409 TEMPLATE_UNAVAILABLE，但已有稿件仍绑定原定义。每修订fields是相应模板key到文本的对象，单值≤1000字，拒绝未知key和非文本。允许保存缺项草稿，缺失值起稿为`[待确认]`，不编造报价/日期；缺必填信息提交审核422 REQUIRED_FIELDS。可选字段的待确认正文为人工检查警示，不等于不可审核。编辑文档信息不自动覆盖手工正文；字段与正文分别保存到同一修订。省略fields的旧客户端保存继承上次值，不清空。历史旧稿不自动猜测模板字段，template_binding为null、checks.legacy_template为true，原正文不变。

GET稿件新增template_binding、fields、checks、undo_suggestion。fields随指定修订返回；检查包含missing_fields/unconfirmed_blocks/reference_issues/can_submit_review，不是事实或合规正确性认证。字段变化同样形成新修订并使待审旧任务失效。

建议为offline_excerpt，不是LLM生成。服务端保存原文版本、章节索引/全文指纹、目标块/指纹和600秒有效期，不复制一份可越权回读的来源全文。预览不改正文；replace不允许替换标题。采纳前必须匹配原始base_revision、目标指纹及最新来源/ACL，事务内修改正文与引用、增加修订、失效旧待审。网页另比对本页快照，未保存修改或迟到结果不覆盖当前文档。截取来源最多1200字；来源仍须完整指纹一致。

取消返回canceled；过期409 SUGGESTION_EXPIRED，取消后409 SUGGESTION_CANCELED，正文/目标/来源或状态变化409 SUGGESTION_CHANGED，来源无权或撤回404。apply幂等返回不含旧摘录的最小结果；create重放通过GET重验而不是回显旧内容。撤销仅在当前修订等于该次applied_revision时允许，恢复之前的正文/字段/引用并再次检查引用权限；产生新修订，后续修改不可被撤销覆盖。采纳或撤销后的版本均须重新审核导出。

## 助手与任务

| 方法 | 路由 | 主要字段或返回 |
|---|---|---|
| GET | `/messages` | 本人最近20条，读取时重新校验来源可见与发布状态 |
| POST | `/messages` | question≤500,scope=all或source_id；返回摘录与locator，不是LLM答案 |
| GET | `/messages/:id/evidence/:evidence_id` | 仅问答所有者；再次校验发布/ACL/来源版本/章节索引/全文指纹，返回精确section原文及定位元数据 |
| POST | `/messages/:id/feedback` | rating=helpful/unhelpful，reason，note≤500（可省略）；每条本人问答仅一次，返回id/status/version/answer_type |
| GET | `/admin/feedback` | 管理员：最近100条反馈运行元数据，未关闭优先；不含问题/原文/来源名/来源ID/message_id/反馈人身份/个人备注 |
| POST | `/admin/feedback/:id/status` | base_version，status=in_progress/closed，resolution≤500；关闭时必填处理备注，反馈人可回读 |
| POST | `/tasks/preview` | title,note,assignee_id,due_date；15分钟有效，尚不创建任务 |
| POST | `/task-previews/:id/confirm` | 仅创建者确认，同一预览不会创建重复任务 |
| GET | `/tasks` | 仅本人创建或负责，最多100项 |
| POST | `/tasks/:id/status` | base_version,status=in_progress/completed/canceled |
| GET | `/admin` | 管理员：公开账号、离线模式与不含正文/对象id的审计事件 |

只有负责人可处理或完成，创建者可取消，已完成/取消是终态；版本冲突409。任务是本系统待办，不发送邮件、写外部系统或执行设备动作。问答中的文字指令按资料内容展示，不作为工具授权。幂等重放问答也重新过滤来源，不能从旧缓存绕过撤回。

## 引用定位与反馈 0.4.0

新问答的来源指针记录source_id/version_id/section_index/section_hash/locator，不复制全文；section_hash为章节标题与完整文本的规范JSON SHA256。evidence_id由本条问答ID、原始引用槽位和指针计算，过滤掉其他引用后仍保持不变，不是当前显示序号，也不是访问令牌。GET证据必须绑定本人问答且指针可用。返回section.title/text为整节原文；列表excerpt最多700字符，不伪造PDF页码或精确坐标。

GET `/messages`的每条记录新增unavailable_count和feedback。matches含evidence_id/source_id/version_id/title/label/category/locator/section_index/excerpt；已撤回、权限变更、版本不匹配、章节位置或全文变化都不回传摘录。旧版仅有文字locator而无指纹的历史记录保留问题，但不从当前文本反向补造证据，须重新查询。未知、他人或失效引用统一404，失败不回传旧摘录。

reason枚举：useful（有帮助）、irrelevant（不相关）、missing（缺依据）、outdated（过时）、locator（定位）、other；helpful只接受useful，unhelpful不接受useful。个人note仅本人在messages.feedback中可见，后台接口始终不返回；即使管理员有部分来源权限，也不因此获取整条私人问题。V1不提供反馈正文授权共享。后台只按评价/类别/当时结果类型/状态跟进，不表示已获得复现上下文。

feedback后台返回id/rating/reason/answer_type/status/resolution/version/created_at/updated_at；answer_type为提交反馈时重新校验所得的cited_excerpts/no_evidence，非原始永久快照。pending可转in_progress或closed，in_progress只可转closed；closed终态不可重新打开或改写。每次处理版本递增，冲突409 VERSION_CONFLICT，须重新读取。重复评价不同key返回409 FEEDBACK_EXISTS；相同成功key不重复创建。员工不可修改已提交评价，可刷新处理备注。

提交/关闭反馈均不触发模型调用、训练、自动调参或奖励惩罚。离线检索只做大小写归一、字符二元组/英文数字词匹配、命中比阈值0.10及top5排序；命中数不是置信度，相关摘录不一定回答问题。`evaluate_retrieval.py`只在独立临时虚构库评测，输出数据版本/哈希、逐例结果、失败和权限检查，检索质量为信息性结果；权限泄漏导致命令失败。不对外提供读任意库或上传评测资料的接口。

常见错误：401会话失效；403来源/CSRF/角色；404不存在或无权限；409修订/状态/幂等冲突；413请求过大；415格式；422字段/来源校验；429登录限流。500不回显SQL、路径、密码或堆栈；请求编号可用于定位。

## 账号与分类 0.6.0

| 方法 | 路由 | 字段与结果 |
|---|---|---|
| GET | `/admin/users` | 仅有效管理员；全部账号id/username/name/role/active/version/updated_at，不含密码或哈希 |
| POST | `/admin/users` | username,name,role=employee/admin,password；创建启用账号，返回安全账号元数据 |
| PATCH | `/admin/users/:id` | base_version；可改name/role/active，active必须为布尔值，不允许改username/口令或硬删账号 |
| POST | `/admin/users/:id/password` | base_version,password；重置口令且撤销全部会话，不自动启用停用账号 |
| GET | `/categories` | 已认证员工可读启用目录，默认其他排首；id/name/active/version/updated_at，无私有资料计数 |
| GET | `/admin/categories` | 仅有效管理员；含停用分类，不含资料正文/标题/数量 |
| POST | `/admin/categories` | name≤30；创建启用分类，稳定新ID |
| PATCH | `/admin/categories/:id` | base_version,name?,active?；改名/启停，不硬删，不接受分类树 |

账号名为3–40位小写字母开头、字母数字或`._-`；姓名1–60字；新密码默认20–200字符，服务器明确启用演示配置时6–200字符，非全空白、不含控制字符。浏览器用两次密码输入校对，密码不置入HTML值、URL、localStorage或通用请求缓存键。账号版本独立于文档版本，任何管理修改/重置递增；旧base_version返回409 VERSION_CONFLICT。无变化422 NO_CHANGES；非法输入422 INVALID_ACCOUNT；重名409 ACCOUNT_EXISTS。

创建/重置密码的幂等请求将非秘密参数按通用规则绑定；口令另用带随机盐的PBKDF2摘要验证重放内容，不保存明文或快速密码指纹，不在成功/错误响应、审计事件、幂等结果中回传哈希。不同密码复用同key返回409 IDEMPOTENCY_MISMATCH。凭据摘要仅在受保护数据库的credential_intents中，不是运营日志。密码由管理员通过独立安全渠道交付，本功能不发邮件/消息、不生成公开重置链接。初始化口令文件不会随后台重置更新；公网交付必须先完成HTTPS，不能将受限环境的内存HTTP解析测试当作网络/TLS验证。

停用或角色变化会删除该账号全部sessions；重置密码同样撤销。再次启用不复活旧token。更新最后一个启用管理员为停用/员工返回409 LAST_ADMIN，计数与修改在同一BEGIN IMMEDIATE写事务内；禁用管理员不计入保底数量。写操作在事务边界重验调用者的enabled/role，防旧身份重放。管理员读取接口同样重验当前有效角色。

停用/降为员工将分配给该账号的pending资料发布申请置superseded，撤销临时原文审阅权，所有者可重新分派；不删除资料、历史修订、审核记录或历史任务。待办确认会再次检查负责人启用状态，旧预览不能给刚停用账号新派任务。自我更改角色/停用或重置时，返回reauthenticate=true并清除当前Cookie；网页退出受保护视图，重新登录。其他账号操作返回false或省略。失效会话可保留内存稿尝试同账号重登，也可明确放弃本页返回登录。

分类按ID绑定来源，名称不区分ASCII大小写；重复名409 CATEGORY_EXISTS，版本冲突409 VERSION_CONFLICT。默认cat_other/其他不能改名或停用（409 DEFAULT_CATEGORY）。停用分类不能用于新文本/导入确认（409 CATEGORY_UNAVAILABLE），已入库来源和历史证据不失效。分类更新不改变来源version_id、正文指纹、元数据version或历史引用；列表返回category_id、当前category名称及category_active。兼容旧客户端category名称，仅匹配当前启用分类，不按任意文本新建目录。

升级只增加account_versions/credential_intents/categories/source_categories及已知默认分类绑定；不改原users/sources/revisions行。旧任意个人分类不自动公开为全员目录，只在有权读取的来源legacy_category中保留，目录归入其他。重启不重新创建被改名/停用的默认分类。无分类/账号DELETE接口；管理员身份不开放员工私有文档、问答或反馈备注。

## 资料版本、独立共享与文本 PDF 0.7.0

来源有三个不同概念：`version_id`为不可变内容身份，`version`为该版本元数据的并发计数；`family_id`关联同一资料的内容版本，`version_number`为1–50的顺序号，`family_version`为整条链的并发计数。新版本和共享请求的base_version使用family_version，原发布申请/撤回仍使用该行version，勿混用。家族ID不是访问令牌。

旧库通过额外source_families/source_versions/publication_context表初始化单版本链，并增建import_targets保存新版上传目标；保留旧source_id/version_id/sections与稿件引用，不改写旧行。每条链只有一个current_source_id发布指针；历史行仍可为published，但is_current_published=false。GET sources按用户可见版本折叠为每资料一项；本人私有新版不会向其他人暴露标题/ID或历史条数。若列表项是新版草稿且旧版仍在检索，published_source返回**已授权**当前发布版；无权不返回。

新版文件上传可在原单文件multipart接口成对发送`X-Source-Parent=source_id`、`X-Source-Version=family_version`。上传事务先验所有者、最新链头和并发版本，保存到import_targets；两个头缺一、非法数值或超长数值422 INVALID_INPUT。本人GET imports/详情含parent_source_id/base_family_version（普通上传为null），重启或刷新后可恢复。目标在等待期间变化时，网页不自动刷新并发基线而是要求明确重新选择；确认仍须POST versions带当前明确同意的base_version。相同原件命中已有job时保留首个目标，不按新上传意图静默改绑；用户可在未入库确认表单显式重选自己的有效目标。取消保留目标与原件，显式删除未入库job级联删除其目标映射，不影响来源或其他记录；已入库文件保护不变。

GET sources/:id新增family_id/family_version/version_number/is_current_published/versions/can_add_version/can_share。versions仅为本人可读版本的摘要，不枚举隐藏草稿。只能从本人最新版本追加，非最新409 LATEST_VERSION_REQUIRED，并发或共享变化409 VERSION_CONFLICT；每链最多50版（409 VERSION_LIMIT）。正文和原件另存、不覆盖；新版默认private/draft，不继承旧共享，也不自动发布。文本入口只复制纯文本，不继承PDF位置或文件。文件入口先沿原上传/解析/预览流程，人工确认时POST versions并带import_id；未解析409 IMPORT_NOT_READY。已经绑定其他资料的相同文件不能重新挂到此链（409 IMPORT_COMMITTED），不复制原件绕过本人去重。旧的confirm仍用于新资料，也兼容parent_source_id/base_version；网页使用专门版本接口。

共享未发布草稿仅调整此版本，不使其参与检索。共享已发布版本则同步本资料**全部历史发布版**范围，未发布/撤回版本不被顺带开放。发布新版时也将申请范围同步到全部历史发布版，防收紧新版却从旧链接读取。范围变化或新版本会使此链pending发布申请superseded、撤销临时审阅权；需重新申请。申请记录保存family_version，批准同时重验链状态和共享账号启用状态。批准切换指针，不自动撤销旧内容身份。撤回当前版本将指针置空，不自动退回旧版检索。

新问答和新起稿/摘录只能选当前已发布授权版本；旧问答和已有稿件仍可读取/导出其精确历史发布版，前提是该版本未撤回、仍有权限且全文指纹一致。历史证据返回historical=true；不替换成新版。共享收紧后旧证据/文件/已生成导出下载均重新鉴权并阻断；已下载或已复制进稿件的文本无法远程抹除。准备中的摘录在发布指针变化后不可继续采纳，需重新预览。

文本PDF使用pypdf 6.10.0，独立子进程、15秒墙钟超时、10秒CPU上限；Linux另设512MiB地址空间限制，Mac不声称同等内存限制。每页解码内容≤6MiB、合计≤12MiB，库自身也有流解码保护；不是完整恶意文件沙箱或杀毒。单消费者保持不变，取消仍等待子进程结束/超时后丢弃结果。DOCX/TXT仍为原解析线程，尚未全部进程隔离。

PDF拒绝加密、损坏、超过100页、交互动作/表单/脚本/嵌入附件，以及页级动作/附件；外链不访问。全篇无文字422 PDF_NO_TEXT，不做OCR；混合空白/扫描页列出页号警告且不生成对应片段。每页最多3500字一片，sections附page_number/fragment_number，locator为`第 2 页 / 片段 1`；这是真实物理页序号，不是印刷页码、精确坐标、表格语义或视觉高亮。全文哈希包括位置元数据。UI提供提取文字预览和授权下载原PDF，尚无内嵌原件阅读器。

## 本人会话与规则追问 0.11.0

仅本人可用，管理员无额外访问权。归属取自当前有效账号；不存在与无权统一404 NOT_FOUND。写入沿用Origin/CSRF/Idempotency-Key约束。

| 方法 | 路径 | 请求与返回 |
|---|---|---|
| POST | `/conversations`（API-34） | `{title?}`，默认新会话，1–80字；返回本人会话摘要 |
| GET | `/conversations` | 本人最多50条，按updated_at/创建顺序倒序，含归档 |
| GET | `/conversations/:id` | 摘要：id/title/status/version/message_count/state/last_message_id/时间 |
| PATCH | `/conversations/:id` | `{base_version,title?,status?}`，至少修改一项；status=active/archived；摘要加change_result |
| GET | `/conversations/:id/messages?cursor=6`（API-35） | 默认最新20条，页内正序；cursor为排他sequence上界；返回conversation/messages/next_cursor |
| POST | `/conversations/:id/messages`（API-36） | `{text,scope?,base_version,reply_to?}`；text≤500字，scope默认all或当前可读已发布source_id；返回conversation/message |

每人最多50会话，含归档（409 CONVERSATION_LIMIT）；每会话100条问答（409 MESSAGE_LIMIT）。归档不删除、不回收额度；无自动清理或删除。message_count为问答轮数，每条同时含用户问题和助手结果。version为正整数，创建为1，每次消息/名称/归档变更递增；布尔等非法值422 INVALID_INPUT。过时基线409 CONVERSATION_CONFLICT，不覆盖；归档写消息409 CONVERSATION_ARCHIVED。

state仅ready/waiting_for_info/archived，是当前同步离线状态，不是后台Agent运行状态。reply_type为evidence_excerpt/needs_info/no_evidence。忽略空白和基本标点后匹配“你好、请问、怎么办、怎么做、怎么操作、如何操作、这个、那个、这个怎么做、这是什么、帮我看看、帮我查一下、怎么弄”，返回needs_info和clarification_prompt，无引用/任务/模型调用；其余走既有关键词检索。不是通用语义意图识别。

reply_to只能指向同会话最新一条needs_info；前条question与本条text拼接做本次检索，消息仍保存本条原问题及clarification_for。跨会话/旧目标409 CLARIFICATION_CHANGED；格式错误422 INVALID_INPUT。省略即独立新问题，不携带历史，无跨会话记忆，不累积全历史。artifact_id/revision等尚不支持的上下文与未知字段明确422拒绝，不静默忽略。

结果沿用matches/unavailable_count/feedback，附conversation_id/sequence/reply_type/clarification_for/clarification_prompt/scope。每次历史读取与成功幂等重试重新解析来源指针，撤回/收紧后不返回旧摘录；无可用引用的旧结果转no_evidence。needs_info不能提交评价（409 FEEDBACK_NOT_APPLICABLE）；其他反馈权限和后台最小元数据规则不变。

cursor为1–6位正整数且不大于当前message_count+1，非法422 INVALID_CURSOR；next_cursor为当前页最早sequence或null。读取同一事务快照，分页不跨会话。旧GET/POST `/messages`保留兼容，列表只展示未归组记录；不搬迁旧历史，网页不再用旧POST创建新问答。

成功写消息的幂等记录仅存message_id；重试回读当前会话/证据，不追加第二条。PATCH重放回读摘要，change_result含applied_version/is_current，不重新修改后续已变化的状态。网页未知发送结果冻结原请求/key，仅人工重试；已确认写入而列表读取失败只提供GET刷新。切换/分页用代次隔离，迟到响应不切回旧会话。发送框按会话在本页内存保留，刷新/崩溃可能丢失；不存localStorage/IndexedDB。

错误码：PDF_ENCRYPTED/INVALID_PDF/UNSAFE_PDF/PDF_NO_TEXT/PDF_PAGE_LIMIT/PDF_TIMEOUT/PDF_RESOURCE_LIMIT，异步保存到本人job，不将解析器stderr/路径回传。限制均为保护值，尚无2核4G负载容量验收；真实上传、浏览器、Docker/Linux资源保护仍须单独验证。
