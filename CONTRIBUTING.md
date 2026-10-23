# 贡献指南

Issue 和 PR 都欢迎。这个项目小，规则也少，但下面四条是硬的——CI 会挡。

## 开发环境

```bash
git clone https://github.com/your-name/fnnas-reminder.git
cd fnnas-reminder
python app/server.py        # Python 3.12+，零依赖，不需要 pip install
```

改完跑两个测试（提交前请确保都绿）：

```bash
python -m unittest discover -s tests -p "test_*.py"   # 单测
python tests/smoke_test.py                            # 端到端，真起服务，约 40 秒
```

## 四条硬性约束

1. **不引入第三方依赖。** 只用 Python 标准库——这是能塞进 fnOS `python312` 直跑、不需要 pip 的前提。
   CI 里有一项 AST 体检会扫 `app/` 下所有 import。
2. **时间只用 `clock.py` 的入口。** `clock.now()`（已绑 `Asia/Shanghai`）、`clock.seconds_late()`
   （内部 `total_seconds()`）。直接用 `datetime.now()` 或 `timedelta.seconds` 会分别带来时区错 8 小时、
   超过一天的时长被截断两个 bug，`tests/test_clock.py` 有源码扫描守卫。
3. **前端不用原生 `confirm()` / `prompt()`。** 飞牛应用中心用 iframe 嵌页面，浏览器会拦原生弹窗
   （恒返回 `false` / `null`），表现就是「点了没反应」。用页内 `uiConfirm()` / `uiPrompt()`，
   `tests/test_frontend.py` 会扫源码。
4. **改行为要带测试。** 修 bug 就补一条能复现它的用例；改规则引擎请补 `tests/test_rules.py`。

## 提 PR

- 一个 PR 只做一件事，顺带把 README / `docs/DESIGN.md` 里过期的描述一起改掉
- 界面改动请附截图（深浅两色不必，但要有真实数据）
- 不要提交构建产物：`fpk-src/app/`、`*.fpk`、`*.db` 都已在 `.gitignore` 里
- 目录里的 `cmd/*` 必须保持 **LF** 换行（`.gitattributes` 已钉住），否则装到 NAS 上会 `bad interpreter`

## 报 Bug

请带上：飞牛版本与机型、应用版本（`/api/health` 返回的 `version`）、复现步骤、
以及 `app.log` 里相关的一小段。**不要贴 Webhook、钉钉 Secret、邮箱授权码原文**。

安全问题请走 [私密漏洞报告](.github/SECURITY.md)，不要开公开 issue。
