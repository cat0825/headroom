## What / 改了什么


## Why / 为什么


## How was it tested? / 如何测试

<!-- OS and Python version / 操作系统和 Python 版本 -->

- [ ] `python -m unittest discover -s skills/headroom/tests -p "test_*.py" -v` (with `HEADROOM_DISABLE_DASHBOARD=1`)
- [ ] `node skills/headroom/tests/test_dashboard_mood.js`

## Checklist / 检查项

- [ ] Core code stays standard-library only; Windows / PowerShell still works / 核心代码只用标准库，Windows / PowerShell 仍可用
- [ ] No prompt text, history, ledgers, or credentials committed / 未提交提示词、历史、账本或凭据
- [ ] `README.md` and `README.zh-CN.md` updated together (if docs changed) / 两份 README 已同步（如涉及文档）
