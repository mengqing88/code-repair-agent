"""接收并校验缺陷修复任务输入，以 JSON 输出元数据和尚未生成的产物占位说明。"""

# 将类型注解保存为字符串，避免定义函数时立即计算注解表达式。
from __future__ import annotations

# argparse：解析命令行；json：序列化结果；platform：查询 Python 版本。
import argparse
import json
import platform
# sys：访问当前进程的标准输出、标准错误等运行环境信息。
import sys
# Path：用对象表示路径；Sequence：标注有顺序的参数序列，例如列表或元组。
from pathlib import Path
from typing import Sequence

# 类型注解中的 str 表示字符串，int 表示整数，dict 表示字典；
# object 允许字典的值具有不同类型，None 表示没有提供可选值。


def make_parser() -> argparse.ArgumentParser:
    """定义 CLI 参数并返回解析器；代码来源须三选一，错误报告来源须二选一。"""
    # parser：保存全部参数规则，并负责解析参数、输出帮助和参数错误。
    # argparse.ArgumentParser() 创建命令行解析器；description 是 --help 显示的程序说明。
    parser = argparse.ArgumentParser(
        description="接收 Python 缺陷修复任务的输入，并显示当前输出占位内容。"
    )
    # source：代码来源互斥组，要求文件、仓库、粘贴代码三者恰好选一个。
    # add_mutually_exclusive_group() 创建互斥组，使组内选项不能同时使用；
    # required=True 表示必须提供组内至少一个选项，两条规则合起来就是三选一。
    source = parser.add_mutually_exclusive_group(required=True)
    # add_argument() 向解析器或互斥组登记一个命令行选项，实际读取输入由 parse_args() 完成。
    # 第一个参数是选项名；这些选项默认各接收一个值，例如 --python-file example.py。
    # type=Path 会调用 Path()，把输入的路径字符串转成路径对象；它本身不检查文件是否存在。
    # help 是 --help 显示的选项说明；解析时选项名里的连字符变成下划线。
    # 此处登记包含 Python 代码的文件选项，扩展名不限；解析后的路径保存在 args.python_file 中。
    source.add_argument("--python-file", type=Path, help="包含 Python 代码的文本文件路径（扩展名不限）")
    # add_argument() 登记仓库选项，将输入转成 Path 对象并保存到 args.repository。
    source.add_argument("--repository", type=Path, help="Python Git 仓库目录")
    # add_argument() 登记代码文本选项；未指定 type，输入按字符串保存在 args.code_text。
    source.add_argument("--code-text", help="直接粘贴的 Python 代码")

    # add_argument() 在解析器上登记版本选项，不加入上面的互斥组，值保存到 args.revision。
    # 此处未设置 required=True，所以可省略；这些选项未提供时的默认值都是 None。
    # 它是否必须提供、能否与其他选项搭配，由后面的 check_source() 校验。
    parser.add_argument(
        "--revision",
        help="仓库的固定完整 commit ID（40 或 64 位十六进制字符）",
    )

    # report：错误报告来源互斥组，要求文件和直接输入文字二者恰好选一个。
    # add_mutually_exclusive_group(required=True) 再建一个独立的互斥组，约束错误来源。
    report = parser.add_mutually_exclusive_group(required=True)
    # add_argument() 登记报告文件选项；type=Path 将值转成路径，保存到 args.error_file。
    report.add_argument("--error-file", type=Path, help="错误报告文件的路径")
    # add_argument() 登记错误文本选项，将输入的字符串保存到 args.error_text。
    report.add_argument("--error-text", help="直接输入的错误描述或失败信息")
    return parser


def read_text_file(path: Path, description: str) -> str:
    """读取非空文本并返回原文；path 是路径，description 是错误提示中的文件称呼。"""
    try:
        # content：读取到的完整原文；utf-8-sig 兼容 UTF-8 并去掉开头的 BOM。
        # Path.read_text() 打开并读取整个文本文件，按 encoding 解码为字符串，随后关闭文件。
        content = path.read_text(encoding="utf-8-sig")
    # error：保存文件访问或编码错误，供下面的提示和异常链使用。
    except (OSError, UnicodeError) as error:
        # 内置异常类 ValueError 表示输入不合法；from error 保留底层失败原因。
        raise ValueError(f"无法读取{description}：{path}（{error}）") from error

    # strip() 是字符串方法，临时去除两端空白用于判空，不修改 content 原文。
    if not content.strip():
        # ValueError 将只有空白的文件报告为无效输入。
        raise ValueError(f"{description}不能为空：{path}")
    return content


def check_source(args: argparse.Namespace) -> dict[str, object]:
    """校验代码来源并返回元数据；args 保存解析后的来源路径、代码文本和 revision。"""
    if args.python_file is not None:
        # path：用户提供的 Python 代码文件路径；args.python_file 是解析 --python-file 后生成的属性。
        path = args.python_file
        # Python 代码可以保存在 .txt 或无扩展名的文件中，因此这里不限制文件后缀。
        # read_text_file() 读取并检查文件非空；这里仅验证输入，不保留返回的源码文本。
        read_text_file(path, "Python 文件")
        if args.revision is not None:
            # ValueError 表示在单文件模式下错误地使用了仓库版本参数。
            raise ValueError("--revision 只能与 --repository 一起使用")
        # resolve() 取得绝对路径；内置 str() 将 Path 转成可放入 JSON 的字符串。
        return {"type": "python_file", "path": str(path.resolve())}

    if args.repository is not None:
        # path：用户提供的 Git 仓库目录路径。
        path = args.repository
        # Path.is_dir() 判断路径是否指向现有目录，返回 True 或 False。
        if not path.is_dir():
            # ValueError 表示路径不存在或不是目录。
            raise ValueError(f"仓库目录不存在或不是目录：{path}")
        # Path 的 / 运算拼接路径；exists() 判断拼出的 .git 路径是否存在，文件和目录均可。
        if not (path / ".git").exists():
            # ValueError 表示缺少 .git 标记；此处没有调用 Git 验证仓库内容。
            raise ValueError(f"目录中没有 .git，无法确认它是 Git 仓库：{path}")
        if args.revision is None:
            # ValueError 表示仓库输入缺少必需的固定版本。
            raise ValueError("使用 --repository 时必须提供固定版本 --revision")
        # 内置 len() 计算 ID 长度；any() 在任意字符非法时返回 True。
        # character：生成器每次从 revision 中取出的一个字符，用于十六进制校验。
        if len(args.revision) not in (40, 64) or any(
            character not in "0123456789abcdefABCDEF" for character in args.revision
        ):
            # ValueError 表示 commit ID 的长度或字符不符合要求。
            raise ValueError("--revision 必须是完整的 40 或 64 位十六进制 commit ID")
        return {
            "type": "git_repository",
            # Path.resolve() 取得绝对路径并解析符号链接；内置 str() 将其转换为字符串。
            "path": str(path.resolve()),
            # str.lower() 返回小写形式的 ID，让输入中的 A～F 统一变成 a～f。
            "revision": args.revision.lower(),
            "revision_check": "仅检查了 ID 格式；尚未检查该 commit 是否存在",
        }

    if args.revision is not None:
        # ValueError 表示粘贴代码模式不接受仓库版本参数。
        raise ValueError("--revision 只能与 --repository 一起使用")
    # 解析器已保证来源三选一；走到这里时 args.code_text 就是输入的代码字符串。
    # str.strip() 返回去除两端空白后的副本；空字符串在 if 判断中视为 False。
    if not args.code_text.strip():
        # ValueError 表示代码文本为空或只有空白。
        raise ValueError("--code-text 不能为空")
    # 内置 len() 统计原始代码的字符数，包括空格和换行；不执行这段代码。
    return {"type": "pasted_code", "character_count": len(args.code_text)}


def check_error_report(args: argparse.Namespace) -> dict[str, object]:
    """校验错误信息并返回元数据；args 保存错误报告文件路径或直接输入的错误文本。"""
    if args.error_file is not None:
        # content：读到的错误报告原文，用于确认非空并统计字符数。
        content = read_text_file(args.error_file, "错误报告")
        return {
            "type": "file",
            # Path.resolve() 取得绝对路径并解析符号链接；内置 str() 将该路径转成字符串。
            "path": str(args.error_file.resolve()),
            # 内置 len() 计算错误报告原文的字符数。
            "character_count": len(content),
        }

    # str.strip() 临时去掉两端空白，用来识别空文本或只有空白的错误描述。
    if not args.error_text.strip():
        # ValueError 表示直接输入的错误信息为空或只有空白。
        raise ValueError("--error-text 不能为空")
    # 内置 len() 统计错误文本的字符数，输出只含数量而不包含原文。
    return {"type": "text", "character_count": len(args.error_text)}


def build_result(args: argparse.Namespace) -> dict[str, object]:
    """接收参数对象 args，汇总校验后的输入和输出占位说明，返回结果字典。"""
    # source：标准化后的代码来源元数据，包含类型、路径或字符数等信息。
    source = check_source(args)
    # error_report：标准化后的错误信息来源元数据。
    error_report = check_error_report(args)

    return {
        "status": "已接收输入；本版本尚未开始复现或修复",
        # platform.python_version() 返回当前 Python 解释器的版本字符串，例如 "3.11.9"。
        "runtime": {"python_version": platform.python_version()},
        "input": {"source": source, "error_report": error_report},
        "outputs": {
            "patch": "【修复补丁尚未生成】",
            "acceptance_report": "【验收报告尚未生成】",
            "vulnerability_fix_report": "【详细漏洞修复报告尚未生成】",
            "failure_reproduction_record": "【失败复现记录尚未生成】",
            "root_cause_evidence": "【根因诊断证明尚未生成】",
        },
    }


def main(argv: Sequence[str] | None = None) -> int:
    """CLI 主流程：解析、校验并打印 JSON；argv 为参数序列，None 时读取真实命令行。"""
    # 内置 hasattr() 检查输出流是否有 reconfigure 属性，兼容不支持它的输出流。
    # sys.stdout 是标准输出流；reconfigure() 将其文本编码设为 UTF-8。
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    # 内置 hasattr() 同样检查标准错误流；sys.stderr 用于输出参数错误等信息。
    # reconfigure(encoding="utf-8") 将标准错误流的文本编码也设置为 UTF-8。
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")

    # parser：当前 CLI 的参数解析器；make_parser() 创建并登记本程序支持的选项。
    parser = make_parser()
    # args：解析后的参数对象；例如 --error-text 对应 args.error_text。
    # parse_args() 按 add_argument() 登记的规则读取参数，返回 argparse.Namespace 对象。
    # argv 为 None 时读取真实命令行（去掉脚本名）；传入列表时解析该列表，方便测试。
    # 互斥规则、必填规则或参数格式出错时，parse_args() 会显示提示并以退出码 2 退出。
    args = parser.parse_args(argv)
    try:
        # result：通过校验后待输出的结果字典。
        result = build_result(args)
    # error：校验过程中抛出的内置 ValueError 异常，保存用户可理解的错误消息。
    except ValueError as error:
        # 内置 str() 提取错误文字；parser.error() 打印使用说明并以退出码 2 退出。
        parser.error(str(error))

    # json.dumps() 将结果字典转换成 JSON 字符串；ensure_ascii=False 让中文保持原样，
    # indent=2 让每层缩进两个空格；内置 print() 将字符串写入标准输出并追加换行。
    print(json.dumps(result, ensure_ascii=False, indent=2))
    # main() 返回整数 0，交给程序入口作为正常完成的进程退出码。
    return 0


# __name__ 是 Python 设置的模块名，只有直接运行本文件时才等于 __main__。
if __name__ == "__main__":
    # 内置异常类 SystemExit 结束进程，并把 main() 的返回值作为退出码。
    raise SystemExit(main())
