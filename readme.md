# Desktop Packaging

当前仓库只保留本地桌面安装包脚本：

- `make_local_osx_pkg.py`
- `make_local_win_installer.py`
- `make_local_deb.py`
- `make_local_rpm.py`

这些脚本共享同一个配置源：

- `src/bucky_project.yaml`

统一入口 `make_local_pkg.py` 会先把它转换成一份中间 manifest JSON，
平台脚本优先消费这份 manifest；只有直接单独调用平台脚本且未传 `--manifest`
时，才会回退读取 `bucky_project.yaml`。

manifest 会固化：

- `apps.buckyos.*`
- `publish.macos_pkg.apps.*`
- `publish.win_pkg.apps.*`
- `publish.*_pkg.apps.*.deps.*`
- 如本地存在 `../cyfs-gateway/src/bucky_project.{yaml,yml,json}`，会把其中 `cyfs-gateway` app 的安装项并入 `buckyos`

然后把目录布局、`data_paths`、`clean_paths`、组件列表和默认安装目标固化进最终安装包与安装脚本。安装时不会再回仓库实时读取 `bucky_project.yaml`。
打包和本地安装时会过滤 `.DS_Store`、`__pycache__` 这类无关文件/目录。

## Common Flow

### 准备 BUCKYOS_BUILD_ROOT
0. 注意依赖，buckyos的local安装包依赖cyfs-gateway和BuckyOSApp,src目录结构固定。安装脚本不会去做git的任何操作
1. `cyfs-gateway` 是 `publish.*_pkg.apps.buckyos.deps` 里的打包依赖，不属于 `apps.buckyos.modules`，因此开发环境执行 devkit install 时不会触碰已有 gateway。
2. `deps.*.source` 按 `bucky_project.yaml` 所在目录解析；`cyfs-gateway` 当前配置为 `../../cyfs-gateway/src`。
3. prepare 阶段会对 `type: buckyos_project` 的 dep 在 `source` 目录执行 `buckyos-build` / `buckyos-install --app=<dep-name>`，安装到对应组件的 staging 目录；`--skip-prepare` 时仍要求 `source` 存在，用于生成打包 manifest。
4. BuckyOSApp 使用 `pnpm run tauri build` 构建，构建目录在 `$RUST_BUILD/release/bundle/...`
5. 在本仓库内用 `uv run ./src/buckyos-build.py` 和 `uv run buckyos-install` 准备好 `BUCKYOS_BUILD_ROOT` 下的发布目录。
6. 在 `src/` 目录运行 `deno task make_config release --rootfs <staged_rootfs>` 生成发布配置。
### 制作安装包
7. 调用对应平台的 `make_local_*` 脚本执行 `build-pkg`。
8. 如需校验，调用对应脚本的 `verify-pkg`。
9. 版本号规则是 src/VERSION下的内容作为主版本，然后 +buildYYMMDD

默认 staging 根目录由 `BUCKYOS_BUILD_ROOT` 决定：

- macOS / Linux 默认 `/opt/buckyosci`
- Windows 默认 `C:\opt\buckyosci`

## macOS Runtime 与安装流程

macOS 上 node_daemon 以 `bin/node-daemon/BuckyOS Runtime.app`（Bundle ID / launchd label `com.buckyos.runtime`）交付，已安装产物只有 bundle 内这一份 `Contents/MacOS/node_daemon`：

- 模板：`src/kernel/node_daemon/macos/`（`Info.plist`、图标）；版本取 `src/VERSION`。
- 组装：`src/tools/macos_runtime_bundle.py assemble` 把 Cargo 输出的裸 `node_daemon` 移进 bundle，签名后校验；`buckyos-build.py`、`make_local_pkg.py` prepare、CI（`build-macos.yml`、`dev-tests.yml`）在 `buckyos-build` 之后调用。直接运行 devkit 的 `buckyos-build` 需要手动补这一步。
- 签名：`BUCKYOS_MACOS_CODESIGN_IDENTITY` 指定正式身份（启用 hardened runtime 与 timestamp）；未设置时为 ad-hoc 签名，只用于开发，其 TCC 授权会随每次构建失效，不能证明发布版的授权连续性。签名之后不再改动 bundle。
- 打包：pkgbuild 的 component plist 把所有 bundle 固定为 `BundleIsRelocatable=false`，避免 Installer 把 Runtime 或 App 装到 LaunchServices 记录的其他副本位置。`verify-pkg` 检查唯一 Runtime 主程序、bundle 签名、不可重定位，以及 pre/postinstall 走用户 LaunchAgent。
- preinstall：停止绑定用户的 `com.buckyos.runtime` 与本实例进程（`bin/stop_osx.sh`），清除旧版 `/Library/LaunchDaemons/buckyos.service.plist`；Docker/OrbStack 在 Runtime 用户上下文检查，未就绪只提示，不阻断安装。
- postinstall：校验 bundle 签名，按 `etc/desktop_runtime.json` 或当前 GUI 用户执行 `node_daemon service register --user <u>`（root 交给该用户，bundle 保持 root 所有），用户已登录 GUI 时 `service start`；没有 GUI 用户时安装照常成功，首次打开 BuckyOS Runtime（或托盘“完成设置”）时经管理员授权完成绑定。
- 本地管理：`"<root>/bin/node-daemon/BuckyOS Runtime.app/Contents/MacOS/node_daemon" service status|start|stop|restart|autostart on|off|unregister`；卸载用 `uv run src/uninstall.py`（先停实例，再注销登录项与绑定）。

## Platform Commands

macOS:

```bash
uv run ./src/publish/make_local_osx_pkg.py build-pkg aarch64 0.5.1+build260115 \
  --app-publish-dir /opt/buckyosci \
  --out-dir ./publish
```

Windows:

```powershell
python .\src\publish\make_local_win_installer.py build-pkg amd64 0.5.1+build260115 `
  --app-publish-dir C:\opt\buckyosci `
  --out-dir .\publish
```

Linux:

```bash
uv run ./src/publish/make_local_deb.py build-pkg amd64 0.5.1+build260115 \
  --app-publish-dir /opt/buckyosci \
  --out-dir ./publish
```

Fedora RPM:

```bash
uv run ./src/publish/make_local_rpm.py build-pkg amd64 0.5.1+build260115 \
  --app-publish-dir /opt/buckyosci \
  --out-dir ./publish
```

## Unified Entry

仓库根目录只保留统一入口：

- `make_local_pkg.py`

它会先执行 Common Flow 里的通用准备步骤：

- 清理并重建 `BUCKYOS_BUILD_ROOT`
- 按 `publish.*_pkg.apps.*.deps` 构建外部 `buckyos_project` 依赖，并安装到对应组件 staging 目录
- 构建并安装 `buckyos`（其中包含系统 Tool `bin/buckyos`）
- 在 `src/` 目录执行 `deno task make_config release --rootfs <staged_rootfs>`
- 按约定在 BuckyOS 同层目录查找并构建桌面端项目：
  `../cyfs-gateway`、`../BuckyOSApp`
- 桌面端构建产物优先从用户环境变量 `RUST_BUILD` 读取；若未设置，则读取 `~/.cargo/config.toml` 的 `[build].target-dir`；再回退到 `/tmp/rust_build`
- 如需覆盖默认约定，可用 `--desktop-app` 直接指定桌面端产物

然后再自动识别当前 OS/Arch，并转调 `src/publish/` 下对应的子脚本：

- macOS -> `make_local_osx_pkg.py`
- Windows -> `make_local_win_installer.py`
- Linux -> `make_local_deb.py` 或 `make_local_rpm.py`

常用命令：

```bash
uv run ./make_local_pkg.py prepare-root
uv run ./make_local_pkg.py build-pkg
uv run ./make_local_pkg.py build-pkg --format rpm
uv run ./make_local_pkg.py build-pkg 0.6.0+build260317 --build-root /opt/buckyosci --out-dir ./publish
uv run ./make_local_pkg.py show-manifest
uv run ./make_local_pkg.py show-manifest --out /tmp/buckyos-pkg-manifest.json
uv run ./make_local_pkg.py verify-pkg ./publish/<pkg-file>
uv run ./make_local_pkg.py verify-pkg ./publish/<pkg-file>.rpm --format rpm
uv run ./make_local_pkg.py show-target
```

说明：

- `build-pkg` 不传版本号时，默认使用 `src/VERSION + buildYYMMDD`
- `build-pkg` 默认先执行 `prepare-root`；如果 staging 已经准备好，可加 `--skip-prepare`
- 默认会在 `../BuckyOSApp` 执行 `pnpm run tauri build`，并从 Rust 构建目录 `release/bundle/...` 复制产物到 `BUCKYOS_BUILD_ROOT/BuckyOSApp/`
- 如果不想自动构建桌面端，可加 `--skip-desktop-app-build`
- 如果桌面端产物不在约定目录，可用 `--desktop-app` 明确指定
- `verify-pkg` 会统一转调当前平台对应的子脚本
- Linux 默认构建 DEB；需要 Fedora RPM 时传 `--format rpm`
- `sync-scripts` 会在 macOS / Windows 上同步安装脚本模板
- `show-manifest` 会输出按安装项目分组的通用 JSON 清单，其中 `module_items` / `data_items` / `clean_items` 都带 `target_dir_name`
- manifest 里的 `source_rootfs` / `source_path` 语义是 staging 后的 `BUCKYOS_BUILD_ROOT` 路径；原工程来源只保留在 `project_source_*` 元数据里
