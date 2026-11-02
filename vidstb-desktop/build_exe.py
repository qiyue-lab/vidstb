"""把 server.py 打包成一个 exe（双击即用，不用装 Python）。

用法：

    python build_exe.py

打包用 PyInstaller，需要先装一次（只装到 D 盘，不进系统盘）：

    python -m pip install --no-cache-dir --target <仓库>/_tools/pysite pyinstaller
    set PYTHONPATH=<仓库>/_tools/pysite

产物：项目根目录下的「启动 Vidstb 电脑版.exe」（单文件，8~10 MB），
双击就能用，不用进子文件夹找。exe 必须留在项目目录里 —— 配置和
资源数据都按 exe 的位置往上找（也可以设环境变量 VIDSTB_HOME 指定别处）。

几个刻意的取舍：
  * 单文件（--onefile）：就一个 exe，方便拷走 / 发到桌面快捷方式。
    代价是每次启动要把依赖解到 runtime-tmpdir，所以把它指到项目下的
    runtime/pack —— 不往 C:\\Users\\...\\Temp 里塞东西。
  * 保留控制台窗口：能直接看到地址、ffmpeg 信息和报错，Ctrl+C / 关窗口即停。
  * web/ 打进包里，但启动时优先用项目里的 web/，改前端不用重新打包。
    数据（config.json、runtime/）始终用项目目录下的，和 python server.py 同一份。
"""

from __future__ import annotations

import os
import subprocess
import sys

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
NAME = "VidstbDesktop"
# 最终交付的 exe 名字（放在项目根目录，双击即用）
FINAL_NAME = "启动 Vidstb 电脑版.exe"
# 打包过程自己的临时目录（同样避开系统盘）
BUILD_DIR = os.path.join(BASE_DIR, "runtime", "_build")
# 单文件 exe 运行时的解压目录
PACK_DIR = os.path.join(BASE_DIR, "runtime", "pack")


def main() -> int:
    entry = os.path.join(BASE_DIR, "server.py")
    for path in (BUILD_DIR, PACK_DIR):
        os.makedirs(path, exist_ok=True)

    env = dict(os.environ)
    env["TMP"] = BUILD_DIR
    env["TEMP"] = BUILD_DIR
    env["PYTHONPATH"] = os.pathsep.join(
        [BASE_DIR] + ([env["PYTHONPATH"]] if env.get("PYTHONPATH") else []))

    command = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm",
        "--onefile",
        "--console",
        "--name", NAME,
        "--distpath", BASE_DIR,
        "--workpath", BUILD_DIR,
        "--specpath", BUILD_DIR,
        # 绝对路径：PyInstaller 会按 spec 所在目录解析相对路径，容易找错
        "--add-data", f"{os.path.join(BASE_DIR, 'web')}{os.pathsep}web",
        "--runtime-tmpdir", PACK_DIR,
        entry,
    ]
    print("打包命令：\n  " + " ".join(command) + "\n")
    result = subprocess.run(command, cwd=BASE_DIR, env=env)
    if result.returncode != 0:
        print("\n打包失败。如果提示找不到 PyInstaller：")
        print("  python -m pip install --no-cache-dir --target <仓库>/_tools/pysite pyinstaller")
        print("  set PYTHONPATH=<仓库>/_tools/pysite")
        return result.returncode

    # 打包出来叫 VidstbDesktop.exe，换成中文名，双击更好认
    exe = os.path.join(BASE_DIR, NAME + ".exe")
    final = os.path.join(BASE_DIR, FINAL_NAME)
    if os.path.exists(exe):
        if os.path.exists(final):
            try:
                os.remove(final)
            except OSError:
                # 旧 exe 正在运行：Windows 不让删，但可以改名，
                # 于是把它挪到 .old 再让新包顶上，不打断正在用的人
                backup = final + ".old"
                try:
                    if os.path.exists(backup):
                        os.remove(backup)
                    os.rename(final, backup)
                    print(f"\n旧版还在运行，先改名留着：{os.path.basename(backup)}")
                    print("关掉那个黑色窗口之后，可以手动删掉这个 .old 文件。")
                except OSError as err:
                    print(f"\n替换失败：旧 exe 占着不让动（{err}）")
                    print(f"新包在这里：{exe}")
                    return 1
        os.replace(exe, final)
    if not os.path.exists(final):
        print("\n打包完成但没找到 exe：", exe)
        return 1
    size = os.path.getsize(final) / 1024 / 1024
    print(f"\n完成：{final}（{size:.1f} MB）")
    print("双击即可启动，会自动打开浏览器；关闭黑色窗口就是停止服务。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
