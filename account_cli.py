"""Operator bootstrap: create an account and explicitly assign legacy Web data offline."""
import argparse
import os
from pathlib import Path
import secrets

from recovery_store import ServiceLock
from user_accounts import UserAccounts


def bootstrap(root, username, *, migrate_legacy=False):
    root = Path(root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    lock = ServiceLock(root)
    lock.acquire()
    try:
        accounts = UserAccounts(root)
        if migrate_legacy and accounts.legacy_owner():
            raise ValueError("历史数据已绑定账号，不能重复分配；已有账号密码不会被重置")
        password = secrets.token_urlsafe(18)
        user = accounts.register(username, password)
        if migrate_legacy:
            accounts.bind_legacy(user["id"])
        return user, password
    finally:
        lock.release()


def main():
    parser = argparse.ArgumentParser(description="离线创建账号；服务停止后才允许执行")
    parser.add_argument("--data-dir", type=Path, default=Path(os.environ.get("REPAIR_DATA_DIR", Path(__file__).parent / "work" / "web")))
    parser.add_argument("--username", required=True)
    parser.add_argument("--migrate-legacy", action="store_true", help="将旧任务和原共享经验库绑定给该账号")
    args = parser.parse_args()
    user, password = bootstrap(args.data_dir, args.username, migrate_legacy=args.migrate_legacy)
    print(f"username: {user['username']}\ninitial password: {password}")


if __name__ == "__main__":
    main()
