import argparse
from contextlib import contextmanager
import os
import re
import sys
import textwrap
import time

# --- 配置区 ---
model_name = "large-v3"
SRT_LINE_WIDTH = 42
SRT_MAX_DURATION = 6.0
SRT_TARGET_MIN_DURATION = 2.0
SRT_PAUSE = 0.5
# 支持的文件后缀 (可以根据需要添加 mp4, mkv, mov 等)
SUPPORTED_EXTENSIONS = ('.mp3', '.wav', '.m4a', '.mp4', '.mov', '.flac', '.aac')
# -----------------

# 这些缩写中的句点通常不是句子结束符。
ENGLISH_ABBREVIATIONS = {
    "mr.", "mrs.", "ms.", "dr.", "prof.", "sr.", "jr.", "st.",
    "vs.", "e.g.", "i.e.", "a.m.", "p.m.", "no.", "fig.",
}

def format_timestamp(seconds: float) -> str:
    """将秒数转换为 SRT 时间码格式"""
    assert seconds >= 0, "non-negative timestamp expected"
    total_milliseconds = round(seconds * 1000.0)
    hours, remainder = divmod(total_milliseconds, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    whole_seconds, milliseconds = divmod(remainder, 1_000)
    return f"{hours:02d}:{minutes:02d}:{whole_seconds:02d},{milliseconds:03d}"


def _is_abbreviation(text: str, punctuation_index: int) -> bool:
    """判断当前句点是否属于常见英文缩写。"""
    prefix = text[:punctuation_index + 1]
    token = prefix.rsplit(maxsplit=1)[-1].strip('"\'(“‘[').lower()

    if token in ENGLISH_ABBREVIATIONS:
        return True

    # 保留人名首字母和 U.S. 这类连续首字母缩写。
    return bool(
        re.fullmatch(r"[a-z]\.", token)
        or re.fullmatch(r"(?:[a-z]\.){2,}", token)
    )


def split_english_sentences(text: str) -> list[str]:
    """将 Whisper 片段合并后的文本按完整英文句子切分。"""
    normalized = " ".join(text.split())
    if not normalized:
        return []

    sentences = []
    sentence_start = 0
    sentence_end_pattern = re.compile(r'[.!?]+["\'”’)\]]*(?=\s|$)')

    for match in sentence_end_pattern.finditer(normalized):
        if normalized[match.start()] == "." and _is_abbreviation(normalized, match.start()):
            continue

        sentence = normalized[sentence_start:match.end()].strip()
        if sentence:
            sentences.append(sentence)
        sentence_start = match.end()

    remainder = normalized[sentence_start:].strip()
    if remainder:
        sentences.append(remainder)

    return sentences

@contextmanager
def menu_key_reader():
    """提供跨平台按键读取器，并在退出时恢复 POSIX 终端设置。"""
    if os.name == "nt":
        import msvcrt

        def read_key():
            key = msvcrt.getwch()
            if key in ("\x00", "\xe0"):
                return {"H": "previous", "K": "previous",
                        "P": "next", "M": "next"}.get(msvcrt.getwch(), "")
            return key

        yield read_key
    else:
        import select
        import termios
        import tty

        fd = sys.stdin.fileno()
        original_settings = termios.tcgetattr(fd)

        def read_key():
            key = os.read(fd, 1)
            if not key:
                raise KeyboardInterrupt
            if key != b"\x1b":
                return key.decode("ascii", errors="ignore")
            # 单独 Esc 是取消；方向键发送 ESC [ A 等转义序列。
            if not select.select([fd], [], [], 0.15)[0]:
                return "\x1b"
            prefix = os.read(fd, 1)
            if prefix not in (b"[", b"O"):
                return ""
            for _ in range(16):
                if not select.select([fd], [], [], 0.15)[0]:
                    return ""
                suffix = os.read(fd, 1)
                if not suffix:
                    raise KeyboardInterrupt
                if b"@" <= suffix <= b"~":
                    return {b"A": "previous", b"D": "previous",
                            b"B": "next", b"C": "next"}.get(suffix, "")
            return ""

        try:
            tty.setcbreak(fd)
            yield read_key
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, original_settings)


def select_output_format():
    """Windows / Linux / macOS 方向键菜单，无需额外依赖。"""
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        raise ValueError("交互菜单需要交互式终端；非交互运行请指定 --format srt、txt 或 both")

    options = [("srt", "SRT 字幕"), ("txt", "TXT 阅读文本"), ("both", "SRT + TXT（只识别一次）")]
    selected = 0
    print("请选择输出格式（方向键切换，回车确认，Esc 取消）：")
    with menu_key_reader() as read_key:
        while True:
            print("\r" + f"  < {options[selected][1]} >".ljust(70), end="", flush=True)
            key = read_key()
            if key == "previous":
                selected = (selected - 1) % len(options)
            elif key == "next":
                selected = (selected + 1) % len(options)
            elif key in ("\r", "\n"):
                print()
                return options[selected][0]
            elif key in ("\x1b", "\x03"):
                print()
                raise KeyboardInterrupt


def subtitle_text(words):
    # 保留模型返回的前导空格，让缩写、标点和连字符保持原样。
    return " ".join("".join(word.word for word in words).split())


def subtitle_lines(words):
    return textwrap.wrap(subtitle_text(words), width=SRT_LINE_WIDTH,
                         break_long_words=False, break_on_hyphens=False)


def subtitle_boundary(words):
    text = subtitle_text(words)
    match = re.search(r'[.!?,;:]["\'”’)\]]*$', text)
    return bool(match and not (
        text[match.start()] == "." and _is_abbreviation(text, match.start())
    ))


def build_subtitles(segments):
    """按词级时间、停顿、标点及两行容量切分，不平均分配时间。"""
    words = []
    for segment in segments:
        if not segment.text.strip():
            continue
        if not segment.words:
            raise ValueError("SRT 缺少单词时间戳，无法准确切分字幕；请确认 word_timestamps=True")
        words.extend(word for word in segment.words if word.word)

    cues = []
    start = 0
    while start < len(words):
        end = start
        preferred = None
        while end < len(words):
            candidate = words[start:end + 1]
            duration = candidate[-1].end - candidate[0].start
            if end > start and (duration > SRT_MAX_DURATION or len(subtitle_lines(candidate)) > 2):
                if preferred is not None:
                    end = preferred
                break
            end += 1
            pause = end < len(words) and words[end].start - words[end - 1].end >= SRT_PAUSE
            if pause:
                break
            if duration >= SRT_TARGET_MIN_DURATION and subtitle_boundary(candidate):
                preferred = end
                if re.search(r'[.!?]["\'”’)\]]*$', subtitle_text(candidate)):
                    break
        chunk = words[start:end]
        # 模型偶尔给出零时长或重叠的词；保证 SRT 时间码严格递增且不重叠。
        begin_ms = max(0, round(chunk[0].start * 1000), cues[-1][1] if cues else 0)
        end_ms = max(begin_ms + 1, round(chunk[-1].end * 1000))
        cues.append((begin_ms, end_ms, "\n".join(subtitle_lines(chunk))))
        start = end
    return cues


def write_srt(segments, output_path):
    cues = build_subtitles(segments)
    with open(output_path, "w", encoding="utf-8") as srt_file:
        for i, (start_ms, end_ms, text) in enumerate(cues, start=1):
            start_time = format_timestamp(start_ms / 1000)
            end_time = format_timestamp(end_ms / 1000)
            srt_file.write(f"{i}\n")
            srt_file.write(f"{start_time} --> {end_time}\n")
            srt_file.write(f"{text}\n\n")

def write_txt(segments, output_path):
    # Whisper segment 是模型的时间分段，不是语法上的完整句子。
    # 先合并所有分段，再按句末标点换行，避免一句话被拆成多行。
    full_text = " ".join(segment.text.strip() for segment in segments)
    sentences = split_english_sentences(full_text)

    with open(output_path, "w", encoding="utf-8") as txt_file:
        for sentence in sentences:
            txt_file.write(sentence + "\n")


def process_single_file(model, input_file, overwrite=False, batch_size=8, beam_size=5, output_format="srt"):
    """处理单个文件的核心逻辑，需要传入已加载的模型"""
    base_filename = os.path.splitext(input_file)[0]
    formats = ("txt", "srt") if output_format == "both" else (output_format,)
    outputs = []
    for fmt in formats:
        path = f"{base_filename}.{fmt}"
        if os.path.exists(path) and not overwrite:
            print(f"⏩ 跳过 (文件已存在): {os.path.basename(path)}")
        else:
            outputs.append((fmt, path))
    if not outputs:
        return

    print(f"▶ 正在处理: {os.path.basename(input_file)}")
    start_time = time.time()
    
    try:
        transcribe_options = {
            "language": "en",
            "beam_size": beam_size,
            "vad_filter": True,
            "vad_parameters": {"min_silence_duration_ms": 500},
            "condition_on_previous_text": False,
            "word_timestamps": True,
        }

        if batch_size > 0:
            # 批量处理同一音频中的多个片段，让高性能 GPU 同时做更多工作。
            transcribe_options["batch_size"] = batch_size
        else:
            # faster-whisper 1.2.0 的批量路径不应用此选项，仅兼容模式支持。
            transcribe_options["hallucination_silence_threshold"] = 2.0

        segments, info = model.transcribe(input_file, **transcribe_options)
        
        # 转换为列表以触发生成器执行
        segment_list = list(segments)
        
        for fmt, output_file_path in outputs:
            if fmt == "srt":
                write_srt(segment_list, output_file_path)
            else:
                write_txt(segment_list, output_file_path)
            
        elapsed = time.time() - start_time
        names = ", ".join(os.path.basename(path) for _, path in outputs)
        print(f"✅ 完成 [{info.language}]: {names} (耗时: {elapsed:.1f}s)")
        
    except Exception as e:
        print(f"❌ 处理失败 {input_file}: {e}")

def main(input_path, overwrite=False, batch_size=8, beam_size=5, output_format=None):
    # 1. 获取文件列表
    files_to_process = []
    
    if os.path.isfile(input_path):
        files_to_process.append(input_path)
    elif os.path.isdir(input_path):
        # 遍历文件夹
        print(f"📂 正在扫描文件夹: {input_path}")
        for root, dirs, files in os.walk(input_path):
            for file in files:
                if file.lower().endswith(SUPPORTED_EXTENSIONS):
                    files_to_process.append(os.path.join(root, file))
    else:
        print("❌ 错误：路径不存在")
        return

    if not files_to_process:
        print("⚠️ 未找到支持的音频/视频文件。")
        return

    files_to_process.sort(key=str.lower)

    print(f"📋 发现 {len(files_to_process)} 个文件准备处理。")

    if output_format is None:
        output_format = select_output_format()
    print(f"输出格式：{output_format.upper()}")

    # 2. 加载模型 (只做一次！)
    print(f"🚀 正在加载模型: {model_name} ...")
    try:
        from faster_whisper import BatchedInferencePipeline, WhisperModel

        # 确保你有 GPU，否则把 device 改为 "cpu", compute_type 改为 "int8"
        whisper_model = WhisperModel(
            model_name,
            device="cuda",
            compute_type="float16",
        )
        if batch_size > 0:
            model = BatchedInferencePipeline(model=whisper_model)
            print(f"⚡ 已启用批量推理: batch_size={batch_size}")
        else:
            model = whisper_model
            print("ℹ️ 已使用兼容模式（未启用批量推理）")
    except Exception as e:
        print(f"❌ 模型加载失败: {e}")
        return

    # 3. 循环处理
    for index, file_path in enumerate(files_to_process, 1):
        print(f"\n--- [{index}/{len(files_to_process)}] ---")
        process_single_file(
            model,
            file_path,
            overwrite=overwrite,
            batch_size=batch_size,
            beam_size=beam_size,
            output_format=output_format,
        )

    print("\n🎉 所有任务已完成！")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="使用 faster-whisper 批量转写音频或视频")
    parser.add_argument("input_path", help="音频/视频文件，或包含这些文件的文件夹")
    parser.add_argument("--format", choices=("srt", "txt", "both"), default=None,
                        help="输出格式；不指定时使用方向键和回车选择")
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="覆盖已存在的输出文件",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=8,
        help="批量推理大小（默认 8；可尝试 16；设为 0 使用原来的非批量模式）",
    )
    parser.add_argument(
        "--beam-size",
        type=int,
        default=5,
        help="束搜索大小（默认 5，保持原有质量；设为 1 更快但可能略降准确率）",
    )
    args = parser.parse_args()
    if args.batch_size < 0:
        parser.error("--batch-size 不能小于 0")
    if args.beam_size < 1:
        parser.error("--beam-size 必须至少为 1")
    try:
        main(
            args.input_path,
            overwrite=args.overwrite,
            batch_size=args.batch_size,
            beam_size=args.beam_size,
            output_format=args.format,
        )
    except KeyboardInterrupt:
        print("\n已取消。")
    except ValueError as exc:
        parser.error(str(exc))
