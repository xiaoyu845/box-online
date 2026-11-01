"""Box Online — 示例服多号协议挂在线（桌面面板）

依赖：Python 3.10+、PySide6、openpyxl
进服：需先准备并启动「进服对接层」（本版本不含，留作后续开发）

启动：
  py -3 -m box_online
"""

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from box_online.ui_main import run_app

if __name__ == "__main__":
    run_app()
