# buddy-picker

A small CLI for rolling a new Claude Code buddy profile. It generates candidate `userID` values to preview your buddy, and writes the chosen one directly to `~/.claude.json`.

## 使用方法

**环境要求:**
- Python 3.10+
- Claude Code 已安装并至少启动过一次

```bash
git clone https://github.com/MiaoSiLa/buddy-picker.git
cd buddy-picker
```

**1. 交互模式（默认）**

生成 10 个随机 buddy 供选择：
```bash
python buddy_picker.py
```

生成 50 个随机 buddy 供选择：
```bash
python buddy_picker.py --count 50
```

**2. 自动抽卡模式**

持续抽取直到命中指定稀有度（1: common, 2: uncommon, 3: rare, 4: epic, 5: legendary）。

抽到 legendary（等级 5）：
```bash
python buddy_picker.py --rare 5
```

抽 legendary dragon：
```bash
python buddy_picker.py --rare 5 --species dragon
```

抽 shiny legendary dragon（可能需要较长时间）：
```bash
python buddy_picker.py --rare 5 --shiny --species dragon --max-attempts 100000
```

## 注意事项

- **重启 Claude Code**：写入新 `userID` 后需完全重启 Claude Code 才能生效。
- **OAuth 用户**：使用 OAuth 登录时，修改 `userID` 可能无效，因为 Claude Code 会从 `accountUuid` 派生 buddy。工具会给出警告。如需确定性重置，请在 Claude Code 中 `/logout` 并使用 API key 模式。
- **数据安全**：本工具仅修改 `~/.claude.json` 中的 `userID` 并清除 `companion` 字段，项目历史和权限完全不受影响。
