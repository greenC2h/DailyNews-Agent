"""DailyNews 运维命令。"""
import argparse
from app.services.rag import init_collection

def main() -> None:
    """执行显式 Milvus collection 初始化。"""
    parser = argparse.ArgumentParser(); parser.add_argument("command", choices=["init-milvus"]); args = parser.parse_args()
    if args.command == "init-milvus": print(init_collection())

if __name__ == "__main__": main()
