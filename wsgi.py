"""waitress / flask 开发服务器入口。"""

import os

from app import create_app

app = create_app()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8000"))
    if os.environ.get("FLASK_DEV") == "1":
        app.run(host="0.0.0.0", port=port)
    else:
        from waitress import serve
        serve(app, host="0.0.0.0", port=port)
