# Windows 发布与更新验证

本项目发布 PyInstaller 单文件 Windows EXE。GitHub 提供完整 EXE，Gitee 提供同一文件按顺序切分的四个分片。下载校验、程序替换、自动启动和从原入口再次启动是四个独立的验证项目；上传完成或单元测试通过不能替代后面三个项目。

## 历史更新器审计（2026-10-24）

已确认 v1.0.0、v1.1.0、v1.1.1 的内置更新器含有相同的 Windows 启动缺陷。在本次测试环境中，同时使用 `DETACHED_PROCESS` 与 `CREATE_NO_WINDOW` 启动 PowerShell，会出现退出码为 0、实际未执行脚本的情况。旧程序随即退出，因此下载完成后原 EXE 没有被替换，重新打开仍为旧版。

这一问题从 v1.0.0 已经存在，并非 v1.1.1 新引入。以下审计区分了实际发布包与源码历史；结论不表示所有 Windows 环境过去都会失败。

| 版本/构建 | 实际检查内容 | 更新器结论 |
| --- | --- | --- |
| v0.1.0 | GitHub tag `0b447ef` 的源码；本地未找到与正式资产 SHA-256 相同的 EXE | 源码没有内置自更新；未声称检查过正式 EXE |
| v0.1.1 中文版 | 本地 `dist/压汞数据分析/压汞数据分析.exe` 的 SHA-256 与 GitHub 正式资产一致，已提取冻结 archive | 没有 `mercury_app.updater`、`update_checker`、`version` 模块，不受本次脚本问题影响 |
| v0.1.2 中文版 | 本地 `dist/压汞数据分析.exe` 的 SHA-256 与 GitHub 正式资产一致，已提取冻结 archive；tag 为 `aa7319c` | 没有内置自更新，升级需手动替换 |
| v1.0.0 初始构建 | 本地 `dist/MIP-DragonScience-v1.0.0.exe` 与 `31be25c` 的历史发布清单 SHA-256 一致 | updater 模块对应 `31be25c`，含启动缺陷 |
| v1.0.0 后续构建 | 本地 `dist/release-v1.0.0/MIP-DragonScience-v1.0.0.exe` 与当前 GitHub 正式资产及历史发布清单 SHA-256 一致 | 对应 `ec29fa7`/`bcc1555` 的 updater；增加了分片下载，未修复启动缺陷 |
| v1.1.0 | 本地 `dist/release-v1.1.0/MIP-DragonScience-v1.1.0.exe` 与 GitHub 正式资产 SHA-256 一致，已提取冻结 archive | 含相同缺陷 |
| v1.1.1 | 本地 `dist/release-v1.1.1/MIP-DragonScience-v1.1.1.exe` 与 GitHub 正式资产 SHA-256 一致，已提取冻结 archive | 含相同缺陷 |

上述 v1.x 构建的启动函数 `_launch_replacement_script` 和替换脚本生成函数 `_replacement_script_text` 的规范化字节码指纹相同：均使用冲突的启动 flags、没有助手启动握手，且成功弹窗位于自动重启之前。v1.0.0 后续构建、v1.1.0、v1.1.1 的整个 updater 模块指纹也相同。v0.1.x 英文正式包没有在本次审计中提取检查。

用于识别已检查构建的完整 SHA-256：

| 构建 | SHA-256 |
| --- | --- |
| v0.1.1 中文 | `8EE5A74FF9D39C5CF00D4C3F02147E8602BF6151B58D42C159D8367DC89D59E5` |
| v0.1.2 中文 | `5FBB0ABF4793E9133F99802A365BEBA009C10C68DF43155D30803AF3FB4C5C94` |
| v1.0.0 初始构建 | `4FBDC55EB9F6CE31AC694A114C3E57792AE0DFF9A830FE361D82BE3E218B31B1` |
| v1.0.0 后续构建 | `CC43D529A7E51BB7CBFDD9E83FD288CE667158D404D6F68BE7FD2B7B0BC1D2A2` |
| v1.1.0 | `C99206CD3DF7EDF1401F5684349CEEBB08513A81CC4551722E40BBEE55AC32A1` |
| v1.1.1 | `9E64882BFDAB61A4863202BCF15249288CD0EF8D3C920006D374245D05041277` |

审计时 GitHub 的 v1.0.0 tag 指向 `ec29fa7`，Gitee 的同名 tag 指向 `bcc1555`；两个提交的 updater 相同。因此历史 tag 名或 EXE 文件名不能单独证明包内实现。应以文件 SHA-256、冻结包内版本和代码核对为准。两个命名为 `github-api-verified.exe`、`github-proxy-verified.exe` 的本地 v1.1.0 文件大小不完整且无法读取 PyInstaller archive，没有计入正式包验证。

本机原始证据位于忽略目录 `build/updater-diagnosis/`：`history-audit.json`、`history-*.dis.txt`、`history-github-metadata.json`、`history-earlier-github-metadata.json`。这些不是仓库随附文件；发布记录应另行保存证据，不应依赖该目录永久存在。GitHub 资产元数据来自对应版本的 `/repos/dragonMaLong/mercury-smp-analyzer/releases/tags/<tag>` API。

## 旧版本用户的一次性恢复

v1.0.0、v1.1.0、v1.1.1 下载的新 EXE 不能修复仍在旧 EXE 内运行的更新启动器。出现“更新后仍是旧版”的用户，需先通过明确提供的恢复工具，或完成以下一次性手动替换：

1. 保存需要导出的结果，关闭软件及其其他实例。
2. 根据平时使用的桌面快捷方式或原 EXE，确认真正启动的程序完整路径；快捷方式可通过属性中的“目标”定位。
3. 取得完整新版 EXE 并核对发布的 SHA-256。Gitee 的四个分片必须按 `part01` 至 `part04` 顺序合并并核对整包 SHA-256，单个分片不能运行。
4. 保留旧 EXE 备份，把新版放到原程序目录，使用原来的文件名。这样原快捷方式仍指向更新后的文件。
5. 从原快捷方式或原 EXE 入口打开，确认界面显示新版；关闭后从同一入口再次打开并再次确认。

只在下载目录运行新版，并不会更新另一个目录里的旧 EXE 或其快捷方式。v0.1.x 没有内置更新，也需手动替换。恢复工具及说明必须与修复版同时提供，不能承诺旧版直接点击更新一定能恢复。

## 构建前确认

1. 检查工作区改动、当前版本和 Git 提交，阅读与本次变更有关的历史源码及实际已发布 EXE。更新器变更尤其要覆盖所有仍被用户使用的旧版本。
2. 更新 `mercury_app/version.py` 和 `updates/release-v<版本>.md`。确定最终代码后再打包，已验证后又有代码变化则必须重新构建并重做受影响验证。
3. **只在干净的 pip 虚拟环境中打包，不要用 Anaconda。** Anaconda 的 numpy/scipy 依赖 Intel MKL，PyInstaller 会把约 600 MB 的 MKL DLL 全部打进 EXE（v1.1.2 首版因此为 340 MB，改用干净环境后为 93 MB）。从 Anaconda 的 Python 创建的 venv 也不行。环境要求：
   - 使用非 Anaconda 的 Python 3.12.3（例如 `uv python install 3.12.3` 安装的独立版本），依赖固定为 numpy 1.26.4、scipy 1.13.1、PyQt5 5.15.10、pyqtgraph 0.14.0、openpyxl 3.1.2、PyInstaller 6.12.0、pyinstaller-hooks-contrib 2025.1；测试另需 pytest 7.4.4、pypdf，发布脚本另需 requests、requests-toolbelt。
   - **虚拟环境必须放在纯英文路径下**（例如 `C:\build\venv-mip`）。pip 版 Qt 的插件路径不支持中文，放在本项目目录（含“压汞”）下时 `QApplication` 会卡住不动。打包后的 EXE 运行在 `_MEI` 临时目录，不受影响。
   - spec 已排除 `numba`、`llvmlite`（pyqtgraph 会尝试导入，程序不使用）。
   - 打包后检查 `build/<workpath>/压汞数据分析/PKG-00.toc` 中不含 `mkl_`、`anaconda` 字样，并记录 EXE 大小。
4. 执行项目测试，保留测试结果。更新器重点测试为 `tests/test_update_replacement.py` 和 `tests/test_update_parts.py`；前者需要 Windows 和可用的 .NET Framework C# 编译器，跳过测试不算通过真实 Windows 验证。界面测试在同一进程中连续创建大量窗口时会出现 Qt 访问冲突（Anaconda 环境同样如此，与改动无关），因此按测试文件逐个运行。
5. 更换数值库或其版本时，用同一批 SMP 文件对比新旧构建的计算结果和 `--csv` 导出，确认汇总、孔结构、Mayer-Stowe 结果一致，差异只在舍入量级。

以 v1.1.2 为例，独立保留构建输出，避免覆盖用户正在使用的程序：

```powershell
uv python install 3.12.3
uv venv --python 3.12.3 --managed-python C:\build\venv-mip
uv pip install --python C:\build\venv-mip\Scripts\python.exe numpy==1.26.4 scipy==1.13.1 PyQt5==5.15.10 pyqtgraph==0.14.0 openpyxl==3.1.2 pyinstaller==6.12.0 pyinstaller-hooks-contrib==2025.1 pytest==7.4.4 pypdf requests requests-toolbelt
Get-ChildItem tests\test_*.py | ForEach-Object { C:\build\venv-mip\Scripts\python.exe -m pytest $_.FullName -q }
C:\build\venv-mip\Scripts\python.exe -m PyInstaller --noconfirm --distpath dist/release-v1.1.2 --workpath build/release-v1.1.2 "压汞数据分析.spec"
```

## 发布前必须执行的更新验证

在隔离目录操作 EXE 副本，不替换用户正在运行的文件。不能仅修改 `sys.frozen` 后用普通 Python 进程模拟单文件程序：PyInstaller 的父子进程、文件占用和重启环境需要真实冻结程序验证。

1. **版本与正常启动。** 直接启动最终候选 EXE，确认 GUI 显示目标版本、主要界面可打开，并核对冻结包内版本。记录 EXE 大小和 SHA-256。
2. **实际更新链路。** 使用真实冻结程序启动更新，验证助手已执行并确认 ready 后旧窗口才关闭；助手退出码 0 本身不算成功。检查旧 Python 子进程和单文件父进程退出、原路径文件被替换、新版自动出现且没有成功弹窗阻塞。
3. **磁盘与入口。** 原路径 EXE 的 SHA-256 必须等于下载的新版，备份 SHA-256 必须等于原文件。确认新版进程路径就是原路径；关闭新版，再通过原 EXE 或原快捷方式启动，界面必须仍显示新版。这一步不能由“新版曾启动过”替代。
4. **旧版恢复。** 对含缺陷的 v1.0.0/v1.1.0/v1.1.1 使用隔离副本验证恢复入口或手动覆盖流程；另用已修复的冻结客户端验证未来自动升级。必须明确哪条路径验证了旧版恢复，哪条路径验证了修复后的自动更新。
5. **失败处理。** 验证助手启动失败、无 ready 即退出、启动超时、下载文件缺失或无法读取、文件占用等情况。启动失败应保留旧窗口并给出日志；替换失败应保留或恢复旧 EXE。包括旧进程在查询后立即退出的竞态，不能把“已经正常退出”当成更新失败。
6. **路径和环境。** 至少覆盖中文、空格、单引号路径；确认 PyInstaller 的旧 `_PYI_*` 环境不会被新版继续使用。实际 GUI 验证必须用最终完整构建，最小冻结探针用于定位机制，不能代替完整程序验证。

本次已完成的机制验证：旧 flags 在普通 Python 和 windowed onefile 探针中均出现 PowerShell 静默退出；去掉 `DETACHED_PROCESS` 后脚本执行。修复后最小 onefile → onefile 在含中文、空格、单引号、`&` 的路径中完成替换和自动启动，原路径及备份 SHA-256 正确，新旧 `_MEI` 目录不同；故意恢复旧 flags 后，新增握手能拒绝假成功。证据为 `build/updater-diagnosis/integration-result.json`、`reject-silent-failure-result.json`。这仅记录已经完成的机制验证，最终 GUI、原入口重开和远程发布验证须在每次发布记录中单独登记结果。

## 两平台发布与更新清单顺序

更新清单 `updates/latest.json` 是客户端发现新版本的入口之一。**先完成最终构建和更新链路验证，再上传资产并校验公开下载，最后更新两平台的清单。**

1. 提交已验证的源码、版本、发布说明和必要文档，将对应提交同步到 GitHub、Gitee，再为本次新版本建立一致的 tag。不要在已经发布后移动旧 tag。记录实际构建对应的提交。
2. 执行 `python scripts/release.py prepare`。该命令从版本目录的最终 EXE 生成带版本名的 EXE、四个分片及候选 `dist/release-v<版本>/latest.json`。核对整包、分片大小和 SHA-256，以及本地分片合并后的整包 SHA-256。此时不要覆盖仓库的 `updates/latest.json`。若现有同版本资产与最终构建不同，脚本会拒绝覆盖；应先辨明是否已经发布，不能忽略冲突继续上传。
3. 分别执行 `github-upload`、`gitee-upload` 上传候选资产。GitHub 使用草稿 Release，Gitee Release/附件可能已公开。上传返回成功、同名附件存在、API 返回的大小或 digest，只是中间证据。
4. 对远程 Gitee 四个分片完整下载，逐个核对大小和 SHA-256，再按顺序合并并核对整包 SHA-256。GitHub 草稿完整 EXE 也应先核对服务器资产信息；必要时通过认证下载验证。网络传输受限时可用 `.github/workflows/mirror-release.yml` 从 Gitee 下载、校验、合并后上传 GitHub；保存 workflow 的实际校验结果。
5. 执行 `github-publish`，使 GitHub 公开下载链接可用。注意该命令同时设置 GitHub 最新 Release，客户端的 Release API 回退可能已经发现新版，因此此前必须完成本地验证和旧版恢复说明。
6. 执行 `python scripts/release.py verify`，完整读取 GitHub 公开 EXE 和 Gitee 四个分片并核对大小、SHA-256。远程合并结果也必须等于本地最终 EXE。若使用云端验证，GitHub 公开下载需另运行 workflow 的 `verify_only=true`；上传流程不能代替公开下载验证。部分下载、HTML 错误页、代理截断文件均不算成功。
7. 两平台全部通过后，才把候选清单复制为 `updates/latest.json`，提交并同步到两平台。分别重新读取两平台的 raw 清单，检查可解析、版本正确、Release/资产 URL 正确、分片顺序/大小/SHA-256 正确、整包 SHA-256 一致。
8. 从客户端实际使用的清单入口检查新版本。记录新用户直接启动、旧用户一次性恢复、修复后自动更新及再次打开原入口各自的结果。对外宣布完成前，应给出真实发布链接、最终文件 SHA-256 与验证状态；仍缺少的验证应明确说明。

发布记录至少保留：构建提交、工具版本、最终 EXE 大小与 SHA-256、各分片 SHA-256、真实 GUI/替换/原入口重开结果、失败场景结果、两平台公开下载校验结果和最终清单内容。日志不得包含访问令牌或凭据。
