import errno
import json
from pathlib import Path
import webbrowser

from application import Application
from server import Server
from store import Store


def main():
    root = Path(__file__).parent
    store = Store(root / "data/workbench.db")
    if not store.users():
        raise SystemExit("请先运行 setup_app.py 初始化独立演示库。")
    for port in range(4180, 4191):
        origin = f"http://127.0.0.1:{port}"
        try:
            server = Server(("127.0.0.1", port), Application(store, origin))
        except OSError as error:
            if error.errno == errno.EADDRINUSE:
                continue
            raise SystemExit("环境未允许监听本机端口，服务没有启动。请在具有本地网络权限的终端运行。") from error
        print(json.dumps({"status": "listening", "url": origin, "mode": "offline_keyword"}), flush=True)
        print("登录信息仅保存在本机私有文件：", root / "private/登录信息.json", flush=True)
        webbrowser.open(origin)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            server.server_close()
        return
    raise SystemExit("4180-4190 端口均已占用；未停止其他服务。")


if __name__ == "__main__":
    main()
