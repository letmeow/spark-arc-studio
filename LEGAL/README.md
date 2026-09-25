# SparkArc 法律与运营声明目录

本目录用于存放 SparkArc 仓库级的中文法律与运营声明，目标是同时服务于以下场景：

- 为仓库维护者提供可公开引用的法律与运营说明
- 为官方实例提供可直接展示或复用的站内文档
- 为第三方部署者提供最小合规参考与责任边界说明
- 为主体识别、内容治理、侵权处理与证据管理提供统一入口

重要说明：

- 本目录内容仅作为项目文档与风控参考，不构成正式法律意见。
- 第三方部署者在对公众提供服务前，应结合自身主体、部署地、用户地区、所接入模型与具体业务形态自行补充、修改并承担责任。
- 若本目录文件与第三方实例自定条款冲突，以该第三方实例自行公示并实际执行的文件为准；但该第三方实例不得据此冒充 SparkArc 官方实例。

阅读顺序：

1. `../LICENSE` 与 `../server/llm/agen_matchbox/LICENSE`
2. `LicensePolicy.zh-CN.md`
3. `TrademarkPolicy.zh-CN.md`
4. `OfficialInstancePolicy.zh-CN.md`
5. `ThirdPartyOperatorNotice.zh-CN.md`
6. `TermsOfService.zh-CN.md`
7. `PrivacyPolicy.zh-CN.md`
8. `ContentPolicy.zh-CN.md`
9. `EvidenceAndIPCompliance.zh-CN.md`

文件用途说明：

- `../LICENSE` 与 `../server/llm/agen_matchbox/LICENSE`
  根项目 AGPL-3.0-only 与火柴 Agent 网关 Apache-2.0 的授权原文。
- `LicensePolicy.zh-CN.md`
  SparkArc 的 AGPL-3.0-only 社区许可立场、自部署友好说明、官方实例自营与第三方默认无商业豁免边界。
- `TrademarkPolicy.zh-CN.md`
  SparkArc 名称、Logo、品牌视觉、官方实例身份和第三方描述性使用规则。
- `TermsOfService.zh-CN.md`
  当前实例对注册用户的服务条款、使用规则与免责声明（中文版，含中国大陆法律依据）。
- `TermsOfService.en-US.md`
  英文版服务条款，已去除中国特有法律条款，做本土化适配。
- `TermsOfService.ja-JP.md`
  日本語版利用規約，中国特有の法的条項を削除し、ローカライズ済み。
- `TermsOfService.ko-KR.md`
  한국어판 이용약관, 중국 특유의 법적 조항을 제거하고 현지화했습니다.
- `PrivacyPolicy.zh-CN.md`
  当前实例对个人信息处理、日志留存、模型转发与用户权利的说明。
- `OfficialInstancePolicy.zh-CN.md`
  用于区分官方实例与第三方实例，降低主体混淆风险。
- `ThirdPartyOperatorNotice.zh-CN.md`
  用于明确第三方站长独立运营、独立负责的边界。
- `ContentPolicy.zh-CN.md`
  用于明确禁止内容、投诉举报、侵权通知、下架与封禁规则。
- `EvidenceAndIPCompliance.zh-CN.md`
  用于说明贡献者版权、来源证明、电子证据固定与知识产权争议处理建议。

维护说明：

- 对外提供服务的实例，应在登录页、页脚、帮助页或设置页显著位置链接本目录中的核心文件。
- `server/core/routes_tos.py` 当前支持按 `?lang=` 参数返回对应语言版本的服务条款。优先读取 `LEGAL/TermsOfService.{lang}.md`，回退到 `server/data/TermsOfService.md`，最终兜底 `LEGAL/TermsOfService.zh-CN.md`。
- 如后续新增官方域名、商标、软件著作权登记号、投诉邮箱、备案号，应优先更新本目录，再同步到页面。
- 如后续调整许可证、引入 CLA/DCO、开放商业豁免或新增官方实例，应同步更新 `../LICENSE`、`LicensePolicy.zh-CN.md`、`TrademarkPolicy.zh-CN.md`、`OfficialInstancePolicy.zh-CN.md` 与 README 四语版本。
