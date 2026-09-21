# Windows 安装包模板

`installer.nsi.in` 集中保存 NSIS 安装流程。`make_local_win_installer.py`
读取组件配置、发现 payload 和 hooks，然后选择模板片段、替换占位符，生成
`installer.nsi`。不需要额外安装模板库，也不改变 `build-pkg` 的命令参数。

## 阅读和修改

先看文件开头的 `INSTALLER` 主模板：包含页面顺序、运行环境检查、旧安装位置发现、
进程停止、初始化、安装段和卸载入口。后面的命名片段处理按组件重复或按条件启用的内容：

| 片段 | 用途 |
| --- | --- |
| `DIRECTORY_CONTROLS` / `BROWSE_CALLBACKS` | 每个组件的目录选择控件 |
| `COMPONENT_INIT` / `SERVICE_ROOT_FALLBACK` | 恢复历史安装目录，缺失时使用默认值 |
| `COMPONENT_SECTIONS` / `PAYLOAD_INSTALL` | 组件安装段及文件释放 |
| `SERVICE_PREREQUISITES` / `VCREDIST_CHECK_*` | 服务组件的 Docker、运行库和端口检查 |
| `VCREDIST_FUNCTIONS` | 内置 VC++ 运行库的提取和安装 |
| `SERVICE_INSTALL` / `BUNDLE_INSTALL` | 环境变量、服务脚本、快捷方式 |
| `HOOK` | 安装前后及卸载前 hook 调用和错误处理 |
| `UNINSTALL_COMPONENTS` / `SERVICE_UNINSTALL` | 组件卸载及数据删除确认 |

修改固定安装行为时直接修改模板中的 NSIS 代码，保留原生的反斜杠、引号、
`$变量`、`${宏}` 和 `$\r$\n`，不需要再做 Python 字符串转义。
修改组件选择、路径计算或附件发现条件时修改 Python。

## 模板格式

- `; @@template NAME@@` 独占一行，开始一个命名片段，直到下一个片段或文件末尾。
  标记本身不会写入生成的脚本；第一个片段之前可以写说明注释。
- `@@NAME@@` 是占位符，名称使用大写字母、数字和下划线。行内占位符替换为文本。
- 整行只有占位符时，必须从行首开始；替换会包含该行的换行符。
  填入的多行片段自行携带缩进和末尾换行；填入空字符串则移除整行。
- 替换只进行一次，不重新解释传入值中的 `$`、反斜杠或 `@@...@@`。
  路径等参数沿用 Python 中已有的 NSIS 转义规则。
- 模板缺失、片段缺失或重名、使用的占位符未提供参数时，生成立即报错。
  本格式只有命名片段和占位符，不在模板内执行 Python、循环或条件表达式。

最终脚本以 UTF-8 BOM 和 CRLF 写入
`BUCKYOS_BUILD_ROOT/win-installer/distbuild/installer.nsi`，随后交给 NSIS 编译。
CI 复制整个 `package-kit`，因此模板会随打包工具一起传到 Windows builder。

## 本地验证

在 `publish` 目录运行：

```sh
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -p 'test_*win*.py' -v
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -p 'test_nsis_templates.py' -v
```

这些测试验证生成结果、模板替换和错误提示；实际安装界面与系统集成仍需在 Windows 验证。
