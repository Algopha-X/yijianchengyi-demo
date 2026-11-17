#!/usr/bin/env python3
"""Create an administrator with an interactive password; no default credentials."""

import argparse
import getpass
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))


def main():
    from app.config import load_settings
    from app.errors import ApiError
    from app.services import accounts
    from app.services.studio import Studio

    parser = argparse.ArgumentParser(description="本地创建管理员，不通过HTTP开放自助提权")
    parser.add_argument("--address", help="管理员邮箱或手机号；未提供时交互输入")
    parser.add_argument("--nickname", default="系统管理员", help="管理员显示名称")
    args = parser.parse_args()
    address = args.address or input("管理员邮箱或手机号：").strip()
    password = getpass.getpass("管理员密码（8至128字符，不会回显）：")
    repeated = getpass.getpass("再次输入密码：")
    if password != repeated:
        parser.error("两次输入的密码不一致，未创建任何账号")
    try:
        service = Studio(load_settings())
        service.db.initialize()
        accounts.initialize(service.db)
        account = accounts.create_admin(service, address, password, args.nickname)
    except (ApiError, ValueError) as error:
        print(f"创建失败：{error}", file=sys.stderr)
        return 1
    print(f"管理员已创建：{account['address']}。请在应用登录；此命令不会输出密码或会话令牌。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
