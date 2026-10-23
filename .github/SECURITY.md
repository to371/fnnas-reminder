# 安全策略

## 报告漏洞

**请不要开公开 issue。** 用 GitHub 的 [私密漏洞报告](../../../security/advisories/new)（Security → Report a vulnerability），
或者直接给维护者发邮件。收到后会尽快确认。

请尽量带上：影响范围、复现步骤、版本号（`/api/health` 里的 `version`）、以及你的判断（是否需要登录/内网）。

## 部署前提（很重要）

这个应用按「**仅内网使用的自托管小工具**」设计：

- 默认 **免密**：打开端口的人就能改你的提醒。要收口就设环境变量 `ADMIN_TOKEN`，
  或把它放在 FN Connect / VPN / 反向代理的 Basic Auth 之后。
- **不要把 8388 直接映射到公网。**
- Webhook、钉钉 Secret、邮箱授权码以 SQLite 明文存放在本机（`reminder.db`），
  界面上做掩码回显但不上加密。请像对待 `.env` 一样对待这个库文件，不要提交、不要外发。
- 卸载应用时 `var/data` 目录默认保留，彻底删除需要手动清理。

## 已知边界

- 口令比较使用 `hmac.compare_digest`；写接口只接受 `application/json`（挡住最简单的 CSRF）；
  静态文件路径用 `realpath` 收口；请求体上限 256 KB。这些在 `tests/smoke_test.py` 里有回归检查。
- 应用**不做**用户体系、不做多租户隔离。一个实例 = 一份数据 = 一个信任域。
- 上游（钉钉 / SMTP 服务器）返回的错误原文会写进日志并显示在界面上，
  其中可能包含对端返回的提示信息，不包含你的密钥。
