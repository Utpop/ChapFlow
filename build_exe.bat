@echo off
rem 本文件用 GBK（cp936）保存：cmd.exe 按系统 ANSI 代码页解析批处理
rem ChapFlow 打包脚本：生成 dist\ChapFlow.exe（单文件，无控制台窗口）
rem
rem   首次使用：py -3 -m pip install pyinstaller
rem   打包：    build_exe.bat
rem   验证：    set QT_QPA_PLATFORM=offscreen && dist\ChapFlow.exe --selftest

setlocal
cd /d "%~dp0"

where py >nul 2>nul || (echo 未找到 py 启动器，请先安装 Python 3.9+ & exit /b 1)

py -3 -c "import PyInstaller" >nul 2>nul || (
    echo 正在安装 PyInstaller...
    py -3 -m pip install pyinstaller || exit /b 1
)

echo 正在清理旧产物...
if exist build rmdir /s /q build
if exist dist  rmdir /s /q dist

echo 正在打包...
py -3 -m PyInstaller ^
    --noconfirm --clean ^
    --onefile --windowed ^
    --name ChapFlow ^
    --icon assets\chapflow.ico ^
    --version-file assets\version_info.txt ^
    --add-data "assets;assets" ^
    --paths src ^
    --hidden-import PySide6.QtSvg ^
    --collect-submodules ui ^
    --exclude-module tkinter ^
    --exclude-module pytest ^
    src\chapflow_gui.py
if errorlevel 1 (echo 打包失败 & exit /b 1)

echo.
echo 验证打包产物（资源是否随包分发）...
set QT_QPA_PLATFORM=offscreen
dist\ChapFlow.exe --selftest
if errorlevel 1 (echo 自检失败 & exit /b 1)

echo.
echo 完成：dist\ChapFlow.exe
endlocal
